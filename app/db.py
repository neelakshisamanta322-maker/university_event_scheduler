from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import os as _os
# Allow override via environment variable (e.g. /tmp/scheduler.db on Vercel serverless).
_db_env = _os.environ.get("DB_PATH")
DB_PATH = Path(_db_env) if _db_env else Path(__file__).resolve().parent.parent / "scheduler.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=20.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA cache_size = -64000")
    con.execute("PRAGMA temp_store = MEMORY")
    return con


def init_db() -> None:
    with connect() as con:
        # Check existing table columns and migrate if necessary
        cols_dept = [r["name"] for r in con.execute("PRAGMA table_info(departments)").fetchall()]
        if cols_dept and "code" not in cols_dept:
            con.execute("ALTER TABLE departments ADD COLUMN code TEXT")

        cols_venue = [r["name"] for r in con.execute("PRAGMA table_info(venues)").fetchall()]
        if cols_venue and "location" not in cols_venue:
            con.execute("ALTER TABLE venues ADD COLUMN location TEXT DEFAULT 'Academic Block'")

        cols_eq = [r["name"] for r in con.execute("PRAGMA table_info(equipment)").fetchall()]
        if cols_eq and "category" not in cols_eq:
            con.execute("ALTER TABLE equipment ADD COLUMN category TEXT DEFAULT 'AV'")

        cols_staff = [r["name"] for r in con.execute("PRAGMA table_info(staff)").fetchall()]
        if cols_staff and "department_id" not in cols_staff:
            con.execute("ALTER TABLE staff ADD COLUMN department_id INTEGER")
        if cols_staff and "role" not in cols_staff:
            con.execute("ALTER TABLE staff ADD COLUMN role TEXT DEFAULT 'Faculty Coordinator'")
        if cols_staff and "skills" not in cols_staff:
            con.execute("ALTER TABLE staff ADD COLUMN skills TEXT DEFAULT '[]'")

        cols_events = [r["name"] for r in con.execute("PRAGMA table_info(events)").fetchall()]
        if cols_events and "buffer_slots" not in cols_events:
            con.execute("ALTER TABLE events ADD COLUMN buffer_slots INTEGER NOT NULL DEFAULT 0")

        cols_runs = [r["name"] for r in con.execute("PRAGMA table_info(schedule_runs)").fetchall()]
        if cols_runs and "solver_type" not in cols_runs:
            con.execute("ALTER TABLE schedule_runs ADD COLUMN solver_type TEXT DEFAULT 'CSP-Heuristic'")

        cols_users = [r["name"] for r in con.execute("PRAGMA table_info(users)").fetchall()]
        if cols_users and "two_factor_enabled" not in cols_users:
            con.execute("ALTER TABLE users ADD COLUMN two_factor_enabled INTEGER DEFAULT 0")

        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                code TEXT
            );
            CREATE TABLE IF NOT EXISTS venues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                capacity INTEGER NOT NULL,
                venue_type TEXT NOT NULL,
                location TEXT DEFAULT 'Academic Block',
                available_slots TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS equipment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                quantity INTEGER NOT NULL,
                category TEXT DEFAULT 'AV'
            );
            CREATE TABLE IF NOT EXISTS staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                role TEXT DEFAULT 'Faculty Coordinator',
                skills TEXT DEFAULT '[]',
                available_slots TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                duration_slots INTEGER NOT NULL,
                expected_attendance INTEGER NOT NULL,
                required_venue_type TEXT,
                required_staff_ids TEXT NOT NULL,
                required_equipment TEXT NOT NULL,
                allowed_slots TEXT NOT NULL,
                preferred_venue_id INTEGER REFERENCES venues(id) ON DELETE SET NULL,
                preferred_start_hour INTEGER,
                priority TEXT NOT NULL DEFAULT 'normal',
                fixed_start_slot TEXT,
                buffer_slots INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS schedule_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                solver_type TEXT DEFAULT 'CSP-Heuristic',
                result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conflict_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES schedule_runs(id) ON DELETE CASCADE,
                event_id INTEGER NOT NULL,
                event_name TEXT NOT NULL,
                conflict_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                contender_event_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'Viewer',
                department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                two_factor_enabled INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_token TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf_token TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT NOT NULL,
                ip_address TEXT,
                user_agent TEXT
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                ip_address TEXT,
                attempt_time TEXT DEFAULT CURRENT_TIMESTAMP,
                success INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS otp_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                otp_code_hash TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT '2fa',
                ticket TEXT NOT NULL UNIQUE,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT NOT NULL,
                consumed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS captcha_challenges (
                id TEXT PRIMARY KEY,
                solution TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_department ON events(department_id);
            CREATE INDEX IF NOT EXISTS idx_events_venue ON events(preferred_venue_id);
            CREATE INDEX IF NOT EXISTS idx_staff_department ON staff(department_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, success, attempt_time);
            CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, success, attempt_time);
            CREATE INDEX IF NOT EXISTS idx_conflict_logs_run ON conflict_logs(run_id);
            CREATE INDEX IF NOT EXISTS idx_conflict_logs_event ON conflict_logs(event_id);
            CREATE INDEX IF NOT EXISTS idx_otp_codes_ticket ON otp_codes(ticket);
            CREATE INDEX IF NOT EXISTS idx_otp_codes_user ON otp_codes(user_id, expires_at);
            CREATE INDEX IF NOT EXISTS idx_captcha_expires ON captcha_challenges(expires_at);
            """
        )


def reset_all() -> None:
    with connect() as con:
        for table in [
            "conflict_logs",
            "schedule_runs",
            "events",
            "staff",
            "equipment",
            "venues",
            "departments",
        ]:
            con.execute(f"DELETE FROM {table}")
        con.execute("DELETE FROM sqlite_sequence")


def _j(value: Any) -> str:
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Department CRUD
# ---------------------------------------------------------------------------

def add_department(name: str, code: Optional[str] = None) -> int:
    with connect() as con:
        cur = con.execute("INSERT INTO departments(name, code) VALUES (?, ?)", (name, code or name[:4].upper()))
        return int(cur.lastrowid)


def update_department(dept_id: int, data: Dict[str, Any]) -> bool:
    with connect() as con:
        cur = con.execute(
            "UPDATE departments SET name = ?, code = ? WHERE id = ?",
            (data["name"], data.get("code"), dept_id),
        )
        return cur.rowcount > 0


def delete_department(dept_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM departments WHERE id = ?", (dept_id,))
        return cur.rowcount > 0


def get_department(dept_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Venue CRUD
# ---------------------------------------------------------------------------

def add_venue(data: Dict[str, Any]) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO venues(name, capacity, venue_type, location, available_slots)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                int(data["capacity"]),
                data.get("venue_type", "general"),
                data.get("location", "Academic Block"),
                _j(data.get("available_slots", [])),
            ),
        )
        return int(cur.lastrowid)


def update_venue(venue_id: int, data: Dict[str, Any]) -> bool:
    with connect() as con:
        cur = con.execute(
            """
            UPDATE venues
            SET name = ?, capacity = ?, venue_type = ?, location = ?, available_slots = ?
            WHERE id = ?
            """,
            (
                data["name"],
                int(data["capacity"]),
                data.get("venue_type", "general"),
                data.get("location", "Academic Block"),
                _j(data.get("available_slots", [])),
                venue_id,
            ),
        )
        return cur.rowcount > 0


def delete_venue(venue_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM venues WHERE id = ?", (venue_id,))
        return cur.rowcount > 0


def get_venue(venue_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM venues WHERE id = ?", (venue_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["available_slots"] = json.loads(d["available_slots"])
        return d


# ---------------------------------------------------------------------------
# Equipment CRUD
# ---------------------------------------------------------------------------

def add_equipment(data: Dict[str, Any]) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO equipment(name, quantity, category) VALUES (?, ?, ?)",
            (data["name"], int(data["quantity"]), data.get("category", "AV")),
        )
        return int(cur.lastrowid)


def update_equipment(eq_id: int, data: Dict[str, Any]) -> bool:
    with connect() as con:
        cur = con.execute(
            "UPDATE equipment SET name = ?, quantity = ?, category = ? WHERE id = ?",
            (data["name"], int(data["quantity"]), data.get("category", "AV"), eq_id),
        )
        return cur.rowcount > 0


def delete_equipment(eq_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM equipment WHERE id = ?", (eq_id,))
        return cur.rowcount > 0


def get_equipment(eq_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM equipment WHERE id = ?", (eq_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Staff CRUD
# ---------------------------------------------------------------------------

def add_staff(data: Dict[str, Any]) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO staff(name, department_id, role, skills, available_slots)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                data.get("department_id"),
                data.get("role", "Faculty Coordinator"),
                _j(data.get("skills", [])),
                _j(data.get("available_slots", [])),
            ),
        )
        return int(cur.lastrowid)


def update_staff(staff_id: int, data: Dict[str, Any]) -> bool:
    with connect() as con:
        cur = con.execute(
            """
            UPDATE staff
            SET name = ?, department_id = ?, role = ?, skills = ?, available_slots = ?
            WHERE id = ?
            """,
            (
                data["name"],
                data.get("department_id"),
                data.get("role", "Faculty Coordinator"),
                _j(data.get("skills", [])),
                _j(data.get("available_slots", [])),
                staff_id,
            ),
        )
        return cur.rowcount > 0


def delete_staff(staff_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM staff WHERE id = ?", (staff_id,))
        return cur.rowcount > 0


def get_staff(staff_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["skills"] = json.loads(d["skills"]) if d["skills"] else []
        d["available_slots"] = json.loads(d["available_slots"])
        return d


# ---------------------------------------------------------------------------
# Event CRUD
# ---------------------------------------------------------------------------

def add_event(data: Dict[str, Any]) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO events(
                name, department_id, duration_slots, expected_attendance,
                required_venue_type, required_staff_ids, required_equipment,
                allowed_slots, preferred_venue_id, preferred_start_hour, priority,
                fixed_start_slot, buffer_slots
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                int(data["department_id"]),
                int(data.get("duration_slots", 1)),
                int(data.get("expected_attendance", 1)),
                data.get("required_venue_type"),
                _j(data.get("required_staff_ids", [])),
                _j(data.get("required_equipment", {})),
                _j(data.get("allowed_slots", [])),
                data.get("preferred_venue_id"),
                data.get("preferred_start_hour"),
                data.get("priority", "normal"),
                data.get("fixed_start_slot"),
                int(data.get("buffer_slots", 0)),
            ),
        )
        return int(cur.lastrowid)


def update_event(event_id: int, data: Dict[str, Any]) -> bool:
    with connect() as con:
        cur = con.execute(
            """
            UPDATE events
            SET name = ?, department_id = ?, duration_slots = ?, expected_attendance = ?,
                required_venue_type = ?, required_staff_ids = ?, required_equipment = ?,
                allowed_slots = ?, preferred_venue_id = ?, preferred_start_hour = ?,
                priority = ?, fixed_start_slot = ?, buffer_slots = ?
            WHERE id = ?
            """,
            (
                data["name"],
                int(data["department_id"]),
                int(data.get("duration_slots", 1)),
                int(data.get("expected_attendance", 1)),
                data.get("required_venue_type"),
                _j(data.get("required_staff_ids", [])),
                _j(data.get("required_equipment", {})),
                _j(data.get("allowed_slots", [])),
                data.get("preferred_venue_id"),
                data.get("preferred_start_hour"),
                data.get("priority", "normal"),
                data.get("fixed_start_slot"),
                int(data.get("buffer_slots", 0)),
                event_id,
            ),
        )
        return cur.rowcount > 0


def delete_event(event_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM events WHERE id = ?", (event_id,))
        return cur.rowcount > 0


def get_event(event_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["required_staff_ids"] = json.loads(d["required_staff_ids"])
        raw_eq = json.loads(d["required_equipment"])
        d["required_equipment"] = {int(k): int(v) for k, v in raw_eq.items()}
        d["allowed_slots"] = json.loads(d["allowed_slots"])
        return d


# ---------------------------------------------------------------------------
# State Query & Deserialization
# ---------------------------------------------------------------------------

def _rows(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    with connect() as con:
        return [dict(r) for r in con.execute(query, params).fetchall()]


def get_state() -> Dict[str, List[Dict[str, Any]]]:
    with connect() as con:
        departments = [dict(r) for r in con.execute("SELECT * FROM departments ORDER BY id").fetchall()]
        venues = [dict(r) for r in con.execute("SELECT * FROM venues ORDER BY id").fetchall()]
        equipment = [dict(r) for r in con.execute("SELECT * FROM equipment ORDER BY id").fetchall()]
        staff = [dict(r) for r in con.execute("SELECT * FROM staff ORDER BY id").fetchall()]
        events = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id").fetchall()]

    for row in venues:
        row["available_slots"] = json.loads(row["available_slots"])
    for row in staff:
        row["skills"] = json.loads(row["skills"]) if row.get("skills") else []
        row["available_slots"] = json.loads(row["available_slots"])
    for row in events:
        row["required_staff_ids"] = json.loads(row["required_staff_ids"])
        raw_eq = json.loads(row["required_equipment"])
        row["required_equipment"] = {int(k): int(v) for k, v in raw_eq.items()}
        row["allowed_slots"] = json.loads(row["allowed_slots"])

    return {
        "departments": departments,
        "venues": venues,
        "equipment": equipment,
        "staff": staff,
        "events": events,
    }


# ---------------------------------------------------------------------------
# Schedule Runs & Conflict Logs
# ---------------------------------------------------------------------------

def save_schedule(result: Dict[str, Any], solver_type: str = "CSP-Heuristic") -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO schedule_runs(result_json, solver_type) VALUES (?, ?)",
            (_j(result), solver_type),
        )
        run_id = int(cur.lastrowid)

        # Save unresolved conflict logs
        for un in result.get("unresolved", []):
            for r in un.get("reasons", []):
                con.execute(
                    """
                    INSERT INTO conflict_logs(run_id, event_id, event_name, conflict_type, reason)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, un["event_id"], un["event_name"], "Unresolved", r),
                )
        return run_id


def latest_schedule() -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM schedule_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        result = json.loads(row["result_json"])
        result["run_id"] = row["id"]
        result["created_at"] = row["created_at"]
        result["solver_type"] = row["solver_type"]
        return result


def get_conflict_logs(run_id: Optional[int] = None) -> List[Dict[str, Any]]:
    with connect() as con:
        if run_id:
            query = "SELECT * FROM conflict_logs WHERE run_id = ? ORDER BY id DESC"
            params = (run_id,)
        else:
            query = "SELECT * FROM conflict_logs ORDER BY id DESC LIMIT 50"
            params = ()
        return [dict(r) for r in con.execute(query, params).fetchall()]


# ---------------------------------------------------------------------------
# User Authentication & Session Persistence
# ---------------------------------------------------------------------------

def create_user(
    email: str,
    password_hash: str,
    salt: str,
    full_name: str,
    role: str = "Viewer",
    department_id: Optional[int] = None,
) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO users(email, password_hash, salt, full_name, role, department_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (email.strip().lower(), password_hash, salt, full_name.strip(), role.capitalize(), department_id),
        )
        return int(cur.lastrowid)


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email.strip(),)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT id, email, full_name, role, department_id, created_at FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def create_session(
    user_id: int,
    session_token: str,
    csrf_token: str,
    expires_at: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO sessions(user_id, session_token, csrf_token, expires_at, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, session_token, csrf_token, expires_at, ip_address, user_agent),
        )
        return int(cur.lastrowid)


def get_session(session_token: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute(
            """
            SELECT s.*, u.email, u.full_name, u.role, u.department_id
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = ?
            """,
            (session_token,),
        ).fetchone()
        return dict(row) if row else None


def delete_session(session_token: str) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM sessions WHERE session_token = ?", (session_token,))
        return cur.rowcount > 0


def delete_user_sessions(user_id: int) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cur.rowcount > 0


def clean_expired_sessions() -> int:
    with connect() as con:
        cur = con.execute("DELETE FROM sessions WHERE expires_at < CURRENT_TIMESTAMP")
        return cur.rowcount


# ---------------------------------------------------------------------------
# Rate Limiting & Login Attempt Logs
# ---------------------------------------------------------------------------

def record_login_attempt(email: str, ip_address: Optional[str], success: bool) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO login_attempts(email, ip_address, success)
            VALUES (?, ?, ?)
            """,
            (email.strip().lower() if email else "unknown", ip_address or "unknown", 1 if success else 0),
        )


def get_recent_failed_attempts(email: str, ip_address: Optional[str], window_minutes: int = 15) -> int:
    with connect() as con:
        if email and email.strip():
            row = con.execute(
                f"""
                SELECT COUNT(*) AS failed_count
                FROM login_attempts
                WHERE LOWER(email) = LOWER(?)
                  AND success = 0
                  AND attempt_time >= datetime('now', '-{window_minutes} minutes')
                """,
                (email.strip(),),
            ).fetchone()
        else:
            row = con.execute(
                f"""
                SELECT COUNT(*) AS failed_count
                FROM login_attempts
                WHERE ip_address = ?
                  AND success = 0
                  AND attempt_time >= datetime('now', '-{window_minutes} minutes')
                """,
                (ip_address or "unknown",),
            ).fetchone()
        return int(row["failed_count"]) if row else 0


def clear_failed_attempts(email: str, ip_address: Optional[str]) -> None:
    with connect() as con:
        if email and email.strip():
            con.execute(
                "DELETE FROM login_attempts WHERE LOWER(email) = LOWER(?)",
                (email.strip(),),
            )
        if ip_address and ip_address not in ("testclient", "unknown", "127.0.0.1"):
            con.execute(
                "DELETE FROM login_attempts WHERE ip_address = ?",
                (ip_address,),
            )



# ---------------------------------------------------------------------------
# OTP Codes Management (Two-Factor Authentication & Password Reset)
# ---------------------------------------------------------------------------

def create_otp(
    user_id: int,
    otp_code_hash: str,
    ticket: str,
    purpose: str = "2fa",
    duration_minutes: int = 10,
    max_attempts: int = 3,
) -> int:
    with connect() as con:
        # Invalidate any previous unconsumed OTPs for this user and purpose
        con.execute(
            "UPDATE otp_codes SET consumed = 1 WHERE user_id = ? AND purpose = ? AND consumed = 0",
            (user_id, purpose),
        )
        cur = con.execute(
            f"""
            INSERT INTO otp_codes(user_id, otp_code_hash, ticket, purpose, max_attempts, expires_at)
            VALUES (?, ?, ?, ?, ?, datetime('now', '+{duration_minutes} minutes'))
            """,
            (user_id, otp_code_hash, ticket, purpose, max_attempts),
        )
        return int(cur.lastrowid)


def get_otp_by_ticket(ticket: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute(
            """
            SELECT o.*, u.email, u.full_name, u.role
            FROM otp_codes o
            JOIN users u ON o.user_id = u.id
            WHERE o.ticket = ?
            """,
            (ticket,),
        ).fetchone()
        return dict(row) if row else None


def increment_otp_attempts(ticket: str) -> int:
    with connect() as con:
        con.execute(
            "UPDATE otp_codes SET attempts = attempts + 1 WHERE ticket = ?",
            (ticket,),
        )
        row = con.execute("SELECT attempts, max_attempts FROM otp_codes WHERE ticket = ?", (ticket,)).fetchone()
        if not row:
            return 0
        if row["attempts"] >= row["max_attempts"]:
            con.execute("UPDATE otp_codes SET consumed = 1 WHERE ticket = ?", (ticket,))
        return int(row["attempts"])


def consume_otp(ticket: str) -> bool:
    with connect() as con:
        cur = con.execute(
            "UPDATE otp_codes SET consumed = 1 WHERE ticket = ? AND consumed = 0",
            (ticket,),
        )
        return cur.rowcount > 0


def get_latest_otp_created_at(ticket: str) -> Optional[str]:
    with connect() as con:
        row = con.execute("SELECT created_at FROM otp_codes WHERE ticket = ?", (ticket,)).fetchone()
        return row["created_at"] if row else None


# ---------------------------------------------------------------------------
# CAPTCHA Challenge Management
# ---------------------------------------------------------------------------

def create_captcha(challenge_id: str, solution: str, duration_minutes: int = 5) -> None:
    with connect() as con:
        con.execute(
            f"""
            INSERT OR REPLACE INTO captcha_challenges(id, solution, expires_at)
            VALUES (?, ?, datetime('now', '+{duration_minutes} minutes'))
            """,
            (challenge_id, solution.strip()),
        )


def verify_captcha(challenge_id: str, user_solution: str) -> bool:
    with connect() as con:
        row = con.execute(
            """
            SELECT solution, expires_at FROM captcha_challenges
            WHERE id = ? AND expires_at >= datetime('now')
            """,
            (challenge_id,),
        ).fetchone()
        if not row:
            return False
        # Delete challenge after single verification attempt to prevent replay
        con.execute("DELETE FROM captcha_challenges WHERE id = ?", (challenge_id,))
        return row["solution"].strip().lower() == user_solution.strip().lower()


def update_user_password(user_id: int, password_hash: str, salt: str) -> bool:
    with connect() as con:
        cur = con.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
            (password_hash, salt, user_id),
        )
        return cur.rowcount > 0


def set_user_2fa(user_id: int, enabled: bool) -> bool:
    with connect() as con:
        cur = con.execute(
            "UPDATE users SET two_factor_enabled = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        return cur.rowcount > 0


