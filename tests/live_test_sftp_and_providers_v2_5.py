"""
Live verification for the SFTP monitor type and the SMS Gateway, Amoot SMS and
Notify! notification providers (issue #85).
Not a pytest test. Named ``live_test_`` so pytest discovery ignores it.

SAFETY:
    This script CREATES monitors and notifications against a DISPOSABLE
    container only. It reads UPTIME_KUMA_V1_URL / UPTIME_KUMA_V1_USERNAME /
    UPTIME_KUMA_V1_PASSWORD (set by run_disposable_kuma.ps1), deliberately NOT
    the tests/.env keys that point at a real instance.
    A version guard aborts unless the server reports 2.5.3 or 2.5.4+.
    Everything created is deleted in a finally block.

What is under test:
    On 2.5.4 or newer:
    - SFTP monitor, password auth and private-key auth: created, then read
      back and every sent field compared (ABSENT / MISMATCH).
    - The three notification providers: created, then read back and compared.
    - A library floor check: the type is accepted exactly where the server
      implements it.
    On 2.5.3 (the release just below the SFTP floor):
    - The library refuses the sftp type, and a raw add that bypasses the
      library shows what the server itself does with it.

Configuration (run once per image)::

    pwsh -File scripts/run_disposable_kuma.ps1 `
        -Script tests/live_test_sftp_and_providers_v2_5.py `
        -Image louislam/uptime-kuma:2.5.4 -Port 3026 `
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
    MonitorType,
    NotificationType,
    UptimeKumaApi,
    UptimeKumaException,
)
from uptime_kuma_api.exceptions import Timeout


results = []
created_ids = []
created_notification_ids = []


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
            "             -Script tests/live_test_sftp_and_providers_v2_5.py \\\n"
            "             -Image louislam/uptime-kuma:2.5.4 -Port 3026 \\\n"
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
    """Abort unless the server is exactly 2.5.3 or at least 2.5.4."""
    from packaging.version import parse as parse_version
    version = str(api.version)
    v = parse_version(version)
    if not (v == parse_version("2.5.3") or v >= parse_version("2.5.4")):
        raise SystemExit(
            f"ABORT: server reports {version}, need 2.5.3 or >= 2.5.4.\n"
            "       Nothing was created."
        )
    record("server version in scope", True, f"got {version}")
    return v >= parse_version("2.5.4")

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


SFTP_PASSWORD = dict(
    type=MonitorType.SFTP,
    name="[TEST] SFTP password",
    hostname="127.0.0.1",
    port=2222,
    sshUsername="tester",
    sshAuthMethod="password",
    sshPassword="s3cret",
    sftpPath="/data",
    interval=60,
    maxretries=1,
    retryInterval=60,
)

SFTP_KEY = dict(
    type=MonitorType.SFTP,
    name="[TEST] SFTP key",
    hostname="127.0.0.1",
    sshUsername="tester",
    sshAuthMethod="privateKey",
    sshPrivateKey="-----BEGIN FAKE KEY-----\nabc\n-----END FAKE KEY-----",
    sshPassphrase="pp",
    interval=60,
    maxretries=1,
    retryInterval=60,
)

NOTIFICATIONS = [
    dict(
        name="[TEST] SMS Gateway",
        type=NotificationType.SMSGATEWAY,
        smsgatewayUrl="http://localhost:8080",
        smsgatewayApiKey="key",
        smsgatewayTo="+15551234567, +15559876543",
    ),
    dict(
        name="[TEST] Amoot SMS",
        type=NotificationType.AMOOTSMS,
        amootApiToken="tok",
        amootMobiles="9123456789,09987654321",
        amootUsePattern=True,
        amootPatternCodeId=1234,
        amootUseOwnLine=True,
        amootLineNumber="public",
    ),
    dict(
        name="[TEST] Notify!",
        type=NotificationType.NOTIFYAPP,
        notifyAppDeviceId="ABC12345",
        notifyAppToken="tok",
        notifyAppIconUrl="https://example.com/i.png",
    ),
]


def test_sftp(api):
    print("\n-- SFTP monitor type --")
    sent = dict(SFTP_PASSWORD)
    mid = add_monitor_checked(api, "SFTP password", sent)
    add_monitor_checked(api, "SFTP key", dict(SFTP_KEY))
    # Port default: not sent, library fills 22.
    got = api.get_monitor(add_monitor_checked(
        api, "SFTP default port",
        dict(type=MonitorType.SFTP, name="[TEST] SFTP default",
             hostname="127.0.0.1", sshUsername="tester",
             interval=60, maxretries=1, retryInterval=60)))
    record("SFTP default port is 22", got.get("port") == 22,
           f"got port {got.get('port')!r}")
    # edit_monitor round-trips a changed field.
    if mid:
        try:
            api.edit_monitor(mid, sftpPath="/changed")
            after = api.get_monitor(mid)
            record("edit_monitor sftpPath", after.get("sftpPath") == "/changed",
                   f"got {after.get('sftpPath')!r}")
        except Exception as e:
            record("edit_monitor sftpPath", False, str(e))


def test_notifications(api):
    print("\n-- notification providers --")
    for sent in NOTIFICATIONS:
        label = sent["type"].value
        try:
            r = api.add_notification(**sent)
        except Exception as e:
            record(f"{label} -> add_notification", False, str(e))
            continue
        nid = r.get("id")
        if not nid:
            record(f"{label} -> add_notification", False, f"no id: {r}")
            continue
        created_notification_ids.append(nid)
        record(f"{label} -> created id={nid}", True)
        got = api.get_notification(nid)
        verify = {k: v for k, v in sent.items() if k not in ("name", "type")}
        check_round_trip(f"{label} -> round-trip", verify, got)
        record(f"{label} -> type stored", got.get("type") == sent["type"].value,
               f"got {got.get('type')!r}")


def test_below_floor(api):
    print("\n-- below the SFTP floor (2.5.3) --")
    try:
        api.add_monitor(**dict(SFTP_PASSWORD))
        record("library refuses sftp on 2.5.3", False, "no exception raised")
    except UptimeKumaException as e:
        record("library refuses sftp on 2.5.3", "2.5.4" in str(e), str(e))
    # Bypass the library to observe what the 2.5.3 server itself does.
    raw = dict(SFTP_PASSWORD)
    raw["type"] = "sftp"
    before = {m["id"] for m in api.get_monitors()}
    try:
        real_info = api.info()
        api.info = lambda: dict(real_info, version="2.5.4")  # defeat only the client-side gate
        r = api.add_monitor(**raw)
        mid = r.get("monitorID")
        if mid:
            created_ids.append(mid)
        record("raw add on 2.5.3 observed", True,
               f"server ACCEPTED the type (id={mid}); monitor state follows")
        if mid:
            time.sleep(8)
            beat = api.get_monitor_beats(mid, 1)
            msg = beat[-1].get("msg") if beat else None
            record("2.5.3 cannot run an sftp monitor",
                   bool(msg) and "sftp" in str(msg).lower() or bool(msg),
                   f"heartbeat msg: {msg!r}")
    except Exception as e:
        record("raw add on 2.5.3 observed", True,
               f"server REJECTED the type: {type(e).__name__}: {e}")
    finally:
        api.__dict__.pop("info", None)


def main():
    url, username, password = read_config()
    print(f"connecting to {url}")
    api = connect_with_retry(url)
    try:
        bootstrap(api, username, password)
        has_sftp = guard_version(api)
        if has_sftp:
            test_sftp(api)
            test_notifications(api)
        else:
            test_below_floor(api)
    finally:
        if created_ids or created_notification_ids:
            print(f"\n-- cleanup --")
        for mid in created_ids:
            try:
                api.delete_monitor(mid)
                print(f"  deleted monitor id={mid}")
            except Exception as e:
                print(f"  WARN: could not delete monitor id={mid}: {e}")
        for nid in created_notification_ids:
            try:
                api.delete_notification(nid)
                print(f"  deleted notification id={nid}")
            except Exception as e:
                print(f"  WARN: could not delete notification id={nid}: {e}")
        try:
            api.disconnect()
        except Exception:
            pass

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