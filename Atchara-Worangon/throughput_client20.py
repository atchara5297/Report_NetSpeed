
#!/usr/bin/env python3

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# CLIENT-20 CONFIGURATION
# ============================================================
# This program runs ONLY on CLIENT-20.
#
# The iperf3 SERVER must already be running on 192.168.99.74:
#     iperf3 -s -p 5202
#
# This client does NOT start an iperf3 server.
# This client does NOT connect directly to MySQL.
# It sends the measurement to the existing Flask API, and
# the Flask API stores it in the ThroughputSpeed table.
#
# Device data below is taken from database/network_monitoring.sql
# (Device table) - NOT re-typed by hand - so it can't drift from what
# the dashboard actually shows:
#   device_id 4 = "Switch02", location = "Floor 2"
# ============================================================

FLOOR = "Floor 2"
VLAN = 20

# Device table:
# 4 | Switch02 | Access Device
DEVICE_ID = 4
DEVICE_NAME = "Switch02"

# Client IP (informational only - the real source IP is auto-detected
# by get_client_ip() below; this is just what's printed if detection
# somehow fails).
CLIENT_EXPECTED_IP = "192.168.99.75"

# ============================================================
# IPERF3 SERVER
# ============================================================

SERVER_IP = "192.168.99.74"
SERVER_PORT = 5202

# ============================================================
# FLASK / DATABASE API
# ============================================================
# Keep this equal to the IP/port of the Flask server (UBserver01).
# ============================================================

FLASK_SERVER_IP = "192.168.155.156"
FLASK_PORT = 5000

API_URL = f"http://{FLASK_SERVER_IP}:{FLASK_PORT}/api/throughput"

# ============================================================
# TEST SETTINGS
# ============================================================

# One iperf3 measurement lasts 10 seconds.
TEST_TIME = 10

# Start another measurement approximately every 1 minute.
INTERVAL = 60

# All 4 clients (client10/20/30/40) start ~ at the same moment (systemd
# boot, or all launched by hand together) and would then hammer the
# single Flask process at the exact same second every single cycle.
# A small fixed per-client offset spreads the 4 POSTs out inside the
# 60s window instead of all 4 racing to connect simultaneously - this
# was one of the causes of "curl works, the client script can't
# connect": Flask's dev server (without threaded=True) only serves one
# request at a time, so 4-at-once requests queue up and the client's
# short timeout gives up before its turn comes.
START_DELAY = 0

IPERF_TIMEOUT = 30
API_TIMEOUT = 10

# Retry the API a few times before declaring the database save
# failed for this cycle.
API_RETRIES = 3
API_RETRY_DELAY = 2


# ============================================================
# CLIENT IP
# ============================================================

def get_client_ip():
    """
    Detect the source IP used to reach the iperf3 server.
    If detection fails, use the known expected client IP.
    """

    sock = None

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((SERVER_IP, SERVER_PORT))
        return sock.getsockname()[0]

    except Exception:
        return CLIENT_EXPECTED_IP

    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


# ============================================================
# THAILAND TIME
# ============================================================

def get_thailand_time():
    return datetime.now(
        ZoneInfo("Asia/Bangkok")
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# PRE-FLIGHT: CAN WE EVEN REACH THE FLASK API?
# ============================================================
# This is a plain TCP connect check, separate from the actual HTTP POST.
# It exists purely so failures are diagnosable: "connection refused"
# (nothing listening / wrong port), "timed out" (firewall dropping the
# packet / routing problem between this VLAN and the server), and
# "name resolution" errors each get a distinct, specific message
# instead of one generic "DATABASE SAVE: FAILED" every time.
# ============================================================

def check_api_reachable():
    try:
        with socket.create_connection(
            (FLASK_SERVER_IP, FLASK_PORT), timeout=5
        ):
            return True, None

    except socket.gaierror as exc:
        return False, f"cannot resolve host {FLASK_SERVER_IP}: {exc}"

    except ConnectionRefusedError:
        return False, (
            f"connection refused by {FLASK_SERVER_IP}:{FLASK_PORT} "
            "- is app.py running on UBserver01?"
        )

    except (socket.timeout, OSError) as exc:
        return False, (
            f"no response from {FLASK_SERVER_IP}:{FLASK_PORT} within "
            f"5s ({exc}) - check routing/firewall between this VLAN "
            "and the server subnet"
        )


# ============================================================
# RUN IPERF3 CLIENT
# ============================================================

def run_iperf():
    """
    Run iperf3 as a CLIENT.
    The iperf3 server is already running on SERVER_IP:SERVER_PORT.
    """

    command = [
        "iperf3",
        "-c", SERVER_IP,
        "-p", str(SERVER_PORT),
        "-t", str(TEST_TIME),
        "-J"
    ]

    print()
    print("-" * 64)
    print("Starting iperf3 throughput test")
    print("-" * 64)
    print("Command :", " ".join(command))
    print("Server  :", f"{SERVER_IP}:{SERVER_PORT}")
    print("Client  :", get_client_ip())
    print()

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=IPERF_TIMEOUT
        )

    except subprocess.TimeoutExpired:
        print("ERROR: iperf3 timeout")
        return None

    except FileNotFoundError:
        print("ERROR: iperf3 is not installed.")
        print("Install with:")
        print("sudo apt install -y iperf3")
        return None

    except Exception as exc:
        print(f"ERROR: cannot run iperf3: {exc}")
        return None

    if result.returncode != 0:
        print("ERROR: iperf3 failed")

        if result.stderr:
            print(result.stderr.strip())

        return None

    if not result.stdout.strip():
        print("ERROR: iperf3 returned empty output")
        return None

    try:
        return json.loads(result.stdout)

    except json.JSONDecodeError as exc:
        print(f"ERROR: cannot parse iperf3 JSON: {exc}")
        return None


# ============================================================
# GET THROUGHPUT
# ============================================================

def get_throughput_mbps(data):
    """
    Prefer receiver-side throughput because the remote host is
    the iperf3 server. Fall back to sender-side data if needed.
    """

    try:
        bits_per_second = data["end"]["sum_received"]["bits_per_second"]

        return round(bits_per_second / 1_000_000, 2)

    except (KeyError, TypeError):
        pass

    try:
        bits_per_second = data["end"]["sum_sent"]["bits_per_second"]

        return round(bits_per_second / 1_000_000, 2)

    except (KeyError, TypeError) as exc:
        print(f"ERROR: throughput value not found: {exc}")
        return None


# ============================================================
# BUILD DATABASE PAYLOAD
# ============================================================

def build_payload(throughput_mbps):
    """
    /api/throughput only persists (device_id, throughput_mbps,
    recorded_at) into ThroughputSpeed - floor/vlan/client_ip/server_ip/
    server_port are accepted but only used server-side to resolve a
    device_id when one isn't given directly. Since we already know our
    own device_id, we send it explicitly (more reliable than floor
    lookup) and include the rest for logging/debugging on the Flask
    side.

    throughput_speed_id is AUTO_INCREMENT in MySQL,
    so it is NOT sent by the client.
    """

    return {
        "device_id": DEVICE_ID,
        "floor": FLOOR,
        "vlan": VLAN,
        "client_ip": get_client_ip(),
        "server_ip": SERVER_IP,
        "server_port": SERVER_PORT,
        "throughput_mbps": throughput_mbps,
        "recorded_at": get_thailand_time()
    }


# ============================================================
# SEND TO FLASK -> DATABASE
# ============================================================

def save_to_database(payload):
    """
    Send the measurement to Flask.
    Flask is responsible for inserting the data into MySQL.

    No JSON file is saved locally.
    No MySQL username/password is stored on the client.
    """

    reachable, reason = check_api_reachable()
    if not reachable:
        print()
        print("=" * 64)
        print("DATABASE SAVE: FAILED (pre-flight check)")
        print("=" * 64)
        print("API       :", API_URL)
        print("Reason    :", reason)
        print("=" * 64)
        return False

    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Connection": "close",
        },
        method="POST"
    )

    for attempt in range(1, API_RETRIES + 1):

        try:
            with urllib.request.urlopen(
                req,
                timeout=API_TIMEOUT
            ) as response:

                response_body = (
                    response.read()
                    .decode("utf-8")
                )

                print()
                print("=" * 64)
                print("DATABASE SAVE: SUCCESS")
                print("=" * 64)
                print("API       :", API_URL)
                print("HTTP Code :", response.status)
                print("Response  :", response_body)
                print("=" * 64)

                return True

        except urllib.error.HTTPError as exc:

            print()
            print(
                f"API ERROR: HTTP {exc.code} "
                f"(attempt {attempt}/{API_RETRIES})"
            )

            try:
                error_body = exc.read().decode("utf-8")
                print("Response:", error_body)
            except Exception:
                pass

            # HTTP 4xx usually means the payload/API is wrong.
            # Retrying immediately will not fix that.
            if 400 <= exc.code < 500:
                return False

        except urllib.error.URLError as exc:

            print()
            print(
                f"API CONNECTION ERROR "
                f"(attempt {attempt}/{API_RETRIES})"
            )
            print("Reason:", exc.reason)

        except (ConnectionResetError, TimeoutError) as exc:

            print()
            print(
                f"API CONNECTION DROPPED "
                f"(attempt {attempt}/{API_RETRIES}): {exc}"
            )

        except Exception as exc:

            print()
            print(
                f"API ERROR "
                f"(attempt {attempt}/{API_RETRIES}): {exc}"
            )

        if attempt < API_RETRIES:
            time.sleep(API_RETRY_DELAY)

    return False


# ============================================================
# DISPLAY RESULT
# ============================================================

def display_result(throughput_mbps, payload):
    print()
    print("=" * 64)
    print("IPERF3 RESULT")
    print("=" * 64)
    print(f"Device          : {DEVICE_NAME}")
    print(f"Device ID       : {DEVICE_ID}")
    print(f"Floor           : {FLOOR}")
    print(f"VLAN            : {VLAN}")
    print(f"Client IP       : {payload['client_ip']}")
    print(f"Server IP       : {SERVER_IP}")
    print(f"Server Port     : {SERVER_PORT}")
    print(f"Throughput      : {throughput_mbps} Mbps")
    print(f"Recorded At     : {payload['recorded_at']}")
    print("=" * 64)


# ============================================================
# START
# ============================================================

def main():

    print()
    print("=" * 64)
    print(f"           CLIENT-20 IPERF3 THROUGHPUT")
    print("=" * 64)
    print(f"Device ID       : {DEVICE_ID}")
    print(f"Device           : {DEVICE_NAME}")
    print(f"Client IP        : {get_client_ip()}")
    print(f"Floor            : {FLOOR}")
    print(f"VLAN             : {VLAN}")
    print(f"iperf3 Server     : {SERVER_IP}:{SERVER_PORT}")
    print(f"Flask API         : {API_URL}")
    print(f"Test Time         : {TEST_TIME} seconds")
    print(f"Interval          : {INTERVAL} seconds")
    print(f"Start Delay       : {START_DELAY} seconds")
    print("Storage           : MySQL ThroughputSpeed via Flask")
    print("Local JSON        : DISABLED")
    print("=" * 64)

    if START_DELAY:
        print(f"Waiting {START_DELAY}s before the first cycle "
              "(staggers the 4 clients apart)...")
        time.sleep(START_DELAY)

    while True:

        cycle_start = time.time()

        data = run_iperf()

        if data is None:
            print()
            print("Measurement failed.")
            print("Retrying in 5 seconds...")
            time.sleep(5)
            continue

        throughput = get_throughput_mbps(data)

        if throughput is None:
            print()
            print("Could not read throughput from iperf3 result.")
            print("Retrying in 5 seconds...")
            time.sleep(5)
            continue

        payload = build_payload(throughput)

        display_result(throughput, payload)

        success = save_to_database(payload)

        if not success:
            print()
            print("DATABASE SAVE: FAILED")
            print("The measurement was NOT stored by the API.")

        # Keep approximately one-minute cycles, including the
        # 10-second iperf3 test and API request time.
        elapsed = time.time() - cycle_start
        sleep_time = max(0, INTERVAL - elapsed)

        print()
        print("-" * 64)
        print(f"Cycle time : {elapsed:.1f} seconds")
        print(f"Next test  : {sleep_time:.1f} seconds")
        print("-" * 64)

        time.sleep(sleep_time)


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print()
        print("Stopped by user.")

    except Exception as exc:
        print()
        print(f"FATAL ERROR: {exc}")
