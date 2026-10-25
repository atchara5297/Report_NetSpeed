"""
Emergency password reset for an admin account (use when nobody can log in).

Run ON THE SERVER, from the project folder:
    python3 reset_admin_password.py <username>

Reads the same MYSQL_* settings as app.py (from .env or environment variables).
The new password is typed hidden and stored as plain text (same as the original system).
"""
import os
import sys
import getpass

import pymysql

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 reset_admin_password.py <username>")
    username = sys.argv[1]

    pw1 = getpass.getpass("New password: ")
    pw2 = getpass.getpass("Confirm new password: ")
    pw1, pw2 = pw1.strip(), pw2.strip()
    if pw1 != pw2:
        sys.exit("Passwords do not match.")
    if len(pw1) < 6:
        sys.exit("Password must be at least 6 characters.")

    conn = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", "1qazxsw2"),
        database=os.environ.get("MYSQL_DATABASE", "network_monitoring"),
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM Admin WHERE username=%s", (username,))
            if not cur.fetchone():
                sys.exit(f'Admin "{username}" not found.')
            cur.execute("UPDATE Admin SET password=%s WHERE username=%s",
                        (pw1.strip(), username))
        conn.commit()
    finally:
        conn.close()
    print(f'Password for "{username}" updated.')


if __name__ == "__main__":
    main()
