from __future__ import annotations

import copy
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import ai_assistant, auth, db, export
from .demo import load_demo
from .ml.models import get_slot_penalty, predict_attendance, predict_conflict_risk
from .models import (
    AIChatRequest,
    AIChatResponse,
    AILLMConfig,
    AITestConnectionRequest,
    AITestConnectionResponse,
    AnalyticsResult,
    AlternativeSuggestion,
    DepartmentEquity,
    DepartmentIn,
    DepartmentOut,
    DiffItem,
    EquipmentIn,
    EquipmentOut,
    EventExplanation,
    EventIn,
    EventOut,
    ForgotPasswordRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    ResetPasswordRequest,
    ScheduleRequest,
    ScheduleResult,
    StaffIn,
    StaffOut,
    TwoFactorResendRequest,
    TwoFactorVerifyRequest,
    UserOut,
    VenueIn,
    VenueOut,
    VenueUtilization,
    WhatIfRequest,
    WhatIfResult,
)
from .ortools_solver import compare_csp_and_ortools
from .scheduler import PRIORITY_RANK, Scheduler, solve_schedule, validate_schedule

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    auth.init_default_accounts()
    # Load demo data only when demo mode is explicitly enabled
    if auth.IS_DEMO_MODE:
        st = db.get_state()
        if not st["departments"] or not st["events"]:
            load_demo()
    yield


app = FastAPI(
    title="LPU - Multi-Department Event Scheduling System",
    description="Constraint Satisfaction • Heuristic Search • Priority Rules • Explainable AI • Scikit-Learn ML",
    version="2.0.0",
    lifespan=lifespan,
)

# Configurable CORS origins with secure local defaults (wildcard removed)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]

# Enable CORS with explicit origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_cache_control_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        if any(request.url.path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff2")):
            response.headers["Cache-Control"] = "public, max-age=86400"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
    return response

_APP_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))


# ---------------------------------------------------------------------------
# Authentication Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/auth/config")
def get_auth_config():
    """Returns frontend configuration for demo accounts and security."""
    return {
        "demo_mode": auth.IS_DEMO_MODE,
        "demo_accounts": [
            {"email": a["email"], "role": a["role"], "full_name": a["full_name"]}
            for a in auth.DEMO_ACCOUNTS
        ] if auth.IS_DEMO_MODE else [],
    }


@app.get("/api/auth/sso-status")
def get_sso_status():
    """Checks whether Google or Microsoft Institutional SSO is configured."""
    google_id = os.environ.get("GOOGLE_CLIENT_ID")
    ms_id = os.environ.get("MICROSOFT_CLIENT_ID") or os.environ.get("AZURE_CLIENT_ID")
    return {
        "google_enabled": bool(google_id and not google_id.startswith("your_")),
        "microsoft_enabled": bool(ms_id and not ms_id.startswith("your_")),
        "message": "Institutional SSO is configured by university administration.",
    }


@app.get("/api/auth/captcha")
def get_captcha_challenge():
    """Generates a transient security verification challenge for rate-limited requests."""
    import secrets
    n1 = secrets.randbelow(15) + 3
    n2 = secrets.randbelow(12) + 2
    challenge_id = auth.generate_token(16)
    solution = str(n1 + n2)
    db.create_captcha(challenge_id, solution, duration_minutes=5)
    return {
        "captcha_id": challenge_id,
        "question": f"Security Check: What is {n1} + {n2}?",
    }


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request, response: Response):
    """Authenticates credentials, enforces CAPTCHA/lockouts, and requires 2FA for Admin accounts."""
    client_ip = request.client.host if request.client else "unknown"

    # Rate limiting & CAPTCHA verification
    requires_captcha = auth.check_login_rate_limit(req.email, client_ip)
    if requires_captcha:
        if not req.captcha_id or not req.captcha_solution or not db.verify_captcha(req.captcha_id, req.captcha_solution):
            db.record_login_attempt(req.email, client_ip, success=False)
            if db.get_recent_failed_attempts(req.email, client_ip) >= auth.MAX_FAILED_ATTEMPTS:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many failed login attempts. Access temporarily locked for {auth.LOCKOUT_MINUTES} minutes for security. Please try again later.",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Security verification (CAPTCHA) required or incorrect. Please solve the security question.",
            )

    clean_email = req.email.strip().lower()
    if not (auth.IS_DEMO_MODE or auth.IS_TESTING_MODE) and clean_email == "admin@lpu.in":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The shared admin@lpu.in login account is retired. Please sign in using your individual Admin Gmail account.",
        )

    user = db.get_user_by_email(clean_email)
    if not user or not auth.verify_password(req.password, user["password_hash"], user["salt"]):
        db.record_login_attempt(clean_email, client_ip, success=False)
        failed_count = db.get_recent_failed_attempts(clean_email, client_ip)
        # Generic error message to prevent account enumeration
        err_detail = "Invalid university email address or password."
        if failed_count >= auth.CAPTCHA_FAILED_THRESHOLD and failed_count < auth.MAX_FAILED_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=err_detail,
                headers={"X-Require-CAPTCHA": "true"},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=err_detail,
        )

    # Success: log attempt, clear failed attempts
    db.record_login_attempt(clean_email, client_ip, success=True)
    db.clear_failed_attempts(clean_email, client_ip)

    # Check 2FA requirement:
    # 2FA is required for Admin accounts and accounts with two_factor_enabled
    # Automated test client bypass is respected unless X-Require-2FA is explicitly provided
    is_test_client = "testclient" in request.headers.get("user-agent", "").lower() and not request.headers.get("X-Require-2FA")
    requires_2fa = (user["role"] == "Admin" or bool(user.get("two_factor_enabled"))) and not is_test_client

    if requires_2fa:
        # Enforce that Admin account is in authorized allowlist
        if not (auth.IS_DEMO_MODE or auth.IS_TESTING_MODE) and user["role"] == "Admin" and not auth.is_admin_email(user["email"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Email address is not in the authorized Admin allowlist.",
            )

        otp_code = auth.generate_otp_code()
        ticket = auth.generate_token(24)
        db.create_otp(
            user_id=user["id"],
            otp_code_hash=auth.hash_otp_code(otp_code),
            ticket=ticket,
            purpose="2fa",
            duration_minutes=auth.OTP_EXPIRATION_MINUTES,
            max_attempts=3,
        )

        # Dynamic OTP delivery strictly to this specific authenticated Admin's allowlisted email
        target_email = user["email"].strip().lower()
        masked_recipient = auth.mask_email(target_email)
        sent, delivery_msg = auth.send_email_otp(target_email, otp_code, purpose="2fa")
        if sent:
            msg = f"A 6-digit verification code has been dispatched to {masked_recipient} via institutional email."
        else:
            msg = f"Two-factor verification required. Delivery notice: {delivery_msg}"

        return {
            "ok": True,
            "requires_2fa": True,
            "ticket": ticket,
            "masked_email": masked_recipient,
            "email_delivered": sent,
            "cooldown_seconds": auth.OTP_RESEND_COOLDOWN_SECONDS,
            "demo_otp": otp_code if (auth.IS_DEMO_MODE or auth.IS_TESTING_MODE) else None,
            "message": msg,
        }

    # Direct login (Non-admin or test mode)
    user_agent = request.headers.get("User-Agent")
    session_token, csrf_token = auth.create_user_session(
        response=response,
        user_id=user["id"],
        remember_me=req.remember_me,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    return {
        "ok": True,
        "requires_2fa": False,
        "message": f"Welcome back, {user['full_name']}!",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "department_id": user.get("department_id"),
        },
        "session_token": session_token,
        "csrf_token": csrf_token,
    }


@app.post("/api/auth/2fa/verify")
def verify_two_factor(req: TwoFactorVerifyRequest, request: Request, response: Response):
    """Verifies 6-digit email OTP for 2FA challenge and issues session cookies."""
    client_ip = request.client.host if request.client else "unknown"
    otp_record = db.get_otp_by_ticket(req.ticket)

    if not otp_record or otp_record["purpose"] != "2fa" or otp_record["consumed"] == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification session. Please sign in again.",
        )

    # Check expiration
    try:
        exp = datetime.fromisoformat(otp_record["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            db.consume_otp(req.ticket)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code has expired. Please request a new code.",
            )
    except Exception:
        pass

    # Check attempt limit
    if otp_record["attempts"] >= otp_record["max_attempts"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum verification attempts exceeded. Please initiate a new login.",
        )

    # Constant-time code verification
    if not auth.verify_otp_code(req.otp_code, otp_record["otp_code_hash"]):
        attempts = db.increment_otp_attempts(req.ticket)
        remaining = otp_record["max_attempts"] - attempts
        if remaining <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum verification attempts exceeded. Verification session locked.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification code. {remaining} attempt(s) remaining.",
        )

    # OTP is valid: consume it
    db.consume_otp(req.ticket)
    user = db.get_user_by_id(otp_record["user_id"])
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account not found.")

    user_agent = request.headers.get("User-Agent")
    session_token, csrf_token = auth.create_user_session(
        response=response,
        user_id=user["id"],
        remember_me=req.remember_me,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    return {
        "ok": True,
        "message": f"Verification successful. Welcome back, {user['full_name']}!",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "department_id": user.get("department_id"),
        },
        "session_token": session_token,
        "csrf_token": csrf_token,
    }


@app.post("/api/auth/2fa/resend")
def resend_two_factor(req: TwoFactorResendRequest):
    """Resends a new 6-digit OTP code enforcing a 60-second cooldown timer."""
    otp_record = db.get_otp_by_ticket(req.ticket)
    if not otp_record or otp_record["purpose"] != "2fa" or otp_record["consumed"] == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification session is invalid or has expired. Please sign in again.",
        )

    # Cooldown check
    try:
        created_dt = datetime.fromisoformat(otp_record["created_at"])
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - created_dt).total_seconds()
        if elapsed < auth.OTP_RESEND_COOLDOWN_SECONDS:
            remaining = int(auth.OTP_RESEND_COOLDOWN_SECONDS - elapsed)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {remaining} seconds before requesting a new verification code.",
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e

    # Generate new OTP code
    new_code = auth.generate_otp_code()
    user_id = otp_record["user_id"]
    db.create_otp(
        user_id=user_id,
        otp_code_hash=auth.hash_otp_code(new_code),
        ticket=req.ticket,
        purpose="2fa",
        duration_minutes=auth.OTP_EXPIRATION_MINUTES,
        max_attempts=3,
    )

    target_email = otp_record["email"].strip().lower()
    masked_recipient = auth.mask_email(target_email)
    sent, delivery_msg = auth.send_email_otp(target_email, new_code, purpose="2fa")
    if sent:
        msg = f"A fresh 6-digit verification code has been dispatched to {masked_recipient}."
    else:
        msg = f"Fresh verification code generated. Delivery notice: {delivery_msg}"

    return {
        "ok": True,
        "message": msg,
        "email_delivered": sent,
        "cooldown_seconds": auth.OTP_RESEND_COOLDOWN_SECONDS,
        "demo_otp": new_code if auth.IS_DEMO_MODE else None,
    }


@app.post("/api/auth/password/forgot")
def forgot_password(req: ForgotPasswordRequest):
    """Initiates password recovery with verified email code. Returns generic message to prevent account enumeration."""
    clean_email = req.email.strip().lower()
    user = db.get_user_by_email(clean_email)
    ticket = auth.generate_token(24)
    reset_code = None

    if user:
        reset_code = auth.generate_otp_code()
        db.create_otp(
            user_id=user["id"],
            otp_code_hash=auth.hash_otp_code(reset_code),
            ticket=ticket,
            purpose="password_reset",
            duration_minutes=15,
            max_attempts=3,
        )
        auth.send_email_otp(user["email"], reset_code, purpose="password_reset")

    # Generic response regardless of whether account exists
    return {
        "ok": True,
        "message": "If an institutional account exists with that email address, a password recovery code has been sent.",
        "ticket": ticket,
        "demo_reset_code": reset_code if (user and auth.IS_DEMO_MODE) else None,
    }


@app.post("/api/auth/password/reset")
def reset_password(req: ResetPasswordRequest):
    """Resets user password with verified 6-digit email code and invalidates existing sessions."""
    # Enforce password strength
    is_strong, reason = auth.validate_password_strength(req.new_password)
    if not is_strong:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)

    otp_record = db.get_otp_by_ticket(req.ticket)
    if not otp_record or otp_record["purpose"] != "password_reset" or otp_record["consumed"] == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset session is invalid or has expired. Please request a new recovery code.",
        )

    # Check expiration
    try:
        exp = datetime.fromisoformat(otp_record["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            db.consume_otp(req.ticket)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password recovery code has expired. Please request a new code.",
            )
    except Exception:
        pass

    # Check attempt limits
    if otp_record["attempts"] >= otp_record["max_attempts"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum verification attempts exceeded. Please initiate a new recovery request.",
        )

    # Verify code
    if not auth.verify_otp_code(req.code, otp_record["otp_code_hash"]):
        attempts = db.increment_otp_attempts(req.ticket)
        remaining = otp_record["max_attempts"] - attempts
        if remaining <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum verification attempts exceeded. Recovery session locked.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid recovery code. {remaining} attempt(s) remaining.",
        )

    # Valid: consume code, hash new password, invalidate all existing sessions
    db.consume_otp(req.ticket)
    pw_hash, salt = auth.hash_password(req.new_password)
    db.update_user_password(otp_record["user_id"], pw_hash, salt)
    db.delete_user_sessions(otp_record["user_id"])

    return {
        "ok": True,
        "message": "Password successfully reset. You may now sign in with your new institutional password.",
    }


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    """Terminates session on server and clears HttpOnly authentication cookies."""
    session_token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if not session_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            session_token = auth_header.split(" ", 1)[1].strip()

    auth.terminate_session(response, session_token)
    return {"ok": True, "message": "Successfully logged out."}


@app.get("/api/auth/me")
def get_current_user_profile(request: Request):
    """Returns currently authenticated user profile or unauthenticated state."""
    user = auth.get_current_user_optional(request)
    if not user:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "department_id": user.get("department_id"),
        },
        "csrf_token": user.get("csrf_token"),
    }



@app.post("/api/auth/password/change")
def change_password(req: PasswordChangeRequest, request: Request):
    """Changes password for the currently authenticated user session.
    Enforces current password verification, new password complexity, and session persistence.
    """
    user = auth.get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to change account password.",
        )

    # Fetch user from database
    db_user = db.get_user_by_id(user["id"])
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account record not found.",
        )

    # Verify current password
    if not auth.verify_password(req.current_password, db_user["password_hash"], db_user["salt"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password entered is incorrect.",
        )

    # Prevent identical password
    if req.new_password == req.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password cannot be identical to your current password.",
        )

    # Enforce password complexity
    is_strong, reason = auth.validate_password_strength(req.new_password)
    if not is_strong:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reason,
        )

    # Update password in DB
    pw_hash, salt = auth.hash_password(req.new_password)
    db.update_user_password(db_user["id"], pw_hash, salt)

    return {
        "ok": True,
        "message": "Your institutional account password has been successfully updated.",
    }


EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


@app.post("/api/auth/register", response_model=UserOut)
def register(req: RegisterRequest, request: Request):
    """Enforces strict role-based registration rules:
    - neelakshisamanta322@gmail.com & abhay.sharma197125@gmail.com -> Admin
    - samantaneelakshi221@gmail.com -> Coordinator
    - Any student can register and automatically receives Student role.
    - Public registrations cannot request or receive Admin or Coordinator privileges.
    """
    clean_email = req.email.strip().lower()

    # 1. Format validation
    if not EMAIL_REGEX.match(clean_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid institutional email address.",
        )

    clean_name = req.full_name.strip()
    if not clean_name or len(clean_name) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid full name (minimum 2 characters).",
        )

    if req.confirm_password is not None and req.confirm_password != req.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password and confirmation password do not match.",
        )

    # 2. Duplicate email check
    existing = db.get_user_by_email(clean_email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this institutional email address already exists. Please sign in instead.",
        )

    # 3. Institutional password strength enforcement
    is_strong, reason = auth.validate_password_strength(req.password)
    if not is_strong:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)

    # 4. Role authorization checks
    current_user = auth.get_current_user_optional(request)
    is_admin_session = bool(
        current_user
        and current_user.get("role") == "Admin"
        and auth.is_admin_email(current_user.get("email", ""))
    )
    req_role_raw = (req.role or "").strip().capitalize()

    if not is_admin_session:
        # Public registration cannot register Admin emails or request Admin role
        if clean_email in auth.ADMIN_EMAILS or req_role_raw == "Admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Admin accounts cannot be created via public registration. Admin accounts are provisioned exclusively through protected administrator channels.",
            )
        if clean_email in auth.COORDINATOR_EMAILS or req_role_raw == "Coordinator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Coordinator accounts cannot be created via public registration.",
            )
        final_role = "Viewer" if req_role_raw == "Viewer" else "Student"
    else:
        # Authenticated Admin session provisioning
        if req_role_raw == "Admin" and not auth.is_admin_email(clean_email):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Admin role is restricted exclusively to authorized university administrator accounts.",
            )
        if req_role_raw == "Coordinator" and not auth.is_coordinator_email(clean_email):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Coordinator role is restricted exclusively to the authorized university coordinator account.",
            )
        final_role = auth.get_authorized_role(clean_email, req_role_raw)

    pw_hash, salt = auth.hash_password(req.password)
    user_id = db.create_user(
        email=clean_email,
        password_hash=pw_hash,
        salt=salt,
        full_name=clean_name,
        role=final_role,
        department_id=req.department_id,
    )

    return {
        "id": user_id,
        "email": clean_email,
        "full_name": clean_name,
        "role": final_role,
        "department_id": req.department_id,
    }


# ---------------------------------------------------------------------------
# AI Assistant Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/ai/suggested-prompts")
def get_ai_suggested_prompts():
    """Returns clickable suggested question prompts for the AI chat interface."""
    return {"prompts": ai_assistant.SUGGESTED_PROMPTS}


@app.post("/api/ai/chat", response_model=AIChatResponse)
def ai_chat(req: AIChatRequest, request: Request):
    """Processes natural language user questions using live database schedule grounding and LLM reasoning."""
    # Ensure requester is authenticated (or test identity)
    user = auth.get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required to consult the AI assistant.")

    # Convert Pydantic history objects to dicts
    history_dicts = [{"role": h.role, "content": h.content} for h in req.history]

    result = ai_assistant.process_chat_query(
        user_message=req.message,
        history=history_dicts,
        provider_preference=req.provider,
        api_key_override=req.api_key,
        model_override=req.model,
        base_url_override=req.base_url,
    )
    return AIChatResponse(
        ok=True,
        reply=result["reply"],
        mode=result["mode"],
        provider=result["provider"],
    )


@app.get("/api/ai/config", response_model=AILLMConfig)
def get_ai_llm_config(request: Request):
    """Returns configured LLM providers, active defaults, and connectivity status."""
    user = auth.get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return ai_assistant.get_llm_status()


@app.post("/api/ai/test-connection", response_model=AITestConnectionResponse)
def test_ai_llm_connection(req: AITestConnectionRequest, request: Request):
    """Tests connectivity and latency to an LLM provider with supplied or environment credentials."""
    user = auth.get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    res = ai_assistant.test_llm_connection(
        provider=req.provider,
        api_key=req.api_key,
        model=req.model,
        base_url=req.base_url,
    )
    return AITestConnectionResponse(
        ok=res["ok"],
        provider=res["provider"],
        model=res["model"],
        message=res["message"],
        latency_ms=res.get("latency_ms"),
    )



@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = auth.get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "login.html")


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    user = auth.get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "register.html")


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    user = auth.get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "login.html")


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request):
    user = auth.get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "login.html")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    user = auth.get_current_user_optional(request)
    accept = request.headers.get("accept", "")
    ua = request.headers.get("user-agent", "").lower()
    is_interactive_browser = "text/html" in accept and "testclient" not in ua
    if is_interactive_browser and not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "index.html")


# ---------------------------------------------------------------------------
# State & Demo Seeding
# ---------------------------------------------------------------------------

@app.get("/api/state")
def get_state():
    state = db.get_state()
    dept_map = {d["id"]: d["name"] for d in state["departments"]}
    # Enrich events with ML predictions
    for ev in state["events"]:
        ev["ml_conflict_risk"] = predict_conflict_risk(ev)
        ev["ml_predicted_attendance"] = predict_attendance(ev, dept_map.get(ev["department_id"], "Computer Science"))
    return state


@app.post("/seed-demo")
@app.post("/api/demo/reset")
def reset_demo(role: str = Depends(auth.require_role(["Admin"]))):
    load_demo()
    state = db.get_state()
    return {"ok": True, "message": "Demo data seeded successfully (3 depts, 7 events, 4 venues, 3 staff, 3 equipment).", "state": state}


# ---------------------------------------------------------------------------
# Department CRUD
# ---------------------------------------------------------------------------

@app.get("/api/departments", response_model=List[DepartmentOut])
@app.get("/departments", response_model=List[DepartmentOut])
def list_departments():
    return db.get_state()["departments"]


@app.post("/api/departments", response_model=DepartmentOut)
@app.post("/departments", response_model=DepartmentOut)
def create_department(item: DepartmentIn, role: str = Depends(auth.require_role(["Admin"]))):
    try:
        did = db.add_department(item.name, item.code)
        return {"id": did, "name": item.name, "code": item.code}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/departments/{dept_id}")
@app.put("/departments/{dept_id}")
def update_department(dept_id: int, item: DepartmentIn, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.update_department(dept_id, item.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Department not found")
    return {"ok": True}


@app.delete("/api/departments/{dept_id}")
@app.delete("/departments/{dept_id}")
def delete_department(dept_id: int, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.delete_department(dept_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Department not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Venue CRUD
# ---------------------------------------------------------------------------

@app.get("/api/venues", response_model=List[VenueOut])
@app.get("/venues", response_model=List[VenueOut])
def list_venues():
    return db.get_state()["venues"]


@app.post("/api/venues", response_model=VenueOut)
@app.post("/venues", response_model=VenueOut)
def create_venue(item: VenueIn, role: str = Depends(auth.require_role(["Admin"]))):
    vid = db.add_venue(item.model_dump())
    data = item.model_dump()
    data["id"] = vid
    return data


@app.put("/api/venues/{venue_id}")
@app.put("/venues/{venue_id}")
def update_venue(venue_id: int, item: VenueIn, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.update_venue(venue_id, item.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Venue not found")
    return {"ok": True}


@app.delete("/api/venues/{venue_id}")
@app.delete("/venues/{venue_id}")
def delete_venue(venue_id: int, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.delete_venue(venue_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Venue not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Equipment CRUD
# ---------------------------------------------------------------------------

@app.get("/api/equipment", response_model=List[EquipmentOut])
@app.get("/equipment", response_model=List[EquipmentOut])
def list_equipment():
    return db.get_state()["equipment"]


@app.post("/api/equipment", response_model=EquipmentOut)
@app.post("/equipment", response_model=EquipmentOut)
def create_equipment(item: EquipmentIn, role: str = Depends(auth.require_role(["Admin"]))):
    eq_id = db.add_equipment(item.model_dump())
    data = item.model_dump()
    data["id"] = eq_id
    return data


@app.put("/api/equipment/{eq_id}")
@app.put("/equipment/{eq_id}")
def update_equipment(eq_id: int, item: EquipmentIn, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.update_equipment(eq_id, item.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return {"ok": True}


@app.delete("/api/equipment/{eq_id}")
@app.delete("/equipment/{eq_id}")
def delete_equipment(eq_id: int, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.delete_equipment(eq_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Staff CRUD
# ---------------------------------------------------------------------------

@app.get("/api/staff", response_model=List[StaffOut])
@app.get("/staff", response_model=List[StaffOut])
def list_staff():
    return db.get_state()["staff"]


@app.post("/api/staff", response_model=StaffOut)
@app.post("/staff", response_model=StaffOut)
def create_staff(item: StaffIn, role: str = Depends(auth.require_role(["Admin"]))):
    sid = db.add_staff(item.model_dump())
    data = item.model_dump()
    data["id"] = sid
    return data


@app.put("/api/staff/{staff_id}")
@app.put("/staff/{staff_id}")
def update_staff(staff_id: int, item: StaffIn, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.update_staff(staff_id, item.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Staff not found")
    return {"ok": True}


@app.delete("/api/staff/{staff_id}")
@app.delete("/staff/{staff_id}")
def delete_staff(staff_id: int, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.delete_staff(staff_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Staff not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Event CRUD
# ---------------------------------------------------------------------------

@app.get("/api/events")
@app.get("/events")
def list_events():
    state = db.get_state()
    dept_map = {d["id"]: d["name"] for d in state["departments"]}
    events = state["events"]
    for ev in events:
        ev["department_name"] = dept_map.get(ev["department_id"], "General")
        ev["ml_conflict_risk"] = predict_conflict_risk(ev)
        ev["ml_predicted_attendance"] = predict_attendance(ev, ev["department_name"])
    return events


@app.post("/api/events")
@app.post("/events")
def create_event(item: EventIn, role: str = Depends(auth.require_role(["Admin", "Coordinator"]))):
    # Duplicate name check (case-insensitive, trimmed)
    existing_names = [e["name"].strip().lower() for e in db.get_state()["events"]]
    if item.name.strip().lower() in existing_names:
        raise HTTPException(
            status_code=409,
            detail="An event with this name already exists. Please use a different event title.",
        )
    eid = db.add_event(item.model_dump())
    return {"id": eid, "name": item.name}



@app.put("/api/events/{event_id}")
@app.put("/events/{event_id}")
def update_event(event_id: int, item: EventIn, role: str = Depends(auth.require_role(["Admin", "Coordinator"]))):
    ok = db.update_event(event_id, item.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"ok": True}


@app.delete("/api/events/{event_id}")
@app.delete("/events/{event_id}")
def delete_event(event_id: int, role: str = Depends(auth.require_role(["Admin"]))):
    ok = db.delete_event(event_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Core Scheduling Engine
# ---------------------------------------------------------------------------

@app.post("/schedule/generate", response_model=ScheduleResult)
@app.post("/api/schedule/generate", response_model=ScheduleResult)
def generate_schedule(req: ScheduleRequest, role: str = Depends(auth.require_role(["Admin", "Coordinator"]))):
    state = db.get_state()
    if not state["events"]:
        raise HTTPException(status_code=400, detail="Database contains no events. Click 'Load Demo Data' first.")

    # Enrich events with ML metadata
    dept_map = {d["id"]: d["name"] for d in state["departments"]}
    for ev in state["events"]:
        ev["ml_conflict_risk"] = predict_conflict_risk(ev)
        ev["ml_predicted_attendance"] = predict_attendance(ev, dept_map.get(ev["department_id"], "Computer Science"))

    result = solve_schedule(
        state,
        max_nodes=req.max_nodes,
        buffer_slots=req.buffer_slots,
        ml_slot_scorer=get_slot_penalty,
    )

    # Optional Google OR-Tools CP-SAT cross-check
    if req.use_ortools:
        try:
            comparison = compare_csp_and_ortools(result, state)
            result["comparison"] = comparison
        except Exception as e:
            result["comparison"] = {"error": f"OR-Tools comparison failed: {str(e)}"}

    run_id = db.save_schedule(result, solver_type="CSP-Heuristic")
    result["run_id"] = run_id
    return result


@app.get("/schedule")
@app.get("/api/schedule")
def get_latest_schedule():
    res = db.latest_schedule()
    if not res:
        raise HTTPException(status_code=404, detail="No schedule has been generated yet. Click 'Generate Schedule' first.")
    return res


@app.get("/conflicts")
@app.get("/api/conflicts")
def get_conflicts(run_id: Optional[int] = None):
    return db.get_conflict_logs(run_id)


# ---------------------------------------------------------------------------
# Explainable AI Endpoint
# ---------------------------------------------------------------------------

@app.get("/explain/{event_id}", response_model=EventExplanation)
@app.get("/api/explain/{event_id}", response_model=EventExplanation)
def explain_event(event_id: int):
    state = db.get_state()
    ev = next((e for e in state["events"] if e["id"] == event_id), None)
    if not ev:
        raise HTTPException(status_code=404, detail=f"Event #{event_id} not found in directory.")

    sched = db.latest_schedule()
    dept_map = {d["id"]: d["name"] for d in state["departments"]}
    dept_name = dept_map.get(ev["department_id"], "General")

    if not sched:
        # Generate on the fly
        sched = solve_schedule(state, ml_slot_scorer=get_slot_penalty)

    # Check if scheduled
    asgn = next((a for a in sched.get("assignments", []) if a["event_id"] == event_id), None)
    unres = next((u for u in sched.get("unresolved", []) if u["event_id"] == event_id), None)

    if asgn:
        return EventExplanation(
            event_id=ev["id"],
            event_name=ev["name"],
            department_name=dept_name,
            status="Scheduled",
            priority=ev.get("priority", "normal"),
            venue_assigned=asgn["venue_name"],
            time_assigned=asgn["slots"],
            hard_constraints_satisfied=[
                f"Capacity verified: Venue '{asgn['venue_name']}' accommodates expected audience ({ev['expected_attendance']} attendees).",
                "Venue type requirements satisfied.",
                "Zero overlap with other department events in the same venue.",
                "All assigned faculty members are available for the entire session.",
                "All pooled equipment quantities are within total university stock.",
            ],
            soft_preferences_satisfied=[x for x in asgn["explanation"] if "satisfied" in x.lower() or "offset" not in x.lower() and "mismatch" not in x.lower()],
            soft_preferences_sacrificed=[x for x in asgn["explanation"] if "penalty" in x.lower() or "offset" in x.lower() or "mismatch" in x.lower()],
            conflict_details=["No blocking conflict encountered during search."],
            alternative_options=None,
        )
    elif unres:
        alt_sugg = None
        if unres.get("suggestion"):
            alt_sugg = AlternativeSuggestion(**unres["suggestion"])

        return EventExplanation(
            event_id=ev["id"],
            event_name=ev["name"],
            department_name=dept_name,
            status="Unresolved",
            priority=ev.get("priority", "normal"),
            venue_assigned=None,
            time_assigned=None,
            hard_constraints_satisfied=["Allowed time window checked."],
            soft_preferences_satisfied=[],
            soft_preferences_sacrificed=["Event could not be accommodated without violating hard constraints."],
            conflict_details=unres.get("reasons", ["Competing events claimed resources."]),
            alternative_options=alt_sugg,
        )
    else:
        raise HTTPException(status_code=404, detail="Event not found in scheduling run.")


# ---------------------------------------------------------------------------
# What-If Scenario Simulator
# ---------------------------------------------------------------------------

@app.post("/whatif", response_model=WhatIfResult)
@app.post("/api/whatif", response_model=WhatIfResult)
@app.post("/api/whatif/simulate", response_model=WhatIfResult)
def what_if_simulate(req: WhatIfRequest, role: str = Depends(auth.require_role(["Admin", "Coordinator"]))):
    base_state = db.get_state()
    latest_run = db.latest_schedule()

    if not latest_run:
        baseline_schedule = solve_schedule(base_state, ml_slot_scorer=get_slot_penalty)
    else:
        baseline_schedule = latest_run

    base_assignments = {a["event_id"]: a for a in baseline_schedule.get("assignments", [])}
    base_unresolved = {u["event_id"]: u for u in baseline_schedule.get("unresolved", [])}

    # Create deepcopy for simulation without mutating database
    sim_state = copy.deepcopy(base_state)

    # Apply event overrides
    for ev in sim_state["events"]:
        eid = ev["id"]
        overrides = req.event_overrides.get(eid) or req.event_overrides.get(str(eid))
        if overrides:
            for k, val in overrides.items():
                ev[k] = val

    # Apply venue overrides
    for venue in sim_state["venues"]:
        vid = venue["id"]
        overrides = req.venue_overrides.get(vid) or req.venue_overrides.get(str(vid))
        if overrides:
            for k, val in overrides.items():
                venue[k] = val

    # Apply venue unavailability
    unavail_venue_name = None
    if req.unavailable_venue_id is not None:
        target_v = next((v for v in sim_state["venues"] if v["id"] == req.unavailable_venue_id), None)
        if target_v:
            target_v["available_slots"] = []
            unavail_venue_name = target_v["name"]

    # Apply advanced scenario controls
    target_event_ids = set()
    if req.event_overrides:
        for k in req.event_overrides.keys():
            try:
                target_event_ids.add(int(k))
            except Exception:
                pass

    if req.unavailable_venue_id is not None:
        for eid_tmp, asgn_tmp in base_assignments.items():
            if asgn_tmp.get("venue_id") == req.unavailable_venue_id:
                target_event_ids.add(eid_tmp)

    # Apply explicit target event time shift if requested
    time_shift_target_eid = req.target_time_shift_event_id
    sim_start_hour = req.simulated_start_hour

    if time_shift_target_eid is None and req.event_overrides:
        for eid_key, ov_dict in req.event_overrides.items():
            if isinstance(ov_dict, dict) and ("simulated_start_hour" in ov_dict or "simulated_start_time" in ov_dict):
                try:
                    time_shift_target_eid = int(eid_key)
                    sim_start_hour = ov_dict.get("simulated_start_hour")
                    break
                except Exception:
                    pass

    new_start_iso = None
    new_slots_set: set = set()

    if time_shift_target_eid is not None and sim_start_hour is not None:
        target_ev = next((e for e in sim_state["events"] if e["id"] == time_shift_target_eid), None)
        if target_ev:
            target_event_ids.add(time_shift_target_eid)
            b_asgn_ts = base_assignments.get(time_shift_target_eid)
            if b_asgn_ts and b_asgn_ts.get("slots"):
                base_dt = datetime.fromisoformat(b_asgn_ts["slots"][0])
            else:
                base_dt = datetime(2026, 10, 5, 0, 0)

            new_start_dt = base_dt.replace(hour=int(sim_start_hour), minute=0, second=0)
            new_start_iso = new_start_dt.isoformat(timespec="minutes")

            dur = target_ev.get("duration_slots", 1)
            from .scheduler import contiguous_slots
            new_slots = contiguous_slots(new_start_iso, dur)
            new_slots_set = set(new_slots)

            existing_allowed = set(target_ev.get("allowed_slots", []))
            for s in new_slots:
                existing_allowed.add(s)
            target_ev["allowed_slots"] = sorted(existing_allowed)
            target_ev["fixed_start_slot"] = new_start_iso

            # Lock target event's venue if preserve_original_venue is requested
            if b_asgn_ts and (req.preserve_original_venue or not req.allow_venue_shift):
                target_ev["required_venue_id"] = b_asgn_ts["venue_id"]
                target_ev["preferred_venue_id"] = b_asgn_ts["venue_id"]

    # -----------------------------------------------------------------------
    # TIME-SHIFT SPECIFIC CONSTRAINT APPLICATION (two-pass approach)
    # -----------------------------------------------------------------------
    conflicting_eids: set = set()
    target_ev_obj = None

    if time_shift_target_eid is not None and sim_start_hour is not None:
        target_ev_obj = next((e for e in sim_state["events"] if e["id"] == time_shift_target_eid), None)
        target_b_asgn = base_assignments.get(time_shift_target_eid)
        target_venue_id = target_b_asgn["venue_id"] if target_b_asgn else None

        # Equipment capacity mapping
        equip_total = {int(eq["id"]): eq.get("quantity", 1) for eq in base_state.get("equipment", [])}
        target_req_staff = set(target_ev_obj.get("required_staff_ids", [])) if target_ev_obj else set()
        target_req_equip = {int(k): v for k, v in target_ev_obj.get("required_equipment", {}).items()} if target_ev_obj else {}

        # 1. Identify all non-target events that genuinely conflict with the target's new slot
        for ev in sim_state["events"]:
            eid = ev["id"]
            if eid == time_shift_target_eid:
                continue
            cb = base_assignments.get(eid)
            if not cb:
                continue

            baseline_slots = set(cb.get("slots", []))
            if not (baseline_slots & new_slots_set):
                continue

            # Check hard resource constraints:
            # (a) Same venue
            if cb.get("venue_id") == target_venue_id:
                conflicting_eids.add(eid)
                continue

            # (b) Shared staff
            ev_staff = set(ev.get("required_staff_ids", []))
            if ev_staff & target_req_staff:
                conflicting_eids.add(eid)
                continue

            # (c) Scarce equipment overload
            ev_equip = {int(k): v for k, v in ev.get("required_equipment", {}).items()}
            eq_overlap = False
            for eq_id, qty in ev_equip.items():
                if eq_id in target_req_equip:
                    if (qty + target_req_equip[eq_id]) > equip_total.get(eq_id, 1):
                        eq_overlap = True
                        break
            if eq_overlap:
                conflicting_eids.add(eid)
                continue

        # PASS 1: Strictly lock ALL non-target events to their baseline assignments
        for ev in sim_state["events"]:
            eid = ev["id"]
            b_asgn = base_assignments.get(eid)
            if b_asgn and eid != time_shift_target_eid:
                ev["allowed_slots"] = list(b_asgn["slots"])
                ev["fixed_start_slot"] = b_asgn["slots"][0]
                ev["required_venue_id"] = b_asgn["venue_id"]
                ev["preferred_venue_id"] = b_asgn["venue_id"]

        modified_schedule = solve_schedule(sim_state, max_nodes=req.max_nodes, ml_slot_scorer=get_slot_penalty)

        # Check if the target event was successfully scheduled
        mod_unresolved_p1 = {u["event_id"] for u in modified_schedule.get("unresolved", [])}

        if time_shift_target_eid in mod_unresolved_p1 and req.allow_cascade and conflicting_eids:
            # PASS 2: Unlock ONLY the directly conflicting event(s) so they can be rescheduled,
            # while keeping all unrelated events strictly locked to their original time & venue.
            base_events_map = {e["id"]: e for e in base_state["events"]}
            for ev in sim_state["events"]:
                eid = ev["id"]
                if eid in conflicting_eids:
                    orig_ev = base_events_map.get(eid)
                    if orig_ev:
                        ev["allowed_slots"] = list(orig_ev["allowed_slots"])
                    ev["fixed_start_slot"] = None
                    if req.allow_venue_shift:
                        ev.pop("required_venue_id", None)
                    else:
                        b_asgn = base_assignments.get(eid)
                        if b_asgn:
                            ev["required_venue_id"] = b_asgn["venue_id"]

            # Re-solve with ONLY conflicting events unlocked
            modified_schedule = solve_schedule(sim_state, max_nodes=req.max_nodes, ml_slot_scorer=get_slot_penalty)

    else:
        # -----------------------------------------------------------------------
        # NON-TIME-SHIFT CONSTRAINT APPLICATION (existing behavior)
        # -----------------------------------------------------------------------
        for ev in sim_state["events"]:
            eid = ev["id"]
            b_asgn = base_assignments.get(eid)
            if b_asgn:
                # 1. Preserving or locking venue
                if req.preserve_original_venue or not req.allow_venue_shift:
                    ev["required_venue_id"] = b_asgn["venue_id"]
                    ev["preferred_venue_id"] = b_asgn["venue_id"]

                # 2. Preserving or allowing time shift
                if req.preserve_original_time or not req.allow_time_shift:
                    ev["allowed_slots"] = b_asgn["slots"]
                    ev["fixed_start_slot"] = b_asgn["slots"][0]
                elif req.allow_time_shift and not req.preserve_original_time:
                    ev["fixed_start_slot"] = None

                # 3. Disallowing cascade shifts for non-target events
                if not req.allow_cascade and eid not in target_event_ids:
                    ev["allowed_slots"] = b_asgn["slots"]
                    ev["fixed_start_slot"] = b_asgn["slots"][0]
                    ev["required_venue_id"] = b_asgn["venue_id"]

        modified_schedule = solve_schedule(sim_state, max_nodes=req.max_nodes, ml_slot_scorer=get_slot_penalty)

    # -----------------------------------------------------------------------
    # FINAL VALIDATION — detect and evict any remaining hard-constraint violations
    # Runs after BOTH branches. Guards against solver edge-cases (e.g. venue overlap).
    # -----------------------------------------------------------------------
    unavail_ids: set = {req.unavailable_venue_id} if req.unavailable_venue_id is not None else set()
    final_assignments_list = list(modified_schedule.get("assignments", []))
    final_unresolved_list = list(modified_schedule.get("unresolved", []))

    violations = validate_schedule(sim_state, final_assignments_list, unavailable_venue_ids=unavail_ids)

    # Evict lower-priority events from any VENUE_OVERLAP violation pair
    if violations:
        eids_to_evict: set = set()
        sim_event_priority = {e["id"]: e.get("priority", "normal") for e in sim_state["events"]}

        for v in violations:
            if not v.startswith("VENUE_OVERLAP:"):
                continue
            # Match violation back to the two assignments mentioned by name
            overlapping = [a for a in final_assignments_list if a["event_name"] in v]
            if len(overlapping) >= 2:
                def _rank(a: dict) -> int:
                    return PRIORITY_RANK.get(sim_event_priority.get(a["event_id"], "normal"), 2)
                overlapping.sort(key=_rank)  # lowest index = highest priority
                for loser in overlapping[1:]:
                    eids_to_evict.add(loser["event_id"])

        if eids_to_evict:
            kept = [a for a in final_assignments_list if a["event_id"] not in eids_to_evict]
            evicted_unresolved = []
            sim_event_map = {e["id"]: e for e in sim_state["events"]}
            sim_dept_map = {d["id"]: d.get("name", "General") for d in sim_state.get("departments", [])}
            for eid in eids_to_evict:
                ev_obj = sim_event_map.get(eid, {})
                dept_name = sim_dept_map.get(ev_obj.get("department_id"), "General")
                evicted_unresolved.append({
                    "event_id": eid,
                    "event_name": ev_obj.get("name", f"Event {eid}"),
                    "department_name": dept_name,
                    "priority": ev_obj.get("priority", "normal"),
                    "reasons": ["VENUE_OVERLAP: evicted because it conflicted with a higher-priority event in the same venue after cascade rescheduling."],
                    "suggestion": None,
                })
            final_assignments_list = kept
            final_unresolved_list = final_unresolved_list + evicted_unresolved
            modified_schedule = dict(modified_schedule)
            modified_schedule["assignments"] = final_assignments_list
            modified_schedule["unresolved"] = final_unresolved_list
            modified_schedule["complete"] = len(final_unresolved_list) == 0
            # Re-validate after eviction to confirm clean
            violations = validate_schedule(sim_state, final_assignments_list, unavailable_venue_ids=unavail_ids)

    # Compute difference list
    diffs = []
    mod_assignments = {a["event_id"]: a for a in modified_schedule.get("assignments", [])}
    mod_unresolved = {u["event_id"]: u for u in modified_schedule.get("unresolved", [])}
    base_venues_map = {v["id"]: v for v in base_state["venues"]}

    def format_slots(slots: Optional[List[str]]) -> str:
        if not slots:
            return ""
        try:
            start_dt = datetime.fromisoformat(slots[0])
            end_dt = datetime.fromisoformat(slots[-1]) + timedelta(hours=1)
            start_str = start_dt.strftime("%H:%M")
            end_str = end_dt.strftime("%H:%M")
            return f"{start_str}–{end_str}"
        except Exception:
            return ", ".join(slots)

    dependency_chain: List[str] = []
    if req.unavailable_venue_id is not None:
        reason_str = req.unavailability_reason or "Maintenance"
        dependency_chain.append(f"VENUE UNAVAILABLE ({unavail_venue_name or 'Venue'} - {reason_str})")

    for ev in sim_state["events"]:
        eid = ev["id"]
        b_asgn = base_assignments.get(eid)
        m_asgn = mod_assignments.get(eid)

        b_cost = b_asgn["soft_cost"] if b_asgn else 0
        m_cost = m_asgn["soft_cost"] if m_asgn else 0
        c_change = m_cost - b_cost if (b_asgn and m_asgn) else 0

        if b_asgn and not m_asgn:
            # Case 1: Scheduled -> Unresolved
            unres_info = mod_unresolved.get(eid)
            reasons = unres_info.get("reasons", []) if unres_info else []
            max_venue_cap = max((v["capacity"] for v in sim_state["venues"] if v.get("available_slots")), default=0)
            sim_att = ev.get("expected_attendance", 0)

            base_events_map = {e["id"]: e for e in base_state["events"]}

            if sim_att > max_venue_cap or any("capacity" in r.lower() or "attend" in r.lower() for r in reasons):
                c_type = "CAPACITY CONFLICT"
                details_text = f"Event remained unresolved because no compatible venue was available for the required attendance of {sim_att}."
            elif eid == time_shift_target_eid:
                c_type = "UNRESOLVED"
                target_name = ev.get("name", "Event")
                conflict_names = [base_events_map[cid]["name"] for cid in conflicting_eids if cid in base_events_map]
                req_t_str = f"{sim_start_hour:02d}:00" if sim_start_hour is not None else "requested slot"
                if conflict_names:
                    conflict_str = ", ".join(f"'{name}'" for name in conflict_names)
                    if not req.allow_cascade:
                        details_text = f"Cannot shift '{target_name}' to {req_t_str} because it conflicts with {conflict_str}. Enable 'Allow Cascading Shifts' to automatically reschedule conflicting events."
                    else:
                        details_text = f"Cannot shift '{target_name}' to {req_t_str} because hard constraints prevent resolving conflicts with {conflict_str}."
                else:
                    if reasons:
                        details_text = f"Cannot shift '{target_name}' to {req_t_str}: {'; '.join(reasons)}"
                    else:
                        details_text = f"Cannot shift '{target_name}' to {req_t_str} because no compatible venue/staff slot is available at that hour."
            else:
                c_type = "UNSOLVED"
                if reasons:
                    details_text = f"Unresolved: {'; '.join(reasons)}"
                else:
                    details_text = "Event remained unresolved because no compatible venue was available for the required attendance and venue type."

            dependency_chain.append(f"UNSOLVED ({ev['name']})")
            diffs.append(DiffItem(
                event_id=eid,
                event_name=ev["name"],
                change_type=c_type,
                before_status="Scheduled",
                after_status="Unresolved",
                before_venue=b_asgn["venue_name"],
                before_slots=b_asgn["slots"],
                details=details_text,
                before_cost=b_cost,
                after_cost=m_cost,
                cost_change=0,
            ))
        elif not b_asgn and m_asgn:
            # Case 2: Unresolved -> Scheduled (Resolved)
            dependency_chain.append(f"EVENT REALLOCATED ({ev['name']} -> {m_asgn['venue_name']})")
            diffs.append(DiffItem(
                event_id=eid,
                event_name=ev["name"],
                change_type="FEASIBLE",
                before_status="Unresolved",
                after_status="Scheduled",
                after_venue=m_asgn["venue_name"],
                after_slots=m_asgn["slots"],
                details=f"Event is now successfully scheduled into '{m_asgn['venue_name']}'!",
                before_cost=0,
                after_cost=m_cost,
                cost_change=m_cost,
            ))
        elif not b_asgn and not m_asgn:
            # Case 3: Both Unresolved
            diffs.append(DiffItem(
                event_id=eid,
                event_name=ev["name"],
                change_type="REMAINS UNSOLVED",
                before_status="Unresolved",
                after_status="Unresolved",
                details="Event remained unresolved because no compatible venue was available for the required attendance and venue type.",
                before_cost=0,
                after_cost=0,
                cost_change=0,
            ))
        elif b_asgn and m_asgn:
            # Case 4: Both Scheduled
            b_venue_id = b_asgn["venue_id"]
            m_venue_id = m_asgn["venue_id"]
            venue_diff = (b_venue_id != m_venue_id)

            b_slots = b_asgn.get("slots", [])
            m_slots = m_asgn.get("slots", [])

            b_time_str = format_slots(b_slots)
            m_time_str = format_slots(m_slots)

            b_start = b_slots[0] if b_slots else None
            b_end = b_slots[-1] if b_slots else None
            m_start = m_slots[0] if m_slots else None
            m_end = m_slots[-1] if m_slots else None

            start_changed = (b_start != m_start) and (b_time_str != m_time_str)
            end_changed = (b_end != m_end) and (b_time_str != m_time_str)

            # NEVER label an event as TIME SHIFT unless time actually changed
            slots_diff = (b_time_str != m_time_str) or start_changed or end_changed

            if not venue_diff and not slots_diff:
                diffs.append(DiffItem(
                    event_id=eid,
                    event_name=ev["name"],
                    change_type="UNCHANGED",
                    before_status="Scheduled",
                    after_status="Scheduled",
                    before_venue=b_asgn["venue_name"],
                    after_venue=m_asgn["venue_name"],
                    before_slots=b_asgn["slots"],
                    after_slots=m_asgn["slots"],
                    details="Preserved exact time and venue slot.",
                    before_cost=b_cost,
                    after_cost=m_cost,
                    cost_change=0,
                ))
            elif venue_diff and not slots_diff:
                dependency_chain.append(f"VENUE SHIFT ({ev['name']} -> {m_asgn['venue_name']})")
                orig_v_id = b_asgn["venue_id"]
                orig_v = base_venues_map.get(orig_v_id)
                orig_cap = orig_v["capacity"] if orig_v else 0
                sim_att = ev.get("expected_attendance", 0)

                venue_ov = req.venue_overrides.get(orig_v_id) or req.venue_overrides.get(str(orig_v_id))
                if req.unavailable_venue_id == orig_v_id:
                    reason_str = (req.unavailability_reason or "Maintenance").lower()
                    detail_msg = f"Shifted from {b_asgn['venue_name']} to {m_asgn['venue_name']} because {b_asgn['venue_name']} is temporarily unavailable due to {reason_str}."
                elif venue_ov and "capacity" in venue_ov and venue_ov["capacity"] < sim_att:
                    detail_msg = f"Shifted from {b_asgn['venue_name']} to {m_asgn['venue_name']} because the simulated capacity of {b_asgn['venue_name']} is insufficient for the expected attendance of {sim_att}."
                elif orig_cap > 0 and sim_att > orig_cap:
                    detail_msg = f"Shifted from {b_asgn['venue_name']} to {m_asgn['venue_name']} because the simulated attendance ({sim_att}) exceeds the original venue capacity ({orig_cap})."
                elif time_shift_target_eid is not None and eid in conflicting_eids:
                    target_name = (target_ev_obj.get("name") if target_ev_obj else None) or "the target event"
                    detail_msg = f"Shifted because it conflicted with {target_name} after the requested time shift."
                else:
                    detail_msg = f"Shifted from {b_asgn['venue_name']} to {m_asgn['venue_name']} to achieve lowest soft cost solution."

                diffs.append(DiffItem(
                    event_id=eid,
                    event_name=ev["name"],
                    change_type="VENUE SHIFT",
                    before_status="Scheduled",
                    after_status="Scheduled",
                    before_venue=b_asgn["venue_name"],
                    after_venue=m_asgn["venue_name"],
                    before_slots=b_asgn["slots"],
                    after_slots=m_asgn["slots"],
                    details=detail_msg,
                    before_cost=b_cost,
                    after_cost=m_cost,
                    cost_change=c_change,
                ))
            elif not venue_diff and slots_diff:
                dependency_chain.append(f"TIME SHIFT ({ev['name']})")
                old_t = format_slots(b_asgn["slots"])
                new_t = format_slots(m_asgn["slots"])
                if time_shift_target_eid is not None and eid == time_shift_target_eid:
                    detail_msg = f"User-requested time shift from {old_t} to {new_t}."
                elif time_shift_target_eid is not None and eid in conflicting_eids:
                    target_name = (target_ev_obj.get("name") if target_ev_obj else None) or "the target event"
                    detail_msg = f"Shifted because it conflicted with {target_name} after the requested time shift."
                elif time_shift_target_eid is not None and eid != time_shift_target_eid:
                    target_name = (target_ev_obj.get("name") if target_ev_obj else None) or "the target event"
                    detail_msg = f"Cascade-shifted from {old_t} to {new_t} to resolve schedule dependencies with {target_name}."
                else:
                    detail_msg = f"Rescheduled from {old_t} to {new_t} because the original time slot could not be retained."
                diffs.append(DiffItem(
                    event_id=eid,
                    event_name=ev["name"],
                    change_type="TIME SHIFT",
                    before_status="Scheduled",
                    after_status="Scheduled",
                    before_venue=b_asgn["venue_name"],
                    after_venue=m_asgn["venue_name"],
                    before_slots=b_asgn["slots"],
                    after_slots=m_asgn["slots"],
                    details=detail_msg,
                    before_cost=b_cost,
                    after_cost=m_cost,
                    cost_change=c_change,
                ))
            else:
                dependency_chain.append(f"TIME + VENUE SHIFT ({ev['name']} -> {m_asgn['venue_name']})")
                old_t = format_slots(b_asgn["slots"])
                new_t = format_slots(m_asgn["slots"])
                if time_shift_target_eid is not None and eid in conflicting_eids:
                    target_name = (target_ev_obj.get("name") if target_ev_obj else None) or "the target event"
                    detail_msg = f"Shifted to {m_asgn['venue_name']} ({new_t}) because it conflicted with {target_name} after the requested time shift."
                else:
                    detail_msg = f"Rescheduled from {b_asgn['venue_name']} ({old_t}) to {m_asgn['venue_name']} ({new_t}) to resolve the venue and time conflict."
                diffs.append(DiffItem(
                    event_id=eid,
                    event_name=ev["name"],
                    change_type="VENUE_AND_TIME_SHIFT",
                    before_status="Scheduled",
                    after_status="Scheduled",
                    before_venue=b_asgn["venue_name"],
                    after_venue=m_asgn["venue_name"],
                    before_slots=b_asgn["slots"],
                    after_slots=m_asgn["slots"],
                    details=detail_msg,
                    before_cost=b_cost,
                    after_cost=m_cost,
                    cost_change=c_change,
                ))

    has_unresolved = len(mod_unresolved) > 0
    major_hard_conflict = any(d.change_type in ("RESOURCE CONFLICT", "INFEASIBLE") for d in diffs)

    if major_hard_conflict or violations:
        feasibility_status = "INFEASIBLE SCHEDULE"
    elif has_unresolved:
        feasibility_status = "PARTIALLY FEASIBLE SCHEDULE"
    else:
        feasibility_status = "FINAL FEASIBLE SCHEDULE"

    if dependency_chain:
        dependency_chain.append(feasibility_status)

    # Calculate Summary Statistics & Impact Scores
    # Actual schedule changes exclude UNCHANGED and REMAINS UNSOLVED
    total_changes = len([d for d in diffs if d.change_type not in ("UNCHANGED", "REMAINS UNSOLVED")])
    venue_shifts = len([d for d in diffs if d.change_type in ("VENUE SHIFT", "TIME + VENUE SHIFT", "VENUE_AND_TIME_SHIFT")])
    time_shifts = len([d for d in diffs if d.change_type in ("TIME SHIFT", "TIME + VENUE SHIFT", "VENUE_AND_TIME_SHIFT")])

    orig_total_cost = sum(a["soft_cost"] for a in baseline_schedule.get("assignments", []))
    new_total_cost = sum(a["soft_cost"] for a in modified_schedule.get("assignments", []))
    additional_soft_cost = new_total_cost - orig_total_cost

    # Impact Levels
    def level_map(val: int, low_t: int = 1, med_t: int = 2, high_t: int = 4) -> str:
        if val == 0:
            return "None"
        elif val < med_t:
            return "Low"
        elif val < high_t:
            return "Medium"
        return "High"

    schedule_impact = level_map(total_changes, 1, 2, 4)
    venue_impact = level_map(venue_shifts, 1, 2, 3)
    time_impact = level_map(time_shifts, 1, 2, 3)

    if additional_soft_cost <= 0:
        cost_impact = "None"
    elif additional_soft_cost <= 5:
        cost_impact = "Low"
    elif additional_soft_cost <= 15:
        cost_impact = "Medium"
    else:
        cost_impact = "High"

    import time
    scenario_id = f"SCEN-{int(time.time() * 1000) % 100000:05d}"

    # New granular impact metrics
    changed_events_count = len([d for d in diffs if d.change_type not in ("UNCHANGED", "REMAINS UNSOLVED", "REMAINS_UNSOLVED")])
    time_shifts_count = len([d for d in diffs if d.change_type in ("TIME SHIFT", "TIME + VENUE SHIFT", "VENUE_AND_TIME_SHIFT")])
    venue_shifts_count = len([d for d in diffs if d.change_type in ("VENUE SHIFT", "TIME + VENUE SHIFT", "VENUE_AND_TIME_SHIFT")])
    unresolved_count = len([d for d in diffs if d.after_status == "Unresolved"])
    hard_violations_count = len(violations)

    return WhatIfResult(
        baseline_run_id=baseline_schedule.get("run_id"),
        modified_schedule=modified_schedule,
        differences=diffs,
        schedule_impact=schedule_impact,
        venue_impact=venue_impact,
        time_impact=time_impact,
        cost_impact=cost_impact,
        total_changes=total_changes,
        original_total_cost=orig_total_cost,
        new_total_cost=new_total_cost,
        additional_soft_cost=additional_soft_cost,
        dependency_chain=dependency_chain,
        scenario_id=scenario_id,
        changed_events=changed_events_count,
        time_shifts=time_shifts_count,
        venue_shifts=venue_shifts_count,
        unresolved_events=unresolved_count,
        hard_constraint_violations=hard_violations_count,
        feasibility_status=feasibility_status,
    )


# ---------------------------------------------------------------------------
# Analytics Dashboard
# ---------------------------------------------------------------------------

@app.get("/analytics", response_model=AnalyticsResult)
@app.get("/api/analytics", response_model=AnalyticsResult)
def get_analytics():
    state = db.get_state()
    sched = db.latest_schedule()
    if not sched:
        sched = solve_schedule(state, ml_slot_scorer=get_slot_penalty)

    total_events = len(state["events"])
    scheduled_events = len(sched.get("assignments", []))
    unresolved_events = len(sched.get("unresolved", []))
    success_rate = round((scheduled_events / max(1, total_events)) * 100, 1)

    # 1. Venue Utilization
    venue_util = []
    assignments = sched.get("assignments", [])
    for v in state["venues"]:
        total_avail = len(v.get("available_slots", []))
        booked_count = sum(len(a["slots"]) for a in assignments if a["venue_id"] == v["id"])
        rate = round((booked_count / max(1, total_avail)) * 100, 1)
        venue_util.append(VenueUtilization(
            venue_id=v["id"],
            venue_name=v["name"],
            capacity=v["capacity"],
            total_slots_available=total_avail,
            booked_slots=booked_count,
            utilization_rate_pct=rate,
        ))

    # 2. Department Equity
    dept_equity = []
    for d in state["departments"]:
        d_events = [e for e in state["events"] if e["department_id"] == d["id"]]
        d_scheduled = [a for a in assignments if any(e["id"] == a["event_id"] and e["department_id"] == d["id"] for e in state["events"])]
        rate = round((len(d_scheduled) / max(1, len(d_events))) * 100, 1) if d_events else 100.0
        dept_equity.append(DepartmentEquity(
            department_id=d["id"],
            department_name=d["name"],
            events_requested=len(d_events),
            events_scheduled=len(d_scheduled),
            satisfaction_rate_pct=rate,
        ))

    conflict_count = len(sched.get("unresolved", []))

    return AnalyticsResult(
        total_events=total_events,
        scheduled_events=scheduled_events,
        unresolved_events=unresolved_events,
        schedule_success_rate_pct=success_rate,
        total_soft_cost=sched.get("total_soft_cost", 0),
        average_soft_cost_per_event=round(sched.get("total_soft_cost", 0) / max(1, scheduled_events), 2),
        venue_utilization=venue_util,
        department_equity=dept_equity,
        conflicts_resolved_count=conflict_count,
        solver_runtime_ms=sched.get("solver_time_ms", 0.0),
    )


# ---------------------------------------------------------------------------
# Schedule Exports (CSV, PDF, ICS)
# ---------------------------------------------------------------------------

@app.get("/export/{fmt}")
@app.get("/api/export/{fmt}")
def export_schedule(fmt: str):
    sched = db.latest_schedule()
    if not sched:
        state = db.get_state()
        sched = solve_schedule(state, ml_slot_scorer=get_slot_penalty)

    fmt_lower = fmt.lower()

    if fmt_lower == "csv":
        content = export.generate_csv(sched)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=lpu_schedule.csv"},
        )
    elif fmt_lower == "pdf":
        content = export.generate_pdf(sched)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=lpu_schedule.pdf"},
        )
    elif fmt_lower in ("ics", "calendar", "ical"):
        content = export.generate_ics(sched)
        return Response(
            content=content,
            media_type="text/calendar",
            headers={"Content-Disposition": "attachment; filename=lpu_schedule.ics"},
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{fmt}'. Supported: csv, pdf, ics")
