"""
Live verification for NTP and OracleDB monitor types + AuthMethod.BEARER.

Not a pytest test. Named ``live_test_`` so pytest discovery ignores it.

SAFETY:
    This script CREATES monitors against a DISPOSABLE container only.
    It reads UPTIME_KUMA_V1_URL / UPTIME_KUMA_V1_USERNAME / UPTIME_KUMA_V1_PASSWORD
    (set by run_disposable_kuma.ps1), deliberately NOT the tests/.env keys that
    point at a real instance.

    A version guard aborts unless the server reports 2.5.x or newer.
    All created monitors are deleted in a finally block.

What is under test:
    - NTP monitor type: creates with hostname + port + threshold fields,
      verifies the round-trip (sent fields match returned fields).
    - OracleDB monitor type: creates with databaseConnectionString + query,
      verifies the round-trip.
    - AuthMethod.BEARER: creates an HTTP monitor using bearer auth, verifies
      bearer_token round-trips correctly.

    The round-trip comparison is the value of this script. "The server didn't
    reject it" is not verification -- a silently dropped or renamed field must
    show up as a failure.

Configuration:
    Use the runner script to deploy and destroy a disposable container::

        pwsh -File scripts/run_disposable_kuma.ps1 `
            -Script tests/live_test_ntp_oracledb_v2_5.py `
            -Image louislam/uptime-kuma:2.5.0 -Port 3025 `
            -DockerEnv UPTIME_KUMA_DB_TYPE=sqlite

Output is ASCII only (Windows cp1252 safety).
Exit code is 0 only if every check passed.
"""
import json
import os
import sys
import time

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from uptime_kuma_api import (
    AuthMethod,
    MonitorType,
    UptimeKumaApi,
    UptimeKumaException,
)
from uptime_kuma_api.exceptions import Timeout

REQUIRED_VERSION_PREFIX = "2.5"

results = []
created_ids = []


def record(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if detail:
        print(f"          {detail}")
    return ok


def equivalent(expected, actual):
    """Compare sent vs returned, tolerating type coercion the server applies."""
    if expected == actual:
        return True

    if isinstance(expected, bool):
        if isinstance(actual, (int, float)) and not isinstance(actual, bool):
            return bool(actual) is expected
        if isinstance(actual, str):
            lowered = actual.strip().lower()
            if lowered in ("1", "true"):
                return expected is True
            if lowered in ("0", "false"):
                return expected is False
        return False

    if isinstance(expected, (int, float)) and isinstance(actual, str):
        try:
            return float(actual) == float(expected)
        except ValueError:
            return False
    if isinstance(expected, str) and isinstance(actual, (int, float)) \
            and not isinstance(actual, bool):
        try:
            return float(expected) == float(actual)
        except ValueError:
            return False

    if isinstance(expected, list) and isinstance(actual, str):
        try:
            return json.loads(actual) == expected
        except (ValueError, TypeError):
            return False

    return False


def check_round_trip(label, sent, got):
    """Verify every field in sent came back intact in got."""
    absent = [key for key in sent if key not in got]
    mismatched = [
        f"{key}: sent {value!r}, got {got[key]!r}"
        for key, value in sent.items()
        if key in got and not equivalent(value, got[key])
    ]

    parts = []
    if absent:
        parts.append("ABSENT: " + ", ".join(absent))
    if mismatched:
        parts.append("MISMATCH: " + "; ".join(mismatched))

    return record(label, not parts, "  ".join(parts))


def connect_with_retry(url, attempts=20, delay=6):
    """Connect, retrying while the server boots socket.io."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            api = UptimeKumaApi(url)
            if attempt > 1:
                print(f"  connected on attempt {attempt}")
            return api
        except Exception as e:
            last = e
            if attempt == attempts:
                break
            print(f"  attempt {attempt}/{attempts} not ready yet, "
                  f"retrying in {delay}s")
            time.sleep(delay)
    raise SystemExit(
        f"ABORT: could not connect to {url} after {attempts} attempts.\n"
        f"       Last error: {type(last).__name__}: {last}"
    )


def read_config():
    url = os.environ.get("UPTIME_KUMA_V1_URL")
    if not url:
        raise SystemExit(
            "ABORT: UPTIME_KUMA_V1_URL is not set.\n"
            "       Use run_disposable_kuma.ps1 to start a throwaway container:\n"
            "\n"
            "         pwsh -File scripts/run_disposable_kuma.ps1 \\\n"
            "             -Script tests/live_test_ntp_oracledb_v2_5.py \\\n"
            "             -Image louislam/uptime-kuma:2.5.0 -Port 3025 \\\n"
            "             -DockerEnv UPTIME_KUMA_DB_TYPE=sqlite"
        )

    username = os.environ.get("UPTIME_KUMA_V1_USERNAME")
    password = os.environ.get("UPTIME_KUMA_V1_PASSWORD")
    missing = [
        name for name, value in (
            ("UPTIME_KUMA_V1_USERNAME", username),
            ("UPTIME_KUMA_V1_PASSWORD", password),
        ) if not value
    ]
    if missing:
        raise SystemExit(f"ABORT: {', '.join(missing)} not set.")

    return url, username, password


def bootstrap(api, username, password):
    """Create admin account if fresh, then log in.

    Polls need_setup() with retries: a 2.x container answers the HTTP probe
    and accepts the socket.io connection before its event handlers are
    registered, so the first call times out. This waits for the real readiness
    signal: the server answering an event.
    """
    needs_setup = None
    for attempt in range(1, 16):
        try:
            needs_setup = api.need_setup()
            if attempt > 1:
                print(f"  server answering events on attempt {attempt}")
            break
        except Timeout:
            if attempt == 15:
                raise SystemExit(
                    "ABORT: connected but server never answered needSetup.\n"
                    "       Nothing was created."
                )
            print(f"  attempt {attempt}/15: connected but not answering "
                  f"events yet, retrying in 6s")
            time.sleep(6)

    if needs_setup:
        api.setup(username, password)
        record("setup() bootstrapped fresh container", True)
    else:
        record("container already set up", True)

    api.login(username, password)
    record("login", True, f"server version {api.version}")


def guard_version(api):
    """Abort unless the server is 2.5.x or newer."""
    version = str(api.version)
    if not version.startswith(REQUIRED_VERSION_PREFIX):
        # Also accept versions > 2.5 (e.g. 2.6.0)
        from packaging.version import parse as parse_version
        if parse_version(version) < parse_version("2.5.0"):
            raise SystemExit(
                f"ABORT: server reports {version}, need {REQUIRED_VERSION_PREFIX}+.\n"
                "       Nothing was created."
            )
    record(f"server version >= {REQUIRED_VERSION_PREFIX}", True, f"got {version}")


def add_monitor_checked(api, label, sent):
    """Create a monitor and round-trip it. Returns the monitor id or None."""
    try:
        r = api.add_monitor(**sent)
    except Exception as e:
        record(f"{label} -> add_monitor", False, str(e))
        return None

    monitor_id = r.get("monitorID")
    if not monitor_id:
        record(f"{label} -> add_monitor", False, f"no monitorID in response: {r}")
        return None

    created_ids.append(monitor_id)
    record(f"{label} -> created id={monitor_id}", True)

    # Read back and compare
    got = api.get_monitor(monitor_id)

    # Fields to verify: everything in sent except 'type' and 'name' (type comes
    # back as the string value, name is trivially round-tripped)
    verify = {k: v for k, v in sent.items() if k not in ("type", "name")}
    check_round_trip(f"{label} -> round-trip", verify, got)

    return monitor_id


def test_ntp(api):
    """Create an NTP monitor with threshold fields and verify round-trip."""
    print("\n-- NTP monitor type --")
    sent = dict(
        type=MonitorType.NTP,
        name="[TEST] NTP pool.ntp.org",
        hostname="pool.ntp.org",
        port=123,
        interval=60,
        maxretries=1,
        retryInterval=60,
        ntpStratumThreshold=3,
        ntpTimeOffsetThreshold=500,
        ntpRootDispersionThreshold=250,
    )
    add_monitor_checked(api, "NTP", sent)


def test_ntp_defaults(api):
    """Create an NTP monitor without threshold fields (use server defaults)."""
    print("\n-- NTP with defaults --")
    sent = dict(
        type=MonitorType.NTP,
        name="[TEST] NTP defaults",
        hostname="time.google.com",
        interval=60,
        maxretries=1,
        retryInterval=60,
    )
    add_monitor_checked(api, "NTP-defaults", sent)


def test_oracledb(api):
    """Create an OracleDB monitor and verify round-trip.

    Points at a non-existent DB so it will be DOWN, but that is expected --
    we are testing that the server accepts and persists the configuration.
    """
    print("\n-- OracleDB monitor type --")
    sent = dict(
        type=MonitorType.ORACLEDB,
        name="[TEST] OracleDB",
        databaseConnectionString="localhost:1521/ORCL",
        databaseQuery="SELECT 1 FROM DUAL",
        interval=60,
        maxretries=1,
        retryInterval=60,
    )
    add_monitor_checked(api, "OracleDB", sent)


def test_bearer_auth(api):
    """Create an HTTP monitor with AuthMethod.BEARER and verify round-trip."""
    print("\n-- AuthMethod.BEARER --")
    sent = dict(
        type=MonitorType.HTTP,
        name="[TEST] Bearer Auth",
        url="http://127.0.0.1:9999/health",
        authMethod=AuthMethod.BEARER,
        bearer_token="test-token-abc123",
        interval=60,
        maxretries=1,
        retryInterval=60,
    )
    add_monitor_checked(api, "Bearer", sent)


def main():
    url, username, password = read_config()

    print(f"connecting to {url}")
    api = connect_with_retry(url)

    try:
        bootstrap(api, username, password)
        guard_version(api)

        test_ntp(api)
        test_ntp_defaults(api)
        test_oracledb(api)
        test_bearer_auth(api)

    finally:
        # Clean up all created monitors
        if created_ids:
            print(f"\n-- cleanup: deleting {len(created_ids)} monitor(s) --")
            for mid in created_ids:
                try:
                    api.delete_monitor(mid)
                    print(f"  deleted id={mid}")
                except Exception as e:
                    print(f"  WARN: could not delete id={mid}: {e}")

        try:
            api.disconnect()
        except Exception:
            pass

    # Summary
    print("\n== summary ==")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"  {passed} passed, {failed} failed")

    if failed:
        print("\nfailed checks:")
        for label, ok, detail in results:
            if not ok:
                print(f"  FAIL  {label}")
                if detail:
                    print(f"        {detail}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
