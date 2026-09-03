"""
Bootstrap a staff/admin account (WEB P5).

There is no self-service staff signup (unlike patients, who register via
OTP) -- someone has to exist to call the ADMIN-only
POST /api/auth/staff/accounts endpoint in the first place. This script is
that bootstrap step: a one-time, operator-run CLI, the same category of
tool as scripts/provision_local_db.sh, not something the application
calls itself.

Uses app.services.staff_management.create_staff_account directly (the
exact same function the ADMIN-only API endpoint calls), so a bootstrapped
account is created identically to one an admin creates later through the
UI -- no separate/duplicated account-creation logic.

Usage:
    python scripts/create_staff_account.py --username admin --role ADMIN
    (prompts for a password, not read from argv, so it never ends up in
    shell history or a process list)

Exit codes:
    0   account created
    1   username already exists, or bad input
    2   could not connect to the database
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import DATABASE_URL
from app.services.exceptions import UsernameAlreadyExists
from app.services.staff_management import create_staff_account


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=["ADMIN", "STAFF"])
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        return 1

    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1

    try:
        conn = psycopg.connect(DATABASE_URL)
    except psycopg.OperationalError as exc:
        print(f"Could not connect to the database: {exc}", file=sys.stderr)
        return 2

    with conn:
        with conn.cursor() as cur:
            try:
                account = create_staff_account(cur, args.username, password, args.role)
            except UsernameAlreadyExists:
                print(f"Username {args.username!r} already exists.", file=sys.stderr)
                return 1

    print(f"Created {account['role']} account {account['username']!r} (id={account['id']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
