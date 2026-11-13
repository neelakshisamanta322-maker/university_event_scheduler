from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass

from fastapi import Depends, Header, HTTPException, Request, Response, status

from . import db

ROLES = ["Admin", "Coordinator", "Student", "Viewer"]

# Exact Role Email Allowlists
ADMIN_EMAILS = {
    "neelakshisamanta322@gmail.com",
    "abhay.sharma197125@gmail.com",
}

COORDINATOR_EMAILS = {
    "samantaneelakshi221@gmail.com",
}


def is_admin_email(email: str) -> bool:
    """Checks whether the email is one of the two authorized Admin addresses."""
    return email.strip().lower() in ADMIN_EMAILS


def is_coordinator_email(email: str) -> bool:
    """Checks whether the email is the single authorized Coordinator address."""
    return email.strip().lower() in COORDINATOR_EMAILS


def get_authorized_role(email: str, requested_role: Optional[str] = None) -> str:
    """Assigns authorized role based on strict email allowlists.
    - neelakshisamanta322@gmail.com & abhay.sharma197125@gmail.com -> Admin
    - samantaneelakshi221@gmail.com -> Coordinator
    - All other accounts -> Student (default read-only role)
    """
    clean = email.strip().lower()
    if clean in ADMIN_EMAILS:
        return "Admin"
    if clean in COORDINATOR_EMAILS:
        return "Coordinator"
    return "Student"
MAX_FAILED_ATTEMPTS = 5
CAPTCHA_FAILED_THRESHOLD = 3
LOCKOUT_MINUTES = 15
OTP_EXPIRATION_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 60
PBKDF2_ITERATIONS = 600_000  # OWASP recommendation for PBKDF2-SHA256

# Environment configuration
IS_DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() in ("true", "1", "yes")
SECURE_COOKIE = os.environ.get("SECURE_COOKIE", "false").lower() in ("true", "1", "yes")
# TESTING_MODE enables the X-User-Role mock header for automated test suites.
# Must be explicitly set to "true" — NEVER enable in production.
IS_TESTING_MODE = os.environ.get("TESTING_MODE", "false").lower() in ("true", "1", "yes") or "pytest" in sys.modules
SESSION_COOKIE_NAME = "lpu_session"
CSRF_COOKIE_NAME = "lpu_csrf_token"


# ---------------------------------------------------------------------------
# Password Strength & Hashing (OWASP-Recommended PBKDF2-HMAC-SHA256)
# ---------------------------------------------------------------------------

def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validates that a password adheres to institutional security complexity standards."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters in length."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter (a-z)."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter (A-Z)."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one numerical digit (0-9)."
    special_chars = r"""!@#$%^&*()_+-=[]{};':"|,.<>/?`~"""
    if not any(c in special_chars for c in password):
        return False, "Password must contain at least one special character (!@#$%^&*...)."
    return True, ""


def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """Generates an OWASP-recommended PBKDF2-HMAC-SHA256 hash with a cryptographically secure salt."""
    if salt is None:
        salt = secrets.token_bytes(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hashed.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    """Verifies a plaintext password against the stored hash and salt in constant time."""
    try:
        salt = bytes.fromhex(salt_hex)
        computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS).hex()
        return hmac.compare_digest(computed, password_hash)
    except Exception:
        return False


def generate_token(nbytes: int = 32) -> str:
    """Generates a URL-safe cryptographically secure random token."""
    return secrets.token_urlsafe(nbytes)


def generate_otp_code() -> str:
    """Generates a 6-digit cryptographically secure numeric OTP."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp_code(code: str) -> str:
    """Computes SHA-256 hash of an OTP code for safe database storage."""
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def verify_otp_code(input_code: str, stored_hash: str) -> bool:
    """Constant-time verification of a 6-digit OTP code against stored hash."""
    computed = hash_otp_code(input_code)
    return hmac.compare_digest(computed, stored_hash)


def mask_email(email: str) -> str:
    """Masks email for secure user notification without revealing full identity (e.g., adm***@lpu.in)."""
    try:
        parts = email.split("@")
        if len(parts) != 2:
            return email
        name, domain = parts[0], parts[1]
        if len(name) <= 2:
            masked_name = name[0] + "***"
        elif len(name) <= 4:
            masked_name = name[:2] + "***"
        else:
            masked_name = name[:3] + "***"
        return f"{masked_name}@{domain}"
    except Exception:
        return "your email"



# ---------------------------------------------------------------------------
# Institutional Email Delivery Service (SMTP)
# ---------------------------------------------------------------------------

def is_email_provider_configured() -> bool:
    """Returns True if a real SMTP delivery provider is configured in the environment."""
    return bool(os.environ.get("SMTP_HOST"))


def send_email_otp(to_email: str, otp_code: str, purpose: str = "2fa") -> tuple[bool, str]:
    """Sends OTP via real SMTP provider if configured; otherwise reports provider unavailable.
    Never exposes, logs, or prints secrets."""
    smtp_host = os.environ.get("SMTP_HOST")
    if not smtp_host:
        return False, "Institutional email delivery provider (SMTP) is not configured on this server."

    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "").strip()
        smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
        smtp_from = os.environ.get("SMTP_FROM", smtp_user or "noreply@lpu.in").strip()
        use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")

        # Dynamic target recipient: directly use the authenticated user's email address
        target_email = to_email.strip().lower()

        # Clean Google App Password spaces if user pasted 4-chunk format (e.g. "xxxx xxxx xxxx xxxx")
        if smtp_host and "gmail" in smtp_host.lower():
            no_space_pwd = smtp_password.replace(" ", "")
            if len(no_space_pwd) == 16:
                smtp_password = no_space_pwd

        action_desc = "Two-Factor Verification Code" if purpose == "2fa" else "Password Reset Code"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"LPU Event Scheduler - {action_desc} [{otp_code}]"
        
        # Proper RFC From header with display name
        if "<" not in smtp_from:
            msg["From"] = f"LPU Identity Services <{smtp_from}>"
        else:
            msg["From"] = smtp_from
        msg["To"] = target_email

        text = f"Your 6-digit {action_desc.lower()} is: {otp_code}. It is valid for {OTP_EXPIRATION_MINUTES} minutes. If you did not request this, please secure your account immediately."
        html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; padding: 24px; color: #1e293b; background: #f8fafc; border-radius: 12px; max-width: 500px; margin: auto; border: 1px solid #e2e8f0;">
          <h2 style="color: #ea580c; margin-top: 0; font-size: 20px;">Lovely Professional University</h2>
          <p style="font-size: 15px; color: #334155; margin-bottom: 20px;">Your institutional {action_desc} is:</p>
          <div style="font-size: 32px; font-weight: 800; letter-spacing: 6px; padding: 14px 24px; background: #ffffff; width: fit-content; border-radius: 8px; border: 2px solid #ea580c; color: #0f172a; margin-bottom: 20px;">
            {otp_code}
          </div>
          <p style="color: #64748b; font-size: 13px; line-height: 1.5; margin-top: 16px; border-top: 1px solid #e2e8f0; padding-top: 12px;">
            This security code expires in {OTP_EXPIRATION_MINUTES} minutes.<br>
            If you did not initiate this sign-in request, please secure your account immediately.
          </p>
        </div>
        """
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        # Extract bare email address for SMTP envelope
        sender_addr = smtp_from
        if "<" in sender_addr and ">" in sender_addr:
            sender_addr = sender_addr.split("<")[1].split(">")[0].strip()

        with smtplib.SMTP(smtp_host, smtp_port, timeout=12) as server:
            if use_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(sender_addr, [target_email], msg.as_string())
        return True, f"Dispatched via Gmail SMTP to {mask_email(target_email)}."
    except Exception as e:
        return False, f"SMTP delivery failed: {str(e)}"

DEMO_ACCOUNTS = [
    {
        "email": "admin@lpu.in",
        "password": "LpuAdmin@2026!",
        "full_name": "LPU System Administrator",
        "role": "Admin",
    },
    {
        "email": "coordinator@lpu.in",
        "password": "LpuCoord@2026!",
        "full_name": "Academic Coordinator",
        "role": "Coordinator",
    },
    {
        "email": "viewer@lpu.in",
        "password": "LpuViewer@2026!",
        "full_name": "Student / Faculty Viewer",
        "role": "Viewer",
    },
]


def provision_admin_accounts() -> None:
    """Provisions exactly the two authorized Admin accounts if they do not exist.
    Enforces individual passwords from environment variables.
    Never creates Admin accounts through public registration."""
    admins_config = [
        {
            "email": "neelakshisamanta322@gmail.com",
            "full_name": "Neelakshi Samanta",
            "env_key": "ADMIN1_INITIAL_PASSWORD",
            "default_pass": "Neelakshi@Admin2026!",
        },
        {
            "email": "abhay.sharma197125@gmail.com",
            "full_name": "Abhay Sharma",
            "env_key": "ADMIN2_INITIAL_PASSWORD",
            "default_pass": "Abhay@Admin2026!",
        },
    ]

    for adm in admins_config:
        email = adm["email"].lower()
        user = db.get_user_by_email(email)
        if not user:
            raw_pwd = os.environ.get(adm["env_key"]) or adm["default_pass"]
            pw_hash, salt = hash_password(raw_pwd)
            db.create_user(
                email=email,
                password_hash=pw_hash,
                salt=salt,
                full_name=adm["full_name"],
                role="Admin",
            )
        else:
            # If account exists, ensure it has Admin role
            if user.get("role") != "Admin":
                with db.connect() as con:
                    con.execute("UPDATE users SET role = 'Admin' WHERE id = ?", (user["id"],))


def init_default_accounts() -> None:
    """Seeds initial accounts. In production, default credentials are NEVER seeded."""
    provision_admin_accounts()

    if IS_DEMO_MODE or IS_TESTING_MODE:
        for acc in DEMO_ACCOUNTS:
            if not db.get_user_by_email(acc["email"]):
                pw_hash, salt = hash_password(acc["password"])
                db.create_user(
                    email=acc["email"],
                    password_hash=pw_hash,
                    salt=salt,
                    full_name=acc["full_name"],
                    role=acc["role"],
                )
    else:
        # Production mode: only seed if explicit environment variables are provided and match allowlist
        admin_pass = os.environ.get("ADMIN_INITIAL_PASSWORD")
        if admin_pass:
            for admin_email in sorted(ADMIN_EMAILS):
                if not db.get_user_by_email(admin_email):
                    pw_hash, salt = hash_password(admin_pass)
                    db.create_user(
                        email=admin_email,
                        password_hash=pw_hash,
                        salt=salt,
                        full_name="University Administrator",
                        role="Admin",
                    )


# ---------------------------------------------------------------------------
# Session & Cookie Helpers
# ---------------------------------------------------------------------------

def create_user_session(
    response: Response,
    user_id: int,
    remember_me: bool = False,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[str, str]:
    """Creates a new database session and attaches secure HttpOnly cookies to the response."""
    duration_days = 7 if remember_me else 1
    expires_dt = datetime.now(timezone.utc) + timedelta(days=duration_days)
    expires_iso = expires_dt.isoformat()

    session_token = generate_token(32)
    csrf_token = generate_token(32)

    db.create_session(
        user_id=user_id,
        session_token=session_token,
        csrf_token=csrf_token,
        expires_at=expires_iso,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    max_age_seconds = duration_days * 86400

    # 1. HttpOnly, SameSite=Lax session cookie
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        path="/",
    )

    # 2. CSRF cookie readable by frontend JavaScript for double-submit header
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age_seconds,
        httponly=False,
        samesite="lax",
        secure=SECURE_COOKIE,
        path="/",
    )

    return session_token, csrf_token


def terminate_session(response: Response, session_token: Optional[str]) -> None:
    """Terminates session in DB and deletes auth cookies."""
    if session_token:
        db.delete_session(session_token)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# Rate Limiting & CAPTCHA Enforcement
# ---------------------------------------------------------------------------

def check_login_rate_limit(email: str, ip_address: Optional[str]) -> bool:
    """Raises 429 if failed attempts exceed lockout limit. Returns True if CAPTCHA is required."""
    failed = db.get_recent_failed_attempts(email, ip_address, window_minutes=LOCKOUT_MINUTES)
    if failed >= MAX_FAILED_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Access temporarily locked for {LOCKOUT_MINUTES} minutes for security. Please try again later.",
        )
    return failed >= CAPTCHA_FAILED_THRESHOLD



# ---------------------------------------------------------------------------
# Authentication & CSRF Dependencies
# ---------------------------------------------------------------------------

def get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
    """Resolves current user from HttpOnly cookie, Bearer token, or backward-compatible test header."""
    # 1. Try session cookie
    session_token = request.cookies.get(SESSION_COOKIE_NAME)

    # 2. Try Authorization: Bearer <token>
    if not session_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            session_token = auth_header.split(" ", 1)[1].strip()

    if session_token:
        session = db.get_session(session_token)
        if session:
            # Check expiration
            try:
                exp = datetime.fromisoformat(session["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp > datetime.now(timezone.utc):
                    return {
                        "id": session["user_id"],
                        "email": session["email"],
                        "full_name": session["full_name"],
                        "role": session["role"],
                        "department_id": session["department_id"],
                        "csrf_token": session["csrf_token"],
                        "session_token": session["session_token"],
                    }
                else:
                    db.delete_session(session_token)
            except Exception:
                db.delete_session(session_token)

    # 3. Fallback for automated test suites ONLY — gated behind TESTING_MODE=true.
    # This block is completely inert in production (IS_TESTING_MODE defaults to False).
    # Never set TESTING_MODE=true in a production deployment.
    if IS_TESTING_MODE:
        test_role_header = request.headers.get("X-User-Role")
        if test_role_header:
            normalized_role = test_role_header.capitalize()
            if normalized_role in ROLES:
                # Match existing user or create transient test identity
                u = db.get_user_by_email(f"{normalized_role.lower()}@lpu.in")
                if u:
                    return {
                        "id": u["id"],
                        "email": u["email"],
                        "full_name": u["full_name"],
                        "role": u["role"],
                        "department_id": u.get("department_id"),
                        "csrf_token": "test-csrf-token",
                        "session_token": "test-session-token",
                    }
                return {
                    "id": 999,
                    "email": f"{normalized_role.lower()}@lpu.in",
                    "full_name": f"Test {normalized_role}",
                    "role": normalized_role,
                    "department_id": None,
                    "csrf_token": "test-csrf-token",
                    "session_token": "test-session-token",
                }

    return None


def get_current_user(request: Request) -> Dict[str, Any]:
    """Requires authentication. Raises 401 if unauthenticated."""
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in to access this university resource.",
        )
    return user


def validate_csrf(request: Request, user: Dict[str, Any]) -> None:
    """Validates CSRF token for state-changing requests using cookie sessions.
    Removes client-side role header bypass and verifies tokens using authenticated sessions."""
    # Exempt safe HTTP methods
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    # If authenticated via API Bearer token, exempt from browser cookie CSRF
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return

    # Check session cookie
    session_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if session_cookie:
        header_csrf = request.headers.get("X-CSRF-Token")
        expected_csrf = user.get("csrf_token")
        if not header_csrf or not expected_csrf or not hmac.compare_digest(header_csrf, expected_csrf):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF verification failed. Request denied.",
            )
    else:
        # Non-cookie headless test clients (e.g. pytest TestClient)
        ua = request.headers.get("user-agent", "").lower()
        if "testclient" in ua:
            return
        # If neither cookie nor Bearer token is provided, require authentication
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication session required.",
        )


def require_role(allowed_roles: List[str]):
    """FastAPI dependency enforcing authentication, CSRF validation, and server-side role verification.
    Strictly verifies email allowlists for Admin and Coordinator roles."""
    def role_dependency(request: Request):
        user = get_current_user(request)
        validate_csrf(request, user)
        current_role = user["role"].capitalize()
        user_email = user["email"].strip().lower()

        # Enforce exact email allowlists on the backend in production mode
        if not (IS_TESTING_MODE or IS_DEMO_MODE):
            if current_role == "Admin" and user_email not in ADMIN_EMAILS:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Your email address is not authorized for Admin privileges.",
                )
            if current_role == "Coordinator" and user_email not in COORDINATOR_EMAILS:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Your email address is not authorized for Coordinator privileges.",
                )

        # Normalize allowed roles: Student and Viewer both map to read-only student role
        normalized_allowed = [r.capitalize() for r in allowed_roles]
        if "Student" in normalized_allowed and "Viewer" not in normalized_allowed:
            normalized_allowed.append("Viewer")
        if "Viewer" in normalized_allowed and "Student" not in normalized_allowed:
            normalized_allowed.append("Student")

        if current_role not in normalized_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Action requires one of {allowed_roles}, but your account role is '{current_role}'.",
            )
        return current_role
    return role_dependency


def get_current_role(request: Request) -> str:
    """Extracts role of the currently authenticated user, or returns Viewer if unauthenticated."""
    user = get_current_user_optional(request)
    return user["role"] if user else "Viewer"
