from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import auth, db

client = TestClient(app)


@pytest.fixture(autouse=True)
def ensure_db_ready():
    """Ensure database and default demo accounts are initialized and clear client cookies before each test."""
    client.cookies.clear()
    db.init_db()
    with db.connect() as con:
        con.execute("DELETE FROM login_attempts")
    auth.init_default_accounts()


def test_login_success_admin():
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@lpu.in", "password": "LpuAdmin@2026!", "remember_me": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["user"]["email"] == "admin@lpu.in"
    assert data["user"]["role"] == "Admin"
    assert "session_token" in data
    assert "csrf_token" in data
    assert auth.SESSION_COOKIE_NAME in res.cookies
    assert auth.CSRF_COOKIE_NAME in res.cookies


def test_login_invalid_password():
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@lpu.in", "password": "WrongPassword123!"},
    )
    assert res.status_code == 401
    assert "Invalid university email address or password" in res.json()["detail"]


def test_login_invalid_email():
    res = client.post(
        "/api/auth/login",
        json={"email": "unknown_user@lpu.in", "password": "AnyPassword!"},
    )
    assert res.status_code == 401
    assert "Invalid university email address or password" in res.json()["detail"]


def test_login_empty_fields_validation():
    res = client.post(
        "/api/auth/login",
        json={"email": "", "password": ""},
    )
    assert res.status_code in (401, 422)


def test_auth_me_lifecycle():
    # 1. Unauthenticated me
    unauth_res = client.get("/api/auth/me")
    assert unauth_res.status_code == 200
    assert unauth_res.json()["authenticated"] is False

    # 2. Login
    login_res = client.post(
        "/api/auth/login",
        json={"email": "coordinator@lpu.in", "password": "LpuCoord@2026!"},
    )
    assert login_res.status_code == 200
    session_cookie = login_res.cookies.get(auth.SESSION_COOKIE_NAME)

    # 3. Authenticated me using cookie
    auth_res = client.get("/api/auth/me", cookies={auth.SESSION_COOKIE_NAME: session_cookie})
    assert auth_res.status_code == 200
    me_data = auth_res.json()
    assert me_data["authenticated"] is True
    assert me_data["user"]["role"] == "Coordinator"

    # 4. Logout
    logout_res = client.post("/api/auth/logout", cookies={auth.SESSION_COOKIE_NAME: session_cookie})
    assert logout_res.status_code == 200

    # 5. Subsequent me is unauthenticated
    after_res = client.get("/api/auth/me", cookies={auth.SESSION_COOKIE_NAME: session_cookie})
    assert after_res.json()["authenticated"] is False


def test_rate_limiting():
    test_ip = "192.168.1.100"
    test_email = "ratelimit_test@lpu.in"
    db.clear_failed_attempts(test_email, test_ip)

    # Trigger 5 failed attempts
    for _ in range(5):
        client.post(
            "/api/auth/login",
            json={"email": test_email, "password": "WrongPassword"},
            headers={"X-Forwarded-For": test_ip},
        )

    # 6th attempt should be blocked by rate limiting (429)
    res = client.post(
        "/api/auth/login",
        json={"email": test_email, "password": "WrongPassword"},
        headers={"X-Forwarded-For": test_ip},
    )
    assert res.status_code == 429
    assert "Too many failed login attempts" in res.json()["detail"]

    # Cleanup test attempts
    db.clear_failed_attempts(test_email, test_ip)


def test_role_enforcement_with_cookies():
    # Login as Viewer
    viewer_login = client.post(
        "/api/auth/login",
        json={"email": "viewer@lpu.in", "password": "LpuViewer@2026!"},
    )
    viewer_cookie = viewer_login.cookies.get(auth.SESSION_COOKIE_NAME)
    viewer_csrf = viewer_login.json()["csrf_token"]

    # Viewer cannot reset demo (requires Admin) -> 403 Forbidden
    res = client.post(
        "/api/demo/reset",
        cookies={auth.SESSION_COOKIE_NAME: viewer_cookie},
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert res.status_code == 403
    assert "Access denied" in res.json()["detail"]

    # Login as Admin
    admin_login = client.post(
        "/api/auth/login",
        json={"email": "admin@lpu.in", "password": "LpuAdmin@2026!"},
    )
    admin_cookie = admin_login.cookies.get(auth.SESSION_COOKIE_NAME)
    admin_csrf = admin_login.json()["csrf_token"]

    # Admin CAN reset demo -> 200 OK
    res_admin = client.post(
        "/api/demo/reset",
        cookies={auth.SESSION_COOKIE_NAME: admin_cookie},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert res_admin.status_code == 200
    assert res_admin.json()["ok"] is True


def test_csrf_protection_on_cookie_session():
    # Login as Admin
    admin_login = client.post(
        "/api/auth/login",
        json={"email": "admin@lpu.in", "password": "LpuAdmin@2026!"},
    )
    admin_cookie = admin_login.cookies.get(auth.SESSION_COOKIE_NAME)

    # Omit CSRF header -> 403 Forbidden
    res_no_csrf = client.post(
        "/api/demo/reset",
        cookies={auth.SESSION_COOKIE_NAME: admin_cookie},
    )
    assert res_no_csrf.status_code == 403
    assert "CSRF verification failed" in res_no_csrf.json()["detail"]

    # Wrong CSRF token -> 403 Forbidden
    res_bad_csrf = client.post(
        "/api/demo/reset",
        cookies={auth.SESSION_COOKIE_NAME: admin_cookie},
        headers={"X-CSRF-Token": "invalid-csrf-token"},
    )
    assert res_bad_csrf.status_code == 403


def test_controlled_registration():
    import secrets
    tag = secrets.token_hex(4)

    # 1. Unauthenticated user can register as Viewer
    new_email = f"new_student_{tag}@lpu.in"
    res_viewer = client.post(
        "/api/auth/register",
        json={"email": new_email, "password": "StudentPass@2026", "full_name": "New Student", "role": "Viewer"},
    )
    assert res_viewer.status_code == 200
    assert res_viewer.json()["role"] == "Viewer"

    # 2. Unauthenticated user cannot register as Admin
    res_unauth_admin = client.post(
        "/api/auth/register",
        json={"email": f"hacker_{tag}@lpu.in", "password": "AdminPass@2026", "full_name": "Rogue Admin", "role": "Admin"},
    )
    assert res_unauth_admin.status_code == 403

    # 3. Authenticated Admin CAN create a Coordinator
    admin_login = client.post(
        "/api/auth/login",
        json={"email": "admin@lpu.in", "password": "LpuAdmin@2026!"},
    )
    admin_cookie = admin_login.cookies.get(auth.SESSION_COOKIE_NAME)
    admin_csrf = admin_login.json()["csrf_token"]

    coord_email = f"new_coordinator_{tag}@lpu.in"
    res_coord = client.post(
        "/api/auth/register",
        json={"email": coord_email, "password": "CoordPass@2026", "full_name": "New Coordinator", "role": "Coordinator"},
        cookies={auth.SESSION_COOKIE_NAME: admin_cookie},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert res_coord.status_code == 200
    assert res_coord.json()["role"] == "Coordinator"


def test_login_page_renders():
    res = client.get("/login")
    assert res.status_code == 200
    assert "Welcome Back" in res.text
    assert "University Event Scheduling & Resource Optimization Matrix" in res.text
    assert "Institutional Email" in res.text


def test_unauthenticated_browser_redirects_to_login():
    res = client.get(
        "/",
        headers={"accept": "text/html,application/xhtml+xml", "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_authenticated_user_on_login_page_redirects_to_dashboard():
    # Login as Viewer
    login_res = client.post(
        "/api/auth/login",
        json={"email": "viewer@lpu.in", "password": "LpuViewer@2026!"},
    )
    session_cookie = login_res.cookies.get(auth.SESSION_COOKIE_NAME)

    # Access /login while authenticated
    res = client.get(
        "/login",
        cookies={auth.SESSION_COOKIE_NAME: session_cookie},
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert res.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Two-Factor Authentication Tests
# ---------------------------------------------------------------------------

def test_two_factor_auth_lifecycle():
    # Admin login with 2FA required
    res_login = client.post(
        "/api/auth/login",
        json={"email": "admin@lpu.in", "password": "LpuAdmin@2026!"},
        headers={"X-Require-2FA": "true"},
    )
    assert res_login.status_code == 200
    data = res_login.json()
    assert data["requires_2fa"] is True
    assert "ticket" in data
    assert "masked_email" in data
    assert "adm" in data["masked_email"]
    assert "demo_otp" in data
    ticket = data["ticket"]
    otp = data["demo_otp"]

    # 1. Test invalid OTP code
    bad_otp_res = client.post(
        "/api/auth/2fa/verify",
        json={"ticket": ticket, "otp_code": "000000"},
    )
    assert bad_otp_res.status_code == 400
    assert "Invalid verification code" in bad_otp_res.json()["detail"]

    # 2. Test valid OTP code completes authentication
    good_otp_res = client.post(
        "/api/auth/2fa/verify",
        json={"ticket": ticket, "otp_code": otp},
    )
    assert good_otp_res.status_code == 200
    verify_data = good_otp_res.json()
    assert verify_data["ok"] is True
    assert verify_data["user"]["role"] == "Admin"
    assert auth.SESSION_COOKIE_NAME in good_otp_res.cookies


def test_two_factor_resend_cooldown():
    res_login = client.post(
        "/api/auth/login",
        json={"email": "admin@lpu.in", "password": "LpuAdmin@2026!"},
        headers={"X-Require-2FA": "true"},
    )
    ticket = res_login.json()["ticket"]

    # Immediate resend should trigger 429 cooldown
    resend_res = client.post(
        "/api/auth/2fa/resend",
        json={"ticket": ticket},
    )
    assert resend_res.status_code == 429
    assert "Please wait" in resend_res.json()["detail"]


# ---------------------------------------------------------------------------
# Password Management Tests
# ---------------------------------------------------------------------------

def test_forgot_password_generic_response():
    # 1. Existing user
    res_exist = client.post(
        "/api/auth/password/forgot",
        json={"email": "coordinator@lpu.in"},
    )
    assert res_exist.status_code == 200
    msg_exist = res_exist.json()["message"]

    # 2. Non-existent user
    res_nonexist = client.post(
        "/api/auth/password/forgot",
        json={"email": "completely_fake_email_999@lpu.in"},
    )
    assert res_nonexist.status_code == 200
    msg_nonexist = res_nonexist.json()["message"]

    # Exactly identical generic response to prevent account enumeration
    assert msg_exist == msg_nonexist
    assert "If an institutional account exists" in msg_exist


def test_password_strength_and_reset_workflow():
    # 1. Request reset
    forgot_res = client.post(
        "/api/auth/password/forgot",
        json={"email": "viewer@lpu.in"},
    )
    data = forgot_res.json()
    ticket = data["ticket"]
    code = data["demo_reset_code"]

    # 2. Try weak password (too short) -> 400
    weak_res1 = client.post(
        "/api/auth/password/reset",
        json={"ticket": ticket, "code": code, "new_password": "short"},
    )
    assert weak_res1.status_code == 400
    assert "at least 8 characters" in weak_res1.json()["detail"]

    # 3. Try weak password (no special char) -> 400
    weak_res2 = client.post(
        "/api/auth/password/reset",
        json={"ticket": ticket, "code": code, "new_password": "SimplePassword123"},
    )
    assert weak_res2.status_code == 400
    assert "special character" in weak_res2.json()["detail"]

    # 4. Valid strong password reset
    strong_pass = "LpuSecure@2027#Viewer"
    reset_res = client.post(
        "/api/auth/password/reset",
        json={"ticket": ticket, "code": code, "new_password": strong_pass},
    )
    assert reset_res.status_code == 200
    assert "successfully reset" in reset_res.json()["message"]

    # 5. Verify new password allows login
    login_new = client.post(
        "/api/auth/login",
        json={"email": "viewer@lpu.in", "password": strong_pass},
    )
    assert login_new.status_code == 200

    # 6. Verify old password is now rejected
    login_old = client.post(
        "/api/auth/login",
        json={"email": "viewer@lpu.in", "password": "LpuViewer@2026!"},
    )
    assert login_old.status_code == 401


# ---------------------------------------------------------------------------
# CAPTCHA & SSO Status Tests
# ---------------------------------------------------------------------------

def test_captcha_challenge_generation_and_verification():
    res = client.get("/api/auth/captcha")
    assert res.status_code == 200
    data = res.json()
    assert "captcha_id" in data
    assert "question" in data
    assert "What is" in data["question"]


def test_sso_status_endpoint():
    res = client.get("/api/auth/sso-status")
    assert res.status_code == 200
    data = res.json()
    assert "google_enabled" in data
    assert "microsoft_enabled" in data
    # In test environment, unconfigured credentials must report False (no fake SSO)
    assert data["google_enabled"] is False
    assert data["microsoft_enabled"] is False


