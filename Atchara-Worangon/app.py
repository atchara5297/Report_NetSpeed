import subprocess
import socket
import threading
import time
from collections import OrderedDict
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask.json.provider import DefaultJSONProvider
import os
import re
import ipaddress
import hmac
from datetime import datetime, date, timedelta, timezone
import pymysql
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Thailand timezone (UTC+7) ──────────────────────────────────────────────
# The host/DB server may be running in a different timezone (commonly UTC),
# which was making every timestamp written to MySQL 7 hours behind Thai time.
# All timestamps saved to the database go through this helper instead of the
# bare `datetime.now()` so they always reflect Asia/Bangkok time.
TH_TZ = timezone(timedelta(hours=7))


def now_th() -> datetime:
    """Current date/time in Thailand (UTC+7), as a naive datetime for MySQL."""
    return datetime.now(TH_TZ).replace(tzinfo=None)


# ── Flask/Werkzeug request-log timestamps in Thai time ─────────────────────
# The console line  127.0.0.1 - - [11/Oct/2026 14:16:24] "GET / ..."  is
# printed by Werkzeug using the *machine's* local clock/timezone. Override
# just the timestamp so the console shows Thai time (UTC+7) too, regardless
# of the host timezone. Works on Windows and Linux (no time.tzset needed).
try:
    from werkzeug.serving import WSGIRequestHandler

    def _log_date_time_string_th(self):
        n = now_th()
        return "%02d/%3s/%04d %02d:%02d:%02d" % (
            n.day, self.monthname[n.month], n.year, n.hour, n.minute, n.second)

    WSGIRequestHandler.log_date_time_string = _log_date_time_string_th
except Exception:
    pass


# ============================================================
# DEVICE ONLINE / OFFLINE MONITOR
# ============================================================
# Monitor network devices BY PINGING FROM MAIN ROUTER.
#
# Flask Server
#     |
#     | SSH (only to Main Router)
#     v
# Main Router: 192.168.155.129
#     |
#     +-- ping 192.168.99.2    -> Core Switch
#     +-- ping 192.168.99.3    -> Switch01
#     +-- ping 192.168.99.66   -> Switch02
#     +-- ping 192.168.99.130  -> Switch03
#     +-- ping 192.168.99.194  -> Switch04
#
# Router itself is also checked by:
#     Router -> ping 192.168.155.129
#
# Router is accessed by Telnet without username/password.
# The switches do NOT need SSH credentials.
#
# Result is saved to:
#     Device.status
#     Device.last_updated
# ============================================================

try:
    from netmiko import ConnectHandler
except ImportError:
    ConnectHandler = None


DEVICE_MONITOR_INTERVAL = 30

ROUTER_SSH_HOST = "192.168.155.129"
# Router has NO username/password authentication.
# The Router VTY lines must allow unauthenticated Telnet (no login).
ROUTER_TELNET_HOST = "192.168.155.129"

# Static fallback, only used if the DB can't be reached when a cycle starts.
MONITOR_DEVICES = {
    "Main Router": "192.168.155.129",
    "Core Switch": "192.168.99.2",
    "Switch01": "192.168.99.3",
    "Switch02": "192.168.99.66",
    "Switch03": "192.168.99.130",
    "Switch04": "192.168.99.194",
}


def _load_monitor_devices():
    """
    Build the ping list from the Device table instead of the hardcoded dict
    above, so any Router / Core Device / Access Device added later from the
    Devices page (a new switch, etc.) is automatically picked up by the next
    ping cycle -- no code change needed. New devices already default to
    Offline until the router successfully pings their IP, so they stay
    Offline for as long as they're unreachable (no IP configured yet, not
    wired up in eve.ng, etc.) and flip Online the first time a real reply
    comes back.
    """
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT device_name, ip_address FROM Device "
            "WHERE device_type IN ('Router','Core Device','Access Device') "
            "AND ip_address IS NOT NULL AND ip_address <> ''"
        ).fetchall()
        conn.close()
        devices = {r["device_name"]: r["ip_address"].strip() for r in rows if r["ip_address"]}
        return devices or dict(MONITOR_DEVICES)
    except Exception as exc:
        print(f"[DEVICE MONITOR] Could not load device list from DB ({exc}); using static fallback.")
        return dict(MONITOR_DEVICES)


def _router_ping(router_conn, ip_address):
    """
    Ask Main Router to ping the target IP.

    Returns:
        True  -> Router received at least one ICMP reply
        False -> Router received no reply / ping failed
    """

    try:
        output = router_conn.send_command(
            f"ping {ip_address}",
            read_timeout=15,
            cmd_verify=False,
        )

        # Cisco IOS normally prints:
        # Success rate is 100 percent (5/5)
        # or another percentage if some packets replied.
        match = re.search(
            r"Success rate is\s+(\d+)\s+percent",
            output,
            re.IGNORECASE,
        )

        if match:
            return int(match.group(1)) > 0

        # Fallback for IOS output that only exposes ! / .
        exclamation_count = output.count("!")
        return exclamation_count > 0

    except Exception as exc:
        print(
            f"[ROUTER PING] {ip_address} -> ERROR ({exc})"
        )
        return False


def _save_device_status(device_name, ip_address, status):
    """
    Save Online/Offline status to Device table.

    FIX: match by ip_address instead of device_name.
    MONITOR_DEVICES keys (e.g. "Core Switch") do not necessarily match the
    actual device_name values stored in the Device table (which may be
    "Switch01", etc., or any custom name set from the Devices page), which
    was causing every UPDATE to silently match 0 rows ("NOT FOUND" in the
    logs) and the dashboard's Online/Offline column to never change.
    ip_address is UNIQUE in the schema and is exactly what the router pings,
    so matching on it works regardless of how a device happens to be named.

    FIX 2: match using TRIM() on both sides of the comparison.
    Some ip_address rows in the live database carry invisible trailing
    characters (stray spaces, \r, \n) picked up from manual edits in
    phpMyAdmin. MySQL's default comparison only ignores trailing plain
    spaces, not \r/\n, so "192.168.99.3\r\n" != "192.168.99.3" and the
    UPDATE silently matched 0 rows even though the value "looked" identical
    on screen. Comparing TRIM(ip_address) = TRIM(%s) makes the match robust
    regardless of what's stored, without requiring a manual data cleanup.

    NOTE: last_updated is NOT touched here. That column reflects when a
    device was added or edited from the Devices page, not when it was last
    pinged - the ping monitor runs every 30 seconds and would otherwise
    overwrite that value constantly.

    last_checked IS touched here (every cycle, regardless of Online/Offline
    result), since that column exists specifically to record when the
    monitor last actually pinged the device.
    """

    conn = None

    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE Device
            SET status = %s, last_checked = %s
            WHERE TRIM(REPLACE(REPLACE(ip_address, '\r', ''), '\n', '')) = TRIM(%s)
            """,
            (status, now_th().strftime("%Y-%m-%d %H:%M:%S"), ip_address),
        )

        affected = cur.rowcount
        conn.commit()
        cur.close()

        if affected == 0:
            print(
                f"[DEVICE MONITOR][DB] "
                f"{device_name} ({ip_address}) -> NOT FOUND "
                f"(no Device row with this ip_address)"
            )
        else:
            print(
                f"[DEVICE MONITOR] "
                f"{device_name:<12} "
                f"{ip_address:<18} -> {status}"
            )

        return True

    except Exception as exc:
        print(
            f"[DEVICE MONITOR][DB ERROR] "
            f"{device_name} ({ip_address}) -> {exc}"
        )

        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        return False

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def device_status_monitor():
    """
    Background monitor.

    Connect to Main Router using Telnet without username/password.
    Ask Router to ping all monitored devices.
    Save Online/Offline into MySQL.
    """

    if ConnectHandler is None:
        print(
            "[DEVICE MONITOR] ERROR: Netmiko is not installed."
        )
        return

    while True:

        router_conn = None
        cycle_start = time.time()

        print()
        print("=" * 70)
        print("[DEVICE MONITOR] ROUTER PING CHECK")
        print("=" * 70)

        try:
            # --------------------------------------------------------
            # Connect ONLY to Main Router
            # --------------------------------------------------------
            router_device = {
                "device_type": "cisco_ios_telnet",
                "host": ROUTER_TELNET_HOST,
                "username": "",
                "password": "",
                "conn_timeout": 10,
                "auth_timeout": 10,
                "banner_timeout": 15,
                "fast_cli": False,
            }

            print(
                f"[DEVICE MONITOR] Connecting to Router "
                f"{ROUTER_TELNET_HOST}..."
            )

            router_conn = ConnectHandler(**router_device)

            print(
                "[DEVICE MONITOR] Router Telnet connection: OK"
            )

            # --------------------------------------------------------
            # Router pings every target -- loaded fresh from the DB each
            # cycle so newly added switches are included automatically.
            # --------------------------------------------------------
            monitor_devices = _load_monitor_devices()
            for device_name, ip_address in monitor_devices.items():

                online = _router_ping(
                    router_conn,
                    ip_address,
                )

                status = "Online" if online else "Offline"

                _save_device_status(
                    device_name,
                    ip_address,
                    status,
                )

            print(
                "[DEVICE MONITOR] Router ping cycle completed."
            )

        except Exception as exc:

            print(
                f"[DEVICE MONITOR][ROUTER TELNET ERROR] {exc}"
            )

            # If Router SSH itself is unavailable,
            # do NOT blindly mark every device Offline.
            # The database keeps the previous status.
            print(
                "[DEVICE MONITOR] Previous DB statuses "
                "are kept because Router could not be reached."
            )

        finally:

            if router_conn:
                try:
                    router_conn.disconnect()
                except Exception:
                    pass

        elapsed = time.time() - cycle_start

        print(
            f"[DEVICE MONITOR] Cycle time: {elapsed:.1f} seconds"
        )
        print(
            f"[DEVICE MONITOR] Sleeping: "
            f"{DEVICE_MONITOR_INTERVAL} seconds"
        )
        print("=" * 70)
        print()

        time.sleep(DEVICE_MONITOR_INTERVAL)


def start_device_status_monitor():
    """
    Start exactly one background device monitor thread.
    """

    if getattr(
        start_device_status_monitor,
        "_started",
        False,
    ):
        return

    start_device_status_monitor._started = True

    thread = threading.Thread(
        target=device_status_monitor,
        name="device-status-monitor",
        daemon=True,
    )

    thread.start()

class ThaiJSONProvider(DefaultJSONProvider):
    """
    By default Flask's jsonify() serializes datetime/date objects as an
    RFC 822 string (e.g. "Tue, 25 Aug 2026 08:54:03 GMT") which does not
    match the site-wide "YYYY-MM-DD HH:MM:SS" time display format and also
    silently re-labels every already-Thai-local timestamp as GMT.
    Any endpoint that returns a raw DB row (dict(r) from pymysql, whose
    DATETIME/DATE columns come back as datetime.datetime/datetime.date
    objects) goes through this instead, so every timestamp in every JSON
    response - and therefore every page - renders as "YYYY-MM-DD HH:MM:SS"
    (or "YYYY-MM-DD" for date-only columns) consistently.
    """
    def default(self, o):
        if isinstance(o, datetime):
            return o.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(o, date):
            return o.strftime("%Y-%m-%d")
        return super().default(o)


app = Flask(__name__)
app.json = ThaiJSONProvider(app)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "network-monitor-secret-key-2024")

# Password recovery from the login page. Disabled unless RECOVERY_KEY in .env is
# at least 12 characters. Anyone who knows this key can reset any admin password,
# so treat it like a root password.
RECOVERY_KEY = os.environ.get("RECOVERY_KEY", "").strip()
RECOVERY_ENABLED = len(RECOVERY_KEY) >= 12
RECOVERY_MAX_FAILS = 5          # wrong attempts per IP ...
RECOVERY_LOCK_SECONDS = 15 * 60  # ... then locked for 15 minutes
_recovery_fails = {}            # ip -> {"count": int, "locked_until": float}

MYSQL_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", "1qazxsw2"),
    "database": os.environ.get("MYSQL_DATABASE", "network_monitoring"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": False,
}


# ─── Database Helpers ──────────────────────────────────────────────────────────

def get_db():
    raw = pymysql.connect(**MYSQL_CONFIG)
    # Force this connection's session timezone to Thailand (UTC+7) so that
    # any SQL-side NOW()/CURRENT_TIMESTAMP defaults line up with Thai time,
    # regardless of what timezone the MySQL server itself is set to.
    with raw.cursor() as _tzcur:
        _tzcur.execute("SET time_zone = '+07:00'")
    return DBConnection(raw)

class DBConnection:
    """Small SQLite-like compatibility wrapper over mysql-connector for the existing app routes."""
    def __init__(self, raw):
        self.raw = raw
        self.cur = raw.cursor()

    def cursor(self):
        return self.cur

    def rollback(self):
        self.raw.rollback()

    def execute(self, sql, params=()):
        self.cur.execute(sql, params)
        return self.cur

    def executemany(self, sql, params):
        self.cur.executemany(sql, params)
        return self.cur

    def executescript(self, script):
        for statement in [s.strip() for s in script.split(";") if s.strip()]:
            self.cur.execute(statement)

    def commit(self):
        self.raw.commit()

    def close(self):
        try:
            self.cur.close()
        except Exception:
            pass
        self.raw.close()



def init_db():
    conn = get_db()
    cur = conn.cur

    cur.execute("""
        CREATE TABLE IF NOT EXISTS Admin (
            admin_id INT PRIMARY KEY AUTO_INCREMENT,
            username VARCHAR(50) NOT NULL UNIQUE,
            password VARCHAR(255) NOT NULL,
            is_super TINYINT(1) NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration for existing databases: add Admin.is_super, and make sure
    # exactly the first admin becomes Super Admin if none exists yet.
    cur.execute("SHOW COLUMNS FROM Admin LIKE 'is_super'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE Admin ADD COLUMN is_super TINYINT(1) NOT NULL DEFAULT 0")
    cur.execute("SELECT COUNT(*) AS n FROM Admin WHERE is_super=1")
    if cur.fetchone()["n"] == 0:
        cur.execute("SELECT MIN(admin_id) AS m FROM Admin")
        first = cur.fetchone()["m"]
        if first is not None:
            cur.execute("UPDATE Admin SET is_super=1 WHERE admin_id=%s", (first,))

    cur.execute("""
        CREATE TABLE IF NOT EXISTS Category (
            category_id INT PRIMARY KEY AUTO_INCREMENT,
            category_name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS Device (
            device_id INT PRIMARY KEY AUTO_INCREMENT,
            category_id INT NOT NULL,
            device_name VARCHAR(100) NOT NULL,
            device_type VARCHAR(50) NOT NULL,
            ip_address VARCHAR(45) NOT NULL UNIQUE,
            status VARCHAR(20) NOT NULL DEFAULT 'Online',
            username_device VARCHAR(100) DEFAULT NULL,
            password_device VARCHAR(255) DEFAULT NULL,
            location VARCHAR(100),
            last_updated DATETIME,
            last_checked DATETIME,
            FOREIGN KEY (category_id) REFERENCES Category(category_id)
        )
    """)

    # Existing installs created before `last_checked` was added won't have
    # the column yet (CREATE TABLE IF NOT EXISTS above is a no-op on them).
    cur.execute("""
        SELECT COUNT(*) AS n FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'Device' AND COLUMN_NAME = 'last_checked'
    """)
    if cur.fetchone()["n"] == 0:
        cur.execute("ALTER TABLE Device ADD COLUMN last_checked DATETIME NULL")

    # `connected_to` powers the interactive network topology on the Devices
    # page (which device this one plugs into, e.g. Switch02 -> Core Switch).
    # Self-referencing FK, nullable (root devices like Main Router have none).
    cur.execute("""
        SELECT COUNT(*) AS n FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'Device' AND COLUMN_NAME = 'connected_to'
    """)
    if cur.fetchone()["n"] == 0:
        cur.execute("ALTER TABLE Device ADD COLUMN connected_to INT NULL")
        cur.execute("""
            SELECT COUNT(*) AS n FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'Device' AND CONSTRAINT_NAME = 'fk_device_connected_to'
        """)
        if cur.fetchone()["n"] == 0:
            cur.execute("""
                ALTER TABLE Device
                ADD CONSTRAINT fk_device_connected_to
                FOREIGN KEY (connected_to) REFERENCES Device(device_id) ON DELETE SET NULL
            """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ThroughputSpeed (
            throughput_speed_id BIGINT PRIMARY KEY AUTO_INCREMENT,
            device_id INT NULL,
            throughput_mbps DECIMAL(12,2) NOT NULL,
            recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_device_time (device_id, recorded_at),
            FOREIGN KEY (device_id) REFERENCES Device(device_id) ON DELETE SET NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ActivityLog (
            log_id INT PRIMARY KEY AUTO_INCREMENT,
            admin_id INT NULL,
            device_id INT NULL,
            action_type VARCHAR(50) NOT NULL,
            action_detail TEXT,
            date_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES Admin(admin_id) ON DELETE SET NULL,
            FOREIGN KEY (device_id) REFERENCES Device(device_id) ON DELETE SET NULL
        )
    """)

    # Migration for existing databases: deleting an admin must KEEP that admin's
    # history. ActivityLog.admin_id becomes nullable with ON DELETE SET NULL, so
    # the log rows stay and the UI shows them as "(deleted user)".
    cur.execute("""
        SELECT rc.CONSTRAINT_NAME AS fk_name, rc.DELETE_RULE AS fk_rule
        FROM information_schema.REFERENTIAL_CONSTRAINTS rc
        JOIN information_schema.KEY_COLUMN_USAGE k
          ON k.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
         AND k.CONSTRAINT_NAME   = rc.CONSTRAINT_NAME
         AND k.TABLE_NAME        = rc.TABLE_NAME
        WHERE rc.CONSTRAINT_SCHEMA = DATABASE()
          AND rc.TABLE_NAME = 'ActivityLog'
          AND rc.REFERENCED_TABLE_NAME = 'Admin'
          AND k.COLUMN_NAME = 'admin_id'
        LIMIT 1
    """)
    fk = cur.fetchone()
    if not fk or str(fk["fk_rule"]).upper() != "SET NULL":
        if fk:
            cur.execute(f"ALTER TABLE ActivityLog DROP FOREIGN KEY `{fk['fk_name']}`")
        cur.execute("ALTER TABLE ActivityLog MODIFY admin_id INT NULL")
        cur.execute("ALTER TABLE ActivityLog ADD CONSTRAINT fk_activitylog_admin "
                    "FOREIGN KEY (admin_id) REFERENCES Admin(admin_id) ON DELETE SET NULL")

    cur.execute("SELECT COUNT(*) AS n FROM Admin")
    if cur.fetchone()["n"] == 0:
        cur.execute(
            "INSERT INTO Admin (username, password, is_super) VALUES (%s, %s, 1)",
            ("admin", "admin")
        )

    cur.execute("SELECT COUNT(*) AS n FROM Category")
    if cur.fetchone()["n"] == 0:
        # Matches the Category rows in the current `network_monitoring` database
        # exactly (the "Server" category that used to ship here was removed
        # from the live database, so it is no longer seeded on fresh installs).
        cats = [
            ("Router", "Main routing device connecting to the internet"),
            ("Switch", "Core or distribution layer switch"),
        ]
        cur.executemany(
            "INSERT INTO Category (category_name, description) VALUES (%s, %s)", cats
        )

    # Seed the actual lab devices once. No fake throughput data is generated.
    # These match the real `network_monitoring` database exactly.
    cur.execute("SELECT COUNT(*) AS n FROM Device")
    if cur.fetchone()["n"] == 0:
        cur.execute("SELECT category_id, category_name FROM Category")
        cat_rows = {r["category_name"]: r["category_id"] for r in cur.fetchall()}
        switch_id = cat_rows["Switch"]
        router_id = cat_rows["Router"]

        # Match the device layout used by the current lab database
        # (device_name/device_type match the live `network_monitoring` DB).
        # "parent" is the device_name this row physically connects to
        # (feeds `connected_to`), used to build the topology tree on the
        # Devices page. Main Router is the root (no parent).
        devices = [
            (router_id, "Main Router", "Core Device", "192.168.155.129", "Online", "Server Room", None),
            (switch_id, "Core Switch", "Core Device", "192.168.99.2", "Online", "Server Room", "Main Router"),
            (switch_id, "Switch01", "Access Device", "192.168.99.3", "Online", "Floor 1", "Core Switch"),
            (switch_id, "Switch02", "Access Device", "192.168.99.66", "Online", "Floor 2", "Core Switch"),
            (switch_id, "Switch03", "Access Device", "192.168.99.130", "Online", "Floor 3", "Core Switch"),
            (switch_id, "Switch04", "Access Device", "192.168.99.194", "Online", "Floor 4", "Core Switch"),
        ]
        now = now_th()
        name_to_id = {}
        for cid, name, dtype, ip, status, loc, parent_name in devices:
            parent_id = name_to_id.get(parent_name)
            cur.execute("""
                INSERT INTO Device
                (category_id, device_name, device_type, ip_address, status, last_updated, last_checked, location, connected_to)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (cid, name, dtype, ip, status, now, now, loc, parent_id))
            name_to_id[name] = cur.lastrowid

    # One-time cleanup: strip any invisible trailing whitespace/CR/LF that
    # may have crept into ip_address values via manual phpMyAdmin edits.
    # This is what was causing the device-status monitor's UPDATE to match
    # 0 rows ("NOT FOUND") for some devices even though the values looked
    # identical on screen. Safe to run on every startup - it's a no-op once
    # the data is clean, and ip_address stays UNIQUE throughout.
    cur.execute("""
        UPDATE Device
        SET ip_address = TRIM(REPLACE(REPLACE(ip_address, '\r', ''), '\n', ''))
        WHERE ip_address <> TRIM(REPLACE(REPLACE(ip_address, '\r', ''), '\n', ''))
    """)

    # Best-effort backfill of connected_to for rows left NULL by the
    # connected_to ALTER above (e.g. an existing database upgraded from an
    # older version of this app). Matches the known lab naming convention
    # only; never overwrites a value someone has already set manually.
    cur.execute("""
        UPDATE Device c
        JOIN Device p ON p.device_name = 'Main Router'
        SET c.connected_to = p.device_id
        WHERE c.device_name = 'Core Switch' AND c.connected_to IS NULL
    """)
    cur.execute("""
        UPDATE Device c
        JOIN Device p ON p.device_name = 'Core Switch'
        SET c.connected_to = p.device_id
        WHERE c.device_name IN ('Switch01','Switch02','Switch03','Switch04') AND c.connected_to IS NULL
    """)

    # One-time correction: an access switch (Switch01-04) that was wired
    # directly under Main Router instead of Core Switch - this is a data
    # mistake, not a valid topology (Main Router only ever has Core Switch
    # as a child; every access switch hangs off Core Switch). This was
    # showing up on the Devices/Dashboard topology as Switch01 sitting
    # beside Core Switch instead of below it. Re-parents it to Core Switch
    # every startup; harmless no-op once the data is correct.
    cur.execute("""
        UPDATE Device c
        JOIN Device root ON root.device_id = c.connected_to AND root.device_name = 'Main Router'
        JOIN Device core ON core.device_name = 'Core Switch'
        SET c.connected_to = core.device_id
        WHERE c.device_name IN ('Switch01','Switch02','Switch03','Switch04')
    """)

    # Location cleanup: old values "Network Room" / "Internet Gateway" -> "Server Room"
    # (Core Switch and the Router share one room). Idempotent; no-op once the data is already migrated.
    cur.execute("UPDATE Device SET location = 'Server Room' WHERE location IN ('Network Room', 'Internet Gateway')")

    conn.commit()
    cur.close()
    conn.close()


def add_activity_log(username, action_type, target_name, action_detail, target_type="System", device_id=None):
    """
    Write one row to ActivityLog.

    NOTE: the real `network_monitoring` database's ActivityLog table only has
    (admin_id, device_id, action_type, action_detail, date_time) - there is
    no target_type/target_name column. target_name/target_type are kept as
    parameters for caller compatibility, but they are not written as separate
    columns; callers already fold the target's name into action_detail.
    """
    conn = get_db()
    cur = conn.cur
    cur.execute(
        "SELECT admin_id FROM Admin WHERE username=%s", (username,)
    )
    admin = cur.fetchone()
    admin_id = admin["admin_id"] if admin else 1
    cur.execute(
        "INSERT INTO ActivityLog (admin_id, device_id, action_type, action_detail, date_time) "
        "VALUES (%s,%s,%s,%s,%s)",
        (admin_id, device_id, action_type, action_detail,
         now_th().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    cur.close()
    conn.close()


# ─── IP Interface Data ─────────────────────────────────────────────────────────

def _switch_ports(status="up", protocol="up"):
    """Generate clean 4x4 GigabitEthernet port list (16 ports, no duplicates)."""
    ports = []
    for g in range(4):
        for p in range(4):
            ports.append({
                "interface":  f"GigabitEthernet{g}/{p}",
                "ip_address": "unassigned",
                "ok":         "YES",
                "method":     "unset",
                "status":     status,
                "protocol":   protocol,
            })
    return ports


def _router_interfaces(ip: str, status: str = "Online"):
    """Router interfaces — same pattern as Main Router lab topology."""
    link_st = "up" if status == "Online" else "administratively down"
    link_pr = "up" if status == "Online" else "down"
    return [
        {"interface": "GigabitEthernet0/0",    "ip_address": ip,           "ok": "YES", "method": "NVRAM",  "status": link_st,                "protocol": link_pr},
        {"interface": "GigabitEthernet0/1",    "ip_address": "unassigned", "ok": "YES", "method": "NVRAM", "status": link_st,                "protocol": link_pr},
        {"interface": "GigabitEthernet0/1.10", "ip_address": "unassigned", "ok": "YES", "method": "NVRAM", "status": link_st,                "protocol": link_pr},
        {"interface": "GigabitEthernet0/1.20", "ip_address": "unassigned", "ok": "YES", "method": "NVRAM", "status": link_st,                "protocol": link_pr},
        {"interface": "GigabitEthernet0/1.30", "ip_address": "unassigned", "ok": "YES", "method": "NVRAM", "status": link_st,                "protocol": link_pr},
        {"interface": "GigabitEthernet0/1.40", "ip_address": "unassigned", "ok": "YES", "method": "NVRAM", "status": link_st,                "protocol": link_pr},
        {"interface": "GigabitEthernet0/2",    "ip_address": "unassigned", "ok": "YES", "method": "NVRAM", "status": "administratively down", "protocol": "down"},
        {"interface": "GigabitEthernet0/3",    "ip_address": "unassigned", "ok": "YES", "method": "NVRAM", "status": "administratively down", "protocol": "down"},
    ]


def _core_switch_interfaces(ip: str, status: str = "Online"):
    """Generate Core Switch interfaces: 16 GigabitEthernet ports + 4 VLANs."""
    link_st = "up" if status == "Online" else "administratively down"
    link_pr = "up" if status == "Online" else "down"
    ports = _switch_ports(status=link_st, protocol=link_pr)
    # Server VLAN 50 management interface for this lab topology
    ports += [
        {"interface": "Vlan50", "ip_address": "192.168.100.2", "ok": "YES", "method": "NVRAM", "status": link_st, "protocol": link_pr},
    ]
    return ports


def _access_switch_interfaces(ip: str, status: str = "Online"):
    """Generate Switch interfaces: 16 GigabitEthernet ports."""
    link_st = "up" if status == "Online" else "administratively down"
    link_pr = "up" if status == "Online" else "down"
    return _switch_ports(status=link_st, protocol=link_pr)


# ── Floor Access Switch uplink sub-interfaces (exact IPs from lab topology) ───
_FLOOR_SUBINTERFACE = {
    "Floor 1": ("GigabitEthernet0/1.10", "192.168.99.1"),
    "Floor 2": ("GigabitEthernet0/1.20", "192.168.99.65"),
    "Floor 3": ("GigabitEthernet0/1.30", "192.168.99.129"),
    "Floor 4": ("GigabitEthernet0/1.40", "192.168.99.193"),
}


def _floor_switch_interfaces(location: str, ip: str, status: str = "Online"):
    """Access switch on a floor: 16 GigabitEthernet ports + its uplink sub-interface IP."""
    link_st = "up" if status == "Online" else "administratively down"
    link_pr = "up" if status == "Online" else "down"
    ports = _switch_ports(status=link_st, protocol=link_pr)
    subif = _FLOOR_SUBINTERFACE.get((location or "").strip())
    if subif:
        interface, sub_ip = subif
        ports.append({
            "interface": interface, "ip_address": sub_ip, "ok": "YES", "method": "NVRAM",
            "status": link_st, "protocol": link_pr,
        })
    return ports


def _end_device_interfaces(ip: str, status: str = "Online"):
    link_st = "up"   if status == "Online" else "administratively down"
    link_pr = "up"   if status == "Online" else "down"
    return [
        {"interface": "FastEthernet0",  "ip_address": ip,           "ok": "YES", "method": "DHCP",  "status": link_st,                "protocol": link_pr},
        {"interface": "FastEthernet1",  "ip_address": "unassigned", "ok": "YES", "method": "unset", "status": "administratively down", "protocol": "down"},
        {"interface": "Loopback0",      "ip_address": "127.0.0.1",  "ok": "YES", "method": "other", "status": "up",                   "protocol": "up"},
    ]


def _linux_server_interfaces(status: str = "Online"):
    """LinuxServer interface view requested by the user."""
    if status == "Online":
        return [
            {"interface": "lo",   "status": "UNKNOWN", "ip_address": "127.0.0.1/8 ::1/128"},
            {"interface": "ens3", "status": "UP",      "ip_address": "192.168.100.10/26 fe80::250:ff:fe00:1700/64"},
            {"interface": "ens4", "status": "DOWN",    "ip_address": ""},
        ]
    return [
        {"interface": "lo",   "status": "UNKNOWN", "ip_address": "127.0.0.1/8 ::1/128"},
        {"interface": "ens3", "status": "DOWN",    "ip_address": ""},
        {"interface": "ens4", "status": "DOWN",    "ip_address": ""},
    ]


# ── Main Router specific interfaces (exact IPs from lab topology) ─────────────
def _main_router_interfaces():
    return [
        {"interface": "GigabitEthernet0/0",    "ip_address": "192.168.155.129", "ok": "YES", "method": "NVRAM", "status": "up",                    "protocol": "up"},
        {"interface": "GigabitEthernet0/1",    "ip_address": "unassigned",      "ok": "YES", "method": "NVRAM", "status": "up",                    "protocol": "up"},
        {"interface": "GigabitEthernet0/1.10", "ip_address": "192.168.99.1",    "ok": "YES", "method": "NVRAM", "status": "up",                    "protocol": "up"},
        {"interface": "GigabitEthernet0/1.20", "ip_address": "192.168.99.65",   "ok": "YES", "method": "NVRAM", "status": "up",                    "protocol": "up"},
        {"interface": "GigabitEthernet0/1.30", "ip_address": "192.168.99.129",  "ok": "YES", "method": "NVRAM", "status": "up",                    "protocol": "up"},
        {"interface": "GigabitEthernet0/1.40", "ip_address": "192.168.99.193",  "ok": "YES", "method": "NVRAM", "status": "up",                    "protocol": "up"},
        {"interface": "GigabitEthernet0/2",    "ip_address": "unassigned",      "ok": "YES", "method": "NVRAM", "status": "administratively down", "protocol": "down"},
        {"interface": "GigabitEthernet0/3",    "ip_address": "unassigned",      "ok": "YES", "method": "NVRAM", "status": "administratively down", "protocol": "down"},
        {"interface": "NVI0",                  "ip_address": "192.168.155.129", "ok": "YES", "method": "unset", "status": "up",                    "protocol": "up"},
    ]


# ── Core Switch specific interfaces (exact IPs from lab topology) ──────────────
def _main_core_switch_interfaces():
    ports = _switch_ports()   # 16 clean ports, no duplicates
    ports += [
        {"interface": "Vlan10", "ip_address": "192.168.99.2", "ok": "YES", "method": "NVRAM", "status": "up", "protocol": "up"},
    ]
    return ports


# ── Floor access switches: each switch's own VLAN SVI (exact IPs used by the
#    router's ping monitor) rather than the router's floor sub-interface IP ──
_SWITCH_VLAN_INFO = {
    "switch01": ("Vlan10", "192.168.99.3"),
    "switch02": ("Vlan20", "192.168.99.66"),
    "switch03": ("Vlan30", "192.168.99.130"),
    "switch04": ("Vlan40", "192.168.99.194"),
}


def _named_switch_interfaces(name_key: str, status: str = "Online"):
    link_st = "up" if status == "Online" else "administratively down"
    link_pr = "up" if status == "Online" else "down"
    ports = _switch_ports(status=link_st, protocol=link_pr)
    vlan_label, vlan_ip = _SWITCH_VLAN_INFO[name_key]
    ports.append({
        "interface": vlan_label, "ip_address": vlan_ip, "ok": "YES", "method": "NVRAM",
        "status": link_st, "protocol": link_pr,
    })
    return ports


def get_ip_interfaces(device_name: str, device_ip: str = "", device_status: str = "Online",
                      device_type: str = "", device_category: str = "", device_location: str = ""):
    """
    Return interface list. Priority order:
    1. Exact device name match (Main Router, Core Switch)
    2. Category = Router → always router interfaces regardless of type
    3. Floor access switch (location = Floor 1-4) → include its uplink sub-interface IP
    4. Device type dispatch (Core Device, Access Device, etc.)
    """
    n    = device_name.lower().strip()
    dt   = device_type.lower().strip()
    dcat = device_category.lower().strip()

    # ── 1. Exact named matches ─────────────────────────────────────────────────
    if "main router" in n or n == "router":
        return _main_router_interfaces()

    if n in ("core switch", "coreswitch") or n.startswith("core switch"):
        return _main_core_switch_interfaces()

    if n in _SWITCH_VLAN_INFO:
        return _named_switch_interfaces(n, device_status)

    # ── 2. Category = Router → show router interfaces ──────────────────────────
    if dcat == "router" or dt == "router":
        return _router_interfaces(device_ip, device_status)

    # ── 3. Floor access switch → use its specific uplink sub-interface IP ──────
    if dt in ("accessdevice", "access device", "access switch") and (device_location or "").strip() in _FLOOR_SUBINTERFACE:
        return _floor_switch_interfaces(device_location, device_ip, device_status)

    # ── 4. Type-based dispatch ─────────────────────────────────────────────────
    if dt in ("coredevice", "core device", "core switch"):
        return _core_switch_interfaces(device_ip, device_status)

    if dt in ("accessdevice", "access device", "access switch"):
        return _access_switch_interfaces(device_ip, device_status)

    # Linux Server
    if dt in ("server",) or dcat == "server" or "linuxserver" in n or "linux server" in n:
        return _linux_server_interfaces(device_status)

    # Generic switch fallback
    if "switch" in dt or "switch" in n:
        return _access_switch_interfaces(device_ip, device_status)

    # Fallback: end device
    return _end_device_interfaces(device_ip, device_status)


def require_login():
    if "user" not in session or not _current_admin():
        session.clear()          # not logged in, or this account was deleted
        return redirect(url_for("login"))
    return None


# ─── Page Routes ──────────────────────────────────────────────────────────────


# Start background device availability monitor.
start_device_status_monitor()

@app.route("/")
def index():
    return redirect(url_for("dashboard"))

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()
        conn = get_db()
        admin = conn.execute(
            "SELECT * FROM Admin WHERE username=%s", (username,)
        ).fetchone()
        ok = False
        if admin:
            ok = _verify_password(admin["password"], password)
            if ok:
                _upgrade_legacy_password(conn, admin, password)
        conn.close()
        if ok:
            session["user"] = username
            add_activity_log(username, "Login", request.remote_addr, f'User "{username}" logged in from {request.remote_addr}', "Session")
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid username or password."
            add_activity_log(username or "unknown", "Login Failed", request.remote_addr,
                             f'Failed login attempt from {request.remote_addr}', "Session")
    notice = "Password updated. Please log in with your new password." if request.args.get("reset") else None
    return render_template("login.html", error=error, notice=notice,
                           recovery_enabled=RECOVERY_ENABLED)

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if not RECOVERY_ENABLED:
        return render_template("forgot_password.html", disabled=True, error=None)

    ip = request.remote_addr or "unknown"
    error = None
    if request.method == "POST":
        now = time.time()
        state = _recovery_fails.get(ip, {"count": 0, "locked_until": 0})
        if state["locked_until"] > now:
            mins = int((state["locked_until"] - now) // 60) + 1
            error = f"Too many failed attempts. Try again in about {mins} minute(s)."
            return render_template("forgot_password.html", disabled=False, error=error)

        username = request.form.get("username", "").strip()
        key = request.form.get("recovery_key", "").strip()
        new_pw = request.form.get("new_password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()

        if len(new_pw) < 6:
            error = "New password must be at least 6 characters."
        elif new_pw != confirm:
            error = "Passwords do not match."
        else:
            key_ok = hmac.compare_digest(key.encode("utf-8"), RECOVERY_KEY.encode("utf-8"))
            conn = get_db()
            row = conn.execute("SELECT admin_id FROM Admin WHERE username=%s", (username,)).fetchone()
            if key_ok and row:
                conn.execute("UPDATE Admin SET password=%s WHERE admin_id=%s",
                             (new_pw, row["admin_id"]))
                conn.commit()
                conn.close()
                _recovery_fails.pop(ip, None)
                add_activity_log(username, "Reset Password (Recovery Key)", ip,
                                 f'Password of "{username}" reset via recovery key from {ip}', "Session")
                return redirect(url_for("login", reset=1))
            conn.close()
            state["count"] += 1
            if state["count"] >= RECOVERY_MAX_FAILS:
                state = {"count": 0, "locked_until": now + RECOVERY_LOCK_SECONDS}
            _recovery_fails[ip] = state
            add_activity_log(username or "unknown", "Recovery Failed", ip,
                             f"Failed password recovery attempt from {ip}", "Session")
            error = "Invalid username or recovery key."
    return render_template("forgot_password.html", disabled=False, error=error)

@app.route("/logout")
def logout():
    user = session.get("user","admin")
    add_activity_log(user, "Logout", "Session", f'User "{user}" logged out', "Session")
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
def dashboard():
    if r := require_login(): return r
    return render_template("dashboard.html", user=session["user"],
                           today_th=now_th().strftime("%Y-%m-%d"))

@app.route("/devices")
def devices():
    if r := require_login(): return r
    return render_template("devices.html", user=session["user"],
                           reserved_ips=RESERVED_IPS)

@app.route("/devices/<device_id>/interfaces")
def device_interfaces(device_id):
    if r := require_login(): return r
    conn = get_db()
    device = conn.execute("SELECT * FROM Device WHERE device_id=%s", (device_id,)).fetchone()
    conn.close()
    if not device:
        return redirect(url_for("devices"))
    interfaces = get_ip_interfaces(device["device_name"], device["ip_address"], device["status"], device["device_type"], "", device["location"])
    return render_template("device_detail.html", device=dict(device), interfaces=interfaces, user=session["user"])

@app.route("/categories")
def categories_page():
    if r := require_login(): return r
    return render_template("Categories.html", user=session["user"])

@app.route("/admins")
def admins_page():
    if r := require_login(): return r
    me = _current_admin()
    return render_template("admins.html", user=session["user"],
                           is_super=bool(me and me["is_super"]))

@app.route("/activity")
def activity():
    if r := require_login(): return r
    return render_template("activity.html", user=session["user"])


# ─── API: Dashboard ───────────────────────────────────────────────────────────

@app.route("/api/dashboard/summary")
def api_dashboard_summary():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) AS n FROM Device").fetchone()["n"]
    online = conn.execute("SELECT COUNT(*) AS n FROM Device WHERE status='Online'").fetchone()["n"]
    offline = conn.execute("SELECT COUNT(*) AS n FROM Device WHERE status='Offline'").fetchone()["n"]

    start = request.args.get("start")
    end = request.args.get("end")
    device_id = request.args.get("device_id")
    same_day = bool(start and end and start == end)

    if not device_id and same_day:
        # Single day, no device selected: Avg Throughput is computed from
        # every reading actually recorded for that day from an Access
        # Device floor - regardless of that floor's CURRENT Online/Offline
        # status. A floor going Offline does not retroactively wipe out
        # the readings it already contributed earlier that day; it simply
        # stops adding new ones (it can't reach the network to post while
        # down). Same policy as the multi-day branch below - both use
        # every recorded reading, never filtered by current live status.
        avg_row = conn.execute("""
            SELECT AVG(ts.throughput_mbps) AS avg_tput
            FROM ThroughputSpeed ts
            JOIN Device d ON d.device_id = ts.device_id
            WHERE d.device_type = 'Access Device'
              AND DATE(ts.recorded_at) = %s
        """, (start,)).fetchone()
        avg = avg_row["avg_tput"] or 0

        # "Last updated" now means exactly what it says: the timestamp of
        # the most recent reading actually recorded for this day, from any
        # Access Device floor - same dataset as Avg Throughput above, no
        # "every floor must report in the same minute" requirement and no
        # current-status filter. It moves the instant any floor posts a
        # new row, and simply stops moving (not "gets stuck waiting") once
        # a floor goes Offline and can no longer post.
        last_row = conn.execute("""
            SELECT MAX(ts.recorded_at) AS last_at
            FROM ThroughputSpeed ts
            JOIN Device d ON d.device_id = ts.device_id
            WHERE d.device_type = 'Access Device'
              AND DATE(ts.recorded_at) = %s
        """, (start,)).fetchone()
        last_complete_at = str(last_row["last_at"]) if last_row and last_row["last_at"] else None
    else:
        # avg_throughput:
        # - No device selected -> average across ALL devices, from `start` to
        #   `end` (inclusive).
        # - Device selected -> average for THAT device only, over the same
        #   start/end range (collapsing to a single day when start == end).
        query = "SELECT AVG(throughput_mbps) AS avg_tput FROM ThroughputSpeed WHERE 1=1"
        params = []
        if start and end:
            query += " AND DATE(recorded_at) BETWEEN %s AND %s"
            params += [start, end]
        if device_id:
            query += " AND device_id = %s"
            params.append(device_id)

        avg_row = conn.execute(query, tuple(params)).fetchone()
        avg = avg_row["avg_tput"] or 0

        # last_complete_at (multi-day / no specific single day being
        # viewed): the timestamp of the most recent reading anywhere in
        # the table's history, from any Access Device floor - same "no
        # completeness, no status filter" policy as the same-day branch
        # above, just not scoped to one day.
        last_row = conn.execute("""
            SELECT MAX(ts.recorded_at) AS last_at
            FROM ThroughputSpeed ts
            JOIN Device d ON d.device_id = ts.device_id
            WHERE d.device_type = 'Access Device'
        """).fetchone()
        last_complete_at = str(last_row["last_at"]) if last_row and last_row["last_at"] else None

    conn.close()
    return jsonify({
        "total": total,
        "online": online,
        "offline": offline,
        "avg_throughput": round(float(avg), 2),
        "last_complete_at": last_complete_at
    })


# Floor mapping, used only to resolve which device a floor's iperf3 client
# result belongs to when a client posts {"floor": "...", ...} instead of a
# device_id directly. The real ThroughputSpeed table has no floor/vlan
# columns, so this is purely a lookup, never stored.
FLOOR_CONFIG = {
    "Floor 1": {"switch": "Switch01"},
    "Floor 2": {"switch": "Switch02"},
    "Floor 3": {"switch": "Switch03"},
    "Floor 4": {"switch": "Switch04"},
}


def get_floor_device(conn, floor):
    """Find the floor switch by location first, then by the configured name."""
    cfg = FLOOR_CONFIG[floor]
    return conn.execute(
        "SELECT device_id, device_name, status FROM Device "
        "WHERE location=%s AND device_type='Access Device' "
        "ORDER BY device_id LIMIT 1",
        (floor,)
    ).fetchone() or conn.execute(
        "SELECT device_id, device_name, status FROM Device WHERE device_name=%s LIMIT 1",
        (cfg["switch"],)
    ).fetchone()



# VLANS of each floor (shown in the dashboard's Device Overview table).
VLAN_BY_LOCATION = {
    "Floor 1": [10, 20, 30, 40],   # Switch01
    "Floor 2": [10, 20, 30, 40],   # Switch02
    "Floor 3": [10, 10, 20, 20],   # Switch03
    "Floor 4": [30, 30, 40, 40],   # Switch04
}


@app.route("/api/dashboard/devices")
def api_dashboard_devices():
    """
    Device Status table: access switches only, each with its latest
    throughput reading and the VLANs it trunks.

    Main Router and Core Switch are excluded on purpose - traffic only
    passes through them, they aren't an access point being measured for
    client throughput - so this is filtered to device_type='Access Device'
    directly off the database rather than hard-coding device names.
    """
    conn = get_db()

    devices = conn.execute("""
        SELECT device_id, device_name, ip_address, location, status
        FROM Device
        WHERE device_type = 'Access Device'
        ORDER BY device_id
    """).fetchall()

    result = []
    for d in devices:
        latest = conn.execute("""
            SELECT throughput_mbps, recorded_at
            FROM ThroughputSpeed
            WHERE device_id=%s
            ORDER BY recorded_at DESC
            LIMIT 1
        """, (d["device_id"],)).fetchone()

        vlans = VLAN_BY_LOCATION.get(d["location"])

        result.append({
            "device_id": d["device_id"],
            "device_name": d["device_name"],
            "ip_address": d["ip_address"],
            "location": d["location"] or "-",
            "vlans": ", ".join(str(v) for v in vlans) if vlans else "-",
            "throughput": round(float(latest["throughput_mbps"]), 2) if latest else None,
            "recorded_at": str(latest["recorded_at"]) if latest else None,
            "status": d["status"],
        })

    conn.close()
    return jsonify(result)


@app.route("/api/dashboard/chart")
def api_dashboard_chart():
    """
    Throughput chart data.

    - Multi-day range (start != end): one averaged point per day - only for
      days that actually have data, so a range with long stretches of no
      history doesn't render as a wall of empty grid.
    - Same-day range (start == end): fine-grained points across that day
      instead of collapsing to a single point, since ThroughputSpeed rows
      are recorded about once per minute. When a specific device is
      selected, its raw per-minute rows are returned as-is; when "all
      devices" is selected, rows are averaged per minute across devices so
      multiple devices posting a few seconds apart don't create separate
      points. The frontend uses HH:MM as the x-axis in this case.
    """
    start = request.args.get(
        "start",
        (now_th() - timedelta(days=14)).strftime("%Y-%m-%d")
    )
    end = request.args.get(
        "end",
        now_th().strftime("%Y-%m-%d")
    )
    device_id = request.args.get("device_id")

    conn = get_db()

    same_day = (start == end)

    if same_day:
        if device_id:
            rows = conn.execute("""
                SELECT recorded_at AS ts,
                       throughput_mbps AS tput
                FROM ThroughputSpeed
                WHERE DATE(recorded_at) = %s
                  AND device_id = %s
                ORDER BY recorded_at
            """, (start, device_id)).fetchall()

            conn.close()
            return jsonify([
                {"date": str(r["ts"]), "throughput": round(float(r["tput"]), 2)}
                for r in rows
            ])

        # No device selected: plot every minute that has AT LEAST ONE
        # reading from an Access Device floor, averaging however many
        # floors actually posted that minute. Not filtered by current
        # Online/Offline status - matches the Avg Throughput card above
        # and the multi-day branch below: a floor going Offline doesn't
        # erase the points it already contributed earlier that day.
        #
        # This used to require a COMPLETE round (every online floor
        # reporting in the exact same minute) before a point was drawn,
        # reusing the same dataset as the Avg Throughput card / "Last
        # updated" label. In practice floors
        # post a few seconds apart and don't reliably line up in the same
        # calendar minute, so most minutes never reached 100% completeness
        # - only the rare minute where every floor happened to align
        # produced a point, and everything else was silently dropped. That
        # is what showed up as a mostly-empty/"hollow" chart with just one
        # or two points even though data was really being collected the
        # whole time. Switching to "at least one reading" keeps every
        # minute that actually has data, matching the same-day + specific
        # -device view below, which already never required completeness.
        rows = conn.execute("""
            SELECT DATE_FORMAT(ts.recorded_at, '%%Y-%%m-%%d %%H:%%i:00') AS minute_key,
                   AVG(ts.throughput_mbps) AS avg_tput
            FROM ThroughputSpeed ts
            JOIN Device d ON d.device_id = ts.device_id
            WHERE d.device_type = 'Access Device'
              AND DATE(ts.recorded_at) = %s
            GROUP BY minute_key
            ORDER BY minute_key
        """, (start,)).fetchall()
        conn.close()

        return jsonify([
            {"date": r["minute_key"], "throughput": round(float(r["avg_tput"]), 2)}
            for r in rows
        ])

    if device_id:
        rows = conn.execute("""
            SELECT DATE(recorded_at) AS day,
                   AVG(throughput_mbps) AS tput
            FROM ThroughputSpeed
            WHERE DATE(recorded_at) BETWEEN %s AND %s
              AND device_id=%s
            GROUP BY DATE(recorded_at)
            ORDER BY DATE(recorded_at)
        """, (start, end, device_id)).fetchall()
    else:
        rows = conn.execute("""
            SELECT DATE(recorded_at) AS day,
                   AVG(throughput_mbps) AS tput
            FROM ThroughputSpeed
            WHERE DATE(recorded_at) BETWEEN %s AND %s
            GROUP BY DATE(recorded_at)
            ORDER BY DATE(recorded_at)
        """, (start, end)).fetchall()

    conn.close()

    # Only the days that actually have data are returned - no null-filled
    # placeholder for empty days - so a range with mostly-empty history
    # (e.g. right after this system started collecting) doesn't render as
    # a wall of blank grid with a couple of stranded points. This matches
    # the same-day view, which likewise only returns minutes that actually
    # have data instead of filling in every missing minute.
    return jsonify([
        {"date": r["day"].strftime("%Y-%m-%d"), "throughput": round(float(r["tput"]), 2)}
        for r in rows
    ])


@app.route("/api/throughput", methods=["POST"])
def api_receive_throughput():
    """
    Receive one real iperf3 result from a DHCP client and store it against a
    device. The real ThroughputSpeed table only has
    (device_id, throughput_mbps, recorded_at) - no floor/vlan/client_ip/
    server_ip/server_port columns - so those fields (if sent) are only used
    to resolve device_id and are never persisted.
    """
    data = request.get_json(silent=True) or {}

    if "throughput_mbps" not in data:
        return jsonify({"success": False, "error": "Missing: throughput_mbps"}), 400

    try:
        throughput = float(data["throughput_mbps"])
        device_id = data.get("device_id")

        conn = get_db()

        if not device_id:
            floor = str(data.get("floor", "")).strip()
            if floor not in FLOOR_CONFIG:
                conn.close()
                return jsonify({
                    "success": False,
                    "error": "Missing/invalid device_id or floor"
                }), 400
            device = get_floor_device(conn, floor)
            device_id = device["device_id"] if device else None

        if not device_id:
            conn.close()
            return jsonify({"success": False, "error": "Could not resolve device_id"}), 400

        # If the device is currently Offline (per the Device table, set by the
        # router-ping monitor), do NOT store the new reading. Nothing new is
        # shown on the dashboard (cards, chart, Device Overview), so the last
        # value measured while Online stays until the device comes back Online.
        st_row = conn.execute(
            "SELECT status FROM Device WHERE device_id=%s", (device_id,)
        ).fetchone()
        if st_row and st_row["status"] == "Offline":
            conn.close()
            return jsonify({
                "success": True,
                "skipped": True,
                "reason": "device is Offline - reading not stored",
                "device_id": device_id,
                "throughput_mbps": throughput
            })

        conn.execute("""
            INSERT INTO ThroughputSpeed
            (device_id, throughput_mbps, recorded_at)
            VALUES (%s,%s,%s)
        """, (
            device_id, throughput,
            now_th().strftime("%Y-%m-%d %H:%M:%S")
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "device_id": device_id,
            "throughput_mbps": throughput
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400


# ─── API: Devices ─────────────────────────────────────────────────────────────

# ─── VLAN / IP validation (used by Add + Edit device) ────────────────────────
# Same plan as the router config: 192.168.99.0/24 split into four /26 VLANs.
VLAN_NETWORKS = {
    "10": ipaddress.ip_network("192.168.99.0/26"),
    "20": ipaddress.ip_network("192.168.99.64/26"),
    "30": ipaddress.ip_network("192.168.99.128/26"),
    "40": ipaddress.ip_network("192.168.99.192/26"),
}
VLAN_GATEWAYS = {
    "10": "192.168.99.1",
    "20": "192.168.99.65",
    "30": "192.168.99.129",
    "40": "192.168.99.193",
}

# IPs that the lab configuration (Config_Cisco_Device.md) already assigns to a
# specific machine. A new device may only take one of these if it is that
# machine (same name) - anything else is rejected as a duplicate IP, even when
# the machine is not (or no longer) listed in the Device table.
#   ip -> owner name
RESERVED_IPS = {
    # infrastructure (router sub-interfaces / switches)
    "192.168.99.2":   "Core Switch",     # CoreSwitch  interface Vlan10
    "192.168.99.3":   "Switch01",        # VLAN10 mgmt
    "192.168.99.66":  "Switch02",        # VLAN20 mgmt
    "192.168.99.130": "Switch03",        # VLAN30 mgmt
    "192.168.99.194": "Switch04",        # VLAN40 mgmt
    # Linux servers (iperf3 servers)
    "192.168.99.10":  "LinuxServer01",   # VLAN10
    "192.168.99.74":  "LinuxServer02",   # VLAN20
    "192.168.99.138": "LinuxServer03",   # VLAN30
    "192.168.99.202": "LinuxServer04",   # VLAN40
    # router WAN side
    "192.168.155.129": "Main Router",    # Router Gi0/0
    "192.168.155.1":   "WAN Gateway",    # Router default route (next hop)
}


def validate_device_name(conn, name, exclude_id=None):
    """Return an error message if another device already uses this name
    (case-insensitive), else None."""
    name = str(name or "").strip()
    sql = "SELECT device_name FROM Device WHERE LOWER(device_name)=LOWER(%s)"
    args = [name]
    if exclude_id is not None:
        sql += " AND device_id<>%s"
        args.append(exclude_id)
    row = conn.execute(sql, tuple(args)).fetchone()
    if row:
        return f'Device name "{row["device_name"]}" already exists'
    return None


def validate_device_ip(conn, vlan, ip_str, exclude_id=None, is_router=False, name=None):
    """Return an error message (str) if the IP is not allowed, else None.

    vlan: "10" | "20" | "30" | "40" | "other" (WAN / outside 192.168.99.x)
          or empty -> inferred from the IP.
    exclude_id: device being edited (so it does not clash with itself).
    is_router: Router (WAN side) may use an IP outside 192.168.99.0/24;
               every other device must stay inside the lab LAN.
    """
    try:
        ip = ipaddress.ip_address(str(ip_str).strip())
    except ValueError:
        return "Invalid IP address (each number must be between 0 and 255)"
    if ip.version != 4:
        return "Only IPv4 addresses are supported"

    vlan = str(vlan or "").strip().lower()
    if not vlan:
        vlan = next((k for k, net in VLAN_NETWORKS.items() if ip in net), "other")

    if vlan == "other" and not is_router:
        return "IP must be in 192.168.99.x (e.g. 192.168.99.10)"

    if vlan == "other":
        for k, net in VLAN_NETWORKS.items():
            if ip in net:
                return f"IP {ip} belongs to VLAN {k} ({net}) - select VLAN {k}"
    else:
        net = VLAN_NETWORKS.get(vlan)
        if net is None:
            return "Invalid VLAN"
        if ip not in net:
            return f"IP {ip} is not in VLAN {vlan} range ({net})"
        if ip in (net.network_address, net.broadcast_address):
            return f"{ip} is the network/broadcast address of VLAN {vlan}"
        if str(ip) == VLAN_GATEWAYS[vlan]:
            return f"{ip} is the gateway of VLAN {vlan}"

    # Reserved by the lab configuration (see RESERVED_IPS above).
    owner = RESERVED_IPS.get(str(ip))
    if owner and str(name or "").strip().lower() != owner.lower():
        # Editing a device that already holds this IP (unchanged) is fine.
        holds_it = False
        if exclude_id is not None:
            cur_row = conn.execute(
                "SELECT ip_address FROM Device WHERE device_id=%s", (exclude_id,)
            ).fetchone()
            holds_it = bool(cur_row) and str(cur_row["ip_address"]).strip() == str(ip)
        if not holds_it:
            return f'{ip} is already used by {owner}'

    # Compare against the stored IP with invisible characters (\r, \n, spaces)
    # stripped, so a row that was edited by hand in phpMyAdmin still counts as
    # a duplicate instead of slipping past an exact "=" match.
    clean_col = "TRIM(REPLACE(REPLACE(ip_address, '\\r', ''), '\\n', ''))"
    if exclude_id is None:
        row = conn.execute(
            f"SELECT device_name FROM Device WHERE {clean_col} = %s", (str(ip),)
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT device_name FROM Device WHERE {clean_col} = %s AND device_id<>%s",
            (str(ip), exclude_id),
        ).fetchone()
    if row:
        return f'{ip} is already used by {row["device_name"]}'
    return None


@app.route("/api/devices", methods=["GET"])
def api_devices():
    try:
        conn = get_db()
        # Keep this query compatible with the current Device table.
        rows = conn.execute("""
            SELECT d.device_id AS id,
                   d.device_name AS name,
                   d.ip_address,
                   d.device_type AS type,
                   COALESCE(c.category_name, 'Unknown') AS category,
                   d.status,
                   d.last_updated,
                   d.last_checked,
                   d.location,
                   d.category_id,
                   d.connected_to,
                   p.device_name AS connected_to_name
            FROM Device d
            LEFT JOIN Category c ON c.category_id = d.category_id
            LEFT JOIN Device p ON p.device_id = d.connected_to
            ORDER BY
              CASE c.category_name
                WHEN 'Router' THEN 1
                WHEN 'Switch' THEN 2
                WHEN 'Server' THEN 3
                ELSE 4
              END,
              CASE d.device_type
                WHEN 'Core Device' THEN 1
                WHEN 'Access Device' THEN 2
                WHEN 'Client Device' THEN 3
                WHEN 'Server' THEN 4
                ELSE 5
              END,
              -- Group by floor/location so a newly added device lands right
              -- after the other devices on the same floor, instead of at
              -- the very end of the whole list (e.g. a new Floor 1 switch
              -- is inserted after the existing Floor 1 devices, not after
              -- Floor 4's).
              CASE d.location
                WHEN 'Server Room'      THEN 1
                WHEN 'Floor 1'          THEN 2
                WHEN 'Floor 2'          THEN 3
                WHEN 'Floor 3'          THEN 4
                WHEN 'Floor 4'          THEN 5
                ELSE 6
              END,
              d.device_id
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/devices", methods=["POST"])
def api_add_device():
    data = request.json or {}
    required = ["name", "ipAddress", "type", "category"]
    missing  = [f for f in required if not str(data.get(f, "")).strip()]
    allowed_types = {"Router", "Core Device", "Access Device", "Server", "Client Device", "Client"}
    allowed_locations = {"Server Room", "Floor 1", "Floor 2", "Floor 3", "Floor 4"}
    if not missing and data["type"] not in allowed_types:
        return jsonify({"success": False, "error": "Invalid Device Type"}), 400
    location = str(data.get("location") or "").strip()
    if location and location not in allowed_locations:
        return jsonify({"success": False, "error": "Invalid Location"}), 400
    if missing:
        return jsonify({"success": False, "error": f"Missing: {', '.join(missing)}"}), 400
    now    = now_th().strftime("%Y-%m-%d %H:%M:%S")
    # New devices start Offline by default: they have no confirmed IP reply
    # yet (the router hasn't pinged them - only the fixed lab devices in
    # MONITOR_DEVICES are actively pinged) so we never claim a device is
    # Online before it has actually been reached.
    status = data.get("status", "Offline")
    if status not in ("Online", "Offline"):
        status = "Offline"
    try:
        conn = get_db()
        # Resolve category_id from category name
        cat = conn.execute(
            "SELECT category_id FROM Category WHERE category_name=%s", (data["category"],)
        ).fetchone()
        if not cat:
            conn.close()
            return jsonify({"success": False, "error": "Category not found"}), 400
        category_id = cat["category_id"]

        name_err = validate_device_name(conn, data["name"])
        if name_err:
            conn.close()
            return jsonify({"success": False, "error": name_err, "field": "name"}), 400

        ip_err = validate_device_ip(conn, data.get("vlan"), data["ipAddress"],
                                    is_router=(data["category"] == "Router"),
                                    name=data["name"])
        if ip_err:
            conn.close()
            return jsonify({"success": False, "error": ip_err, "field": "ip"}), 400

        # connected_to: which existing device this one plugs into (topology).
        connected_to = data.get("connectedTo") or None
        connected_to_name = None
        if connected_to:
            parent = conn.execute(
                "SELECT device_id, device_name FROM Device WHERE device_id=%s", (connected_to,)
            ).fetchone()
            if not parent:
                conn.close()
                return jsonify({"success": False, "error": "Connected To device not found"}), 400
            connected_to_name = parent["device_name"]

        cur = conn.execute(
            "INSERT INTO Device (category_id, device_name, device_type, ip_address, status, last_updated, location, connected_to) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (category_id, data["name"].strip(), data["type"], data["ipAddress"].strip(),
             status, now, data.get("location", None), connected_to),
        )
        new_id = cur.lastrowid


        conn.commit()
        conn.close()
        conn_note = f' connected to "{connected_to_name}"' if connected_to_name else ''
        add_activity_log(session.get("user","admin"), "Add Device", data["name"],
            f'Added {data["type"]} "{data["name"]}" ({data["ipAddress"]}) [{status}]{conn_note}',
            "Device", new_id)
        return jsonify({"success": True, "id": new_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/devices/<device_id>", methods=["PUT"])
def api_edit_device(device_id):
    data = request.json or {}
    required = ["name", "ipAddress", "type", "category", "location"]
    missing  = [f for f in required if not str(data.get(f, "")).strip()]
    allowed_types = {"Router", "Core Device", "Access Device", "Server", "Client Device", "Client"}
    allowed_locations = {"Server Room", "Floor 1", "Floor 2", "Floor 3", "Floor 4"}
    if not missing and data["type"] not in allowed_types:
        return jsonify({"success": False, "error": "Invalid Device Type"}), 400
    if not missing and data["location"] not in allowed_locations:
        return jsonify({"success": False, "error": "Invalid Location"}), 400
    if missing:
        return jsonify({"success": False, "error": f"Missing: {', '.join(missing)}"}), 400
    now = now_th().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = get_db()
        cat = conn.execute(
            "SELECT category_id FROM Category WHERE category_name=%s", (data["category"],)
        ).fetchone()
        if not cat:
            conn.close()
            return jsonify({"success": False, "error": "Category not found"}), 400
        category_id = cat["category_id"]

        name_err = validate_device_name(conn, data["name"], exclude_id=device_id)
        if name_err:
            conn.close()
            return jsonify({"success": False, "error": name_err, "field": "name"}), 400

        ip_err = validate_device_ip(conn, data.get("vlan"), data["ipAddress"], exclude_id=device_id,
                                    is_router=(data["category"] == "Router"),
                                    name=data["name"])
        if ip_err:
            conn.close()
            return jsonify({"success": False, "error": ip_err, "field": "ip"}), 400

        # connected_to: which existing device this one plugs into (topology).
        # A device can't be connected to itself.
        connected_to = data.get("connectedTo") or None
        if connected_to:
            if str(connected_to) == str(device_id):
                conn.close()
                return jsonify({"success": False, "error": "A device cannot connect to itself"}), 400
            parent = conn.execute(
                "SELECT device_id FROM Device WHERE device_id=%s", (connected_to,)
            ).fetchone()
            if not parent:
                conn.close()
                return jsonify({"success": False, "error": "Connected To device not found"}), 400

        # status is deliberately NOT part of this UPDATE. Online/Offline must
        # only ever be set by the router-ping monitor (_save_device_status).
        # Editing a device's name/IP/type/location must never change its
        # status - previously this endpoint (and the edit form) defaulted a
        # missing status to "Online", so every edit silently marked the
        # device Online even though nothing had actually pinged it yet.
        rows = conn.execute(
            "UPDATE Device SET device_name=%s, ip_address=%s, device_type=%s, category_id=%s, "
            "last_updated=%s, location=%s, connected_to=%s WHERE device_id=%s",
            (data["name"].strip(), data["ipAddress"].strip(), data["type"], category_id,
             now, data.get("location", None), connected_to, device_id),
        ).rowcount
        conn.commit()
        conn.close()
        if rows == 0:
            return jsonify({"success": False, "error": "Device not found"}), 404
        add_activity_log(session.get("user","admin"), "Edit Device", data["name"],
            f'Updated "{data["name"]}" ({data["ipAddress"]})',
            "Device", int(device_id))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/devices/<device_id>", methods=["DELETE"])
def api_delete_device(device_id):
    try:
        conn = get_db()
        dev = conn.execute("SELECT device_name FROM Device WHERE device_id=%s", (device_id,)).fetchone()
        if not dev:
            conn.close()
            return jsonify({"success": False, "error": "Device not found"}), 404
        conn.execute("DELETE FROM ThroughputSpeed WHERE device_id=%s", (device_id,))
        conn.execute("DELETE FROM Device WHERE device_id=%s", (device_id,))
        conn.commit()
        conn.close()
        add_activity_log(session.get("user","admin"), "Delete Device", dev["device_name"],
            f'Permanently deleted device "{dev["device_name"]}"', "Device")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── API: Categories ──────────────────────────────────────────────────────────

@app.route("/api/categories", methods=["GET"])
def api_categories():
    conn = get_db()
    rows = conn.execute("SELECT * FROM Category ORDER BY category_id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/categories", methods=["POST"])
def api_add_category():
    data = request.json or {}
    name = str(data.get("name","")).strip()
    desc = str(data.get("description","")).strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"}), 400
    try:
        conn = get_db()
        cur = conn.execute("INSERT INTO Category (category_name, description) VALUES (%s,%s)", (name, desc))
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        add_activity_log(session.get("user","admin"), "Add Category", name, f'Added category "{name}"', "Category")
        return jsonify({"success": True, "id": new_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/categories/<cat_id>", methods=["PUT"])
def api_edit_category(cat_id):
    data = request.json or {}
    name = str(data.get("name","")).strip()
    desc = str(data.get("description","")).strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"}), 400
    try:
        conn = get_db()
        rows = conn.execute("UPDATE Category SET category_name=%s, description=%s WHERE category_id=%s",
                            (name, desc, cat_id)).rowcount
        conn.commit()
        conn.close()
        if rows == 0:
            return jsonify({"success": False, "error": "Category not found"}), 404
        add_activity_log(session.get("user","admin"), "Edit Category", name, f'Updated category "{name}"', "Category")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/categories/<cat_id>", methods=["DELETE"])
def api_delete_category(cat_id):
    try:
        conn = get_db()
        cat = conn.execute("SELECT category_name FROM Category WHERE category_id=%s", (cat_id,)).fetchone()
        if not cat:
            conn.close()
            return jsonify({"success": False, "error": "Category not found"}), 404
        conn.execute("DELETE FROM Category WHERE category_id=%s", (cat_id,))
        conn.commit()
        conn.close()
        add_activity_log(session.get("user","admin"), "Delete Category", cat["category_name"],
            f'Deleted category "{cat["category_name"]}"', "Category")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── API: Admin Management ────────────────────────────────────────────────────
# Rules:
#   * Any logged-in admin can list admins and change THEIR OWN password (must
#     supply the current password).
#   * Only the Super Admin can add an admin, reset another admin's password or
#     delete an admin.
#   * Super Admin accounts cannot be deleted; nobody can delete themselves.

def _current_admin():
    """Admin row of the logged-in user, read fresh from the DB (not from the cookie)."""
    if "user" not in session:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT admin_id, username, is_super FROM Admin WHERE username=%s", (session["user"],)
    ).fetchone()
    conn.close()
    return row

def _admin_guard(super_only=False):
    """Return (me, error_response). error_response is None when allowed."""
    me = _current_admin()
    if not me:
        return None, (jsonify({"success": False, "error": "Unauthorized"}), 401)
    if super_only and not me["is_super"]:
        return me, (jsonify({"success": False, "error": "Only the Super Admin can do this"}), 403)
    return me, None

_LEGACY_HASH_PREFIXES = ("scrypt:", "pbkdf2:", "argon2:", "bcrypt:")

def _is_legacy_hash(stored):
    return (stored or "").startswith(_LEGACY_HASH_PREFIXES)

def _verify_password(stored, password):
    """Passwords are stored as plain text so they are readable in phpMyAdmin.

    Rows written by an older (hashed) version of this app still start with
    "scrypt:" / "pbkdf2:". Those are verified once with werkzeug and then
    converted to plain text by login() - see _upgrade_legacy_password().
    """
    stored = stored or ""
    if _is_legacy_hash(stored):
        try:
            from werkzeug.security import check_password_hash
            return check_password_hash(stored, password)
        except Exception:
            return False
    return hmac.compare_digest(stored.encode("utf-8"), password.encode("utf-8"))

def _upgrade_legacy_password(conn, admin, password):
    """If the stored value is an old hash and the login was correct, store the plain text."""
    if admin and _is_legacy_hash(admin["password"]):
        conn.execute("UPDATE Admin SET password=%s WHERE admin_id=%s",
                     (password, admin["admin_id"]))
        try:
            conn.commit()
        except Exception:
            pass

@app.route("/api/admins", methods=["GET"])
def api_admins():
    me, err = _admin_guard()
    if err: return err
    conn = get_db()
    rows = conn.execute(
        "SELECT a.admin_id, a.username, a.is_super, a.created_at, "
        "(SELECT MAX(l.date_time) FROM ActivityLog l "
        " WHERE l.admin_id=a.admin_id AND l.action_type='Login') AS last_login "
        "FROM Admin a ORDER BY a.admin_id"
    ).fetchall()
    conn.close()
    out = []
    for r_ in rows:
        d = dict(r_)
        d["is_super"] = bool(d["is_super"])
        d["is_me"] = (d["admin_id"] == me["admin_id"])
        out.append(d)
    return jsonify(out)

@app.route("/api/admins", methods=["POST"])
def api_add_admin():
    me, err = _admin_guard(super_only=True)
    if err: return err
    data = request.json or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}", username):
        return jsonify({"success": False,
                        "error": "Username must be 3-50 characters (A-Z, 0-9, _ . -)"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400
    try:
        conn = get_db()
        if conn.execute("SELECT 1 FROM Admin WHERE username=%s", (username,)).fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Username already exists"}), 409
        cur = conn.execute("INSERT INTO Admin (username, password, is_super) VALUES (%s,%s,0)",
                           (username, password))
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        add_activity_log(me["username"], "Add Admin", username, f'Added admin "{username}"', "Admin")
        return jsonify({"success": True, "id": new_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/admins/<int:admin_id>/password", methods=["PUT"])
def api_admin_password(admin_id):
    me, err = _admin_guard()
    if err: return err
    data = request.json or {}
    new_pw = str(data.get("password", "")).strip()
    if len(new_pw) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400
    is_self = (admin_id == me["admin_id"])
    if not is_self and not me["is_super"]:
        return jsonify({"success": False,
                        "error": "You can only change your own password"}), 403
    try:
        conn = get_db()
        row = conn.execute("SELECT username, password FROM Admin WHERE admin_id=%s", (admin_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Admin not found"}), 404
        if is_self:
            # own password: must prove knowledge of the current one
            if not _verify_password(row["password"], str(data.get("current_password", "")).strip()):
                conn.close()
                return jsonify({"success": False, "error": "Current password is incorrect"}), 400
        conn.execute("UPDATE Admin SET password=%s WHERE admin_id=%s",
                     (new_pw, admin_id))
        conn.commit()
        conn.close()
        if is_self:
            add_activity_log(me["username"], "Change Password", row["username"],
                             "Changed own password", "Admin")
        else:
            add_activity_log(me["username"], "Reset Admin Password", row["username"],
                             f'Reset password of admin "{row["username"]}"', "Admin")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/admins/<int:admin_id>", methods=["DELETE"])
def api_delete_admin(admin_id):
    me, err = _admin_guard(super_only=True)
    if err: return err
    try:
        conn = get_db()
        row = conn.execute("SELECT username, is_super FROM Admin WHERE admin_id=%s", (admin_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Admin not found"}), 404
        if admin_id == me["admin_id"]:
            conn.close()
            return jsonify({"success": False, "error": "You cannot delete your own account"}), 400
        if row["is_super"]:
            conn.close()
            return jsonify({"success": False, "error": "A Super Admin cannot be deleted"}), 400
        # ActivityLog.admin_id is ON DELETE SET NULL -> the history stays and is
        # shown as "(deleted user)".
        conn.execute("DELETE FROM Admin WHERE admin_id=%s", (admin_id,))
        conn.commit()
        conn.close()
        add_activity_log(me["username"], "Delete Admin", row["username"],
                         f'Deleted admin "{row["username"]}"', "Admin")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── API: Activity Log ────────────────────────────────────────────────────────

@app.route("/api/activity")
def api_activity():
    """
    Activity Log, straight from the ActivityLog table (no hard cap).

    The "target" the UI displays is derived by joining Device on device_id
    (falling back to 'System' for rows with no device, e.g. Login/Logout).

    - Called with ?page=&page_size=&q= -> server-side paging + search over the
      WHOLE table, returns {"logs": [...], "total": N, "page": P, "pages": M}.
    - Called with no paging params     -> every row, newest first (legacy shape).
    """
    conn = get_db()
    from_sql = """
        FROM ActivityLog al
        LEFT JOIN Admin a ON a.admin_id = al.admin_id
        LEFT JOIN Device d ON d.device_id = al.device_id
    """
    select_sql = """
        SELECT al.log_id as id, COALESCE(a.username, '(deleted user)') as user,
               al.action_type as action,
               COALESCE(d.device_name, 'System') as target,
               al.action_detail as details, al.date_time as timestamp,
               CASE WHEN al.device_id IS NOT NULL THEN 'Device' ELSE 'System' END as target_type,
               al.device_id
    """
    order_sql = " ORDER BY al.date_time DESC, al.log_id DESC"

    q = (request.args.get("q") or "").strip()
    where_sql, params = "", []
    if q:
        like = f"%{q}%"
        where_sql = (
            " WHERE (COALESCE(a.username, '(deleted user)') LIKE %s OR al.action_type LIKE %s"
            " OR al.action_detail LIKE %s OR COALESCE(d.device_name, 'System') LIKE %s"
            " OR CAST(al.date_time AS CHAR) LIKE %s)"
        )
        params = [like] * 5

    if "page" not in request.args:
        rows = conn.execute(select_sql + from_sql + where_sql + order_sql, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    try:
        page_size = min(max(int(request.args.get("page_size", 10)), 1), 200)
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page_size, page = 10, 1

    total = conn.execute("SELECT COUNT(*) AS n " + from_sql + where_sql, params).fetchone()["n"]
    pages = max(1, -(-total // page_size))
    page = min(page, pages)
    rows = conn.execute(
        select_sql + from_sql + where_sql + order_sql + " LIMIT %s OFFSET %s",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    conn.close()
    return jsonify({"logs": [dict(r) for r in rows], "total": total, "page": page, "pages": pages})


@app.route("/api/health")
def api_health():
    try:
        conn = get_db()
        conn.execute("SELECT 1")
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ThroughputSpeed"
        ).fetchone()
        conn.close()
        return jsonify({
            "success": True,
            "database": "ok",
            "throughput_rows": row["n"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0", port=5000)