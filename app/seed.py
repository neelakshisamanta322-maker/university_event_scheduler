'''Seed script for secure admin account creation.

Usage (run once in a protected environment):
    python -m app.seed

The script generates a strong random password for each admin email,
stores the Argon2id‑hashed password in the database, and prints the
plaintext password only to the console (do NOT commit the output).

Admin emails are hard‑coded as per the allow‑list.  The script is
idempotent – running it again will not create duplicate accounts.
''' 

import os
import sys
import secrets
from pathlib import Path

# Ensure the project root is on PYTHONPATH so "app" can be imported.
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from app import db, auth, config

ADMIN_ALLOWLIST = config.ADMIN_ALLOWLIST

def generate_strong_password() -> str:
    """Generate a 12‑character password that satisfies the strength policy.
    Uses letters, digits and special characters.
    """
    specials = "!@#$%^&*()-_+="
    alphabet = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        + specials
    )
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(12))
        ok, _ = auth.validate_password_strength(pwd)
        if ok:
            return pwd

def main():
    # Ensure tables exist
    db.init_db()
    existing = {u["email"].lower() for u in db.list_users()}
    created = []
    for email in ADMIN_ALLOWLIST:
        if email.lower() in existing:
            print(f"[INFO] Admin account already exists: {email}")
            continue
        pwd = generate_strong_password()
        pw_hash, salt = auth.hash_password(pwd)
        db.create_user(
            email=email,
            password_hash=pw_hash,
            salt=salt,
            full_name="System Administrator",
            role="Admin",
            is_active=1,
        )
        created.append((email, pwd))
        print(f"[CREATED] {email} -> password: {pwd}")
    if not created:
        print("All admin accounts already exist. No new accounts created.")

if __name__ == "__main__":
    main()
