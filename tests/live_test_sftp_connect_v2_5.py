"""
End-to-end check of the SFTP monitor type against a real SFTP server.

Not a pytest test. Named ``live_test_`` so pytest discovery ignores it.

Why this exists: live_test_sftp_and_providers_v2_5.py proves the server stores
what the library sends. It cannot prove the server READS the same field names
when it connects. This script does: it points SFTP monitors at a throwaway SFTP
server and checks the heartbeat each one produces.

SAFETY:
    Creates monitors on a DISPOSABLE Uptime Kuma container only. It reads
    UPTIME_KUMA_V1_URL / _USERNAME / _PASSWORD (set by run_disposable_kuma.ps1),
    deliberately NOT the tests/.env keys. Aborts unless the server is 2.5.4+.
    Monitors are deleted in a finally block.

Run it through scripts/run_disposable_kuma_sftp.ps1, which starts the SFTP
server, generates the credentials and key pairs, runs the Kuma container
through run_disposable_kuma.ps1, and removes everything again::

    pwsh -File scripts/run_disposable_kuma_sftp.ps1 `
        -KumaImage louislam/uptime-kuma:2.5.4 -Port 3026

Environment (set by the wrapper):
    SFTP_TEST_HOST, SFTP_TEST_PORT, SFTP_TEST_USER, SFTP_TEST_PASSWORD,
    SFTP_TEST_KEY_FILE            private key, no passphrase
    SFTP_TEST_KEY_PP_FILE         private key, passphrase-protected
    SFTP_TEST_PASSPHRASE          passphrase for the key above

Output is ASCII only (Windows cp1252 safety). Exit code 0 only if every check
passed.
"""
import os
import sys
import time

sys.path.insert(0, ".")

from uptime_kuma_api import MonitorType, UptimeKumaApi
from uptime_kuma_api.exceptions import Timeout

results = []
created_ids = []

UP = 1
DOWN = 0


def record(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if detail:
        print(f"          {detail}")
    return ok


def need_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"ABORT: {name} is not set. Run this through "
            "scripts/run_disposable_kuma_sftp.ps1."
        )
    return value


def connect_with_retry(url, attempts=20, delay=6):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return UptimeKumaApi(url)
        except Exception as e:
            last = e
            if attempt == attempts:
                break
            print(f"  attempt {attempt}/{attempts} not ready yet, "
                  f"retrying in {delay}s")
            time.sleep(delay)
    raise SystemExit(f"ABORT: could not connect after {attempts} attempts: "
                     f"{type(last).__name__}: {last}")


def bootstrap(api, username, password):
    for attempt in range(1, 16):
        try:
            needs_setup = api.need_setup()
            break
        except Timeout:
            if attempt == 15:
                raise SystemExit("ABORT: server never answered needSetup.")
            print(f"  attempt {attempt}/15: not answering events yet")
            time.sleep(6)
    if needs_setup:
        api.setup(username, password)
    api.login(username, password)
    record("login", True, f"server version {api.version}")


def wait_for_beat(api, monitor_id, timeout=75):
    """Return the first heartbeat of a monitor, or None on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        beats = api.get_monitor_beats(monitor_id, 1)
        if beats:
            return beats[-1]
        time.sleep(3)
    return None


def status_of(beat):
    status = beat.get("status")
    return getattr(status, "value", status)


def run_case(api, label, expect, msg_contains=None, **overrides):
    """Create one SFTP monitor, read its first heartbeat, compare to expect."""
    sent = dict(
        type=MonitorType.SFTP,
        name=f"[TEST] {label}",
        hostname=HOST,
        port=PORT,
        sshUsername=USER,
        interval=60,
        maxretries=0,
        retryInterval=60,
    )
    sent.update(overrides)
    try:
        r = api.add_monitor(**sent)
    except Exception as e:
        return record(label, False, f"add_monitor failed: {e}")
    monitor_id = r.get("monitorID")
    created_ids.append(monitor_id)
    beat = wait_for_beat(api, monitor_id)
    if beat is None:
        return record(label, False, "no heartbeat within the timeout")
    got = status_of(beat)
    msg = str(beat.get("msg"))
    ok = got == expect
    if ok and msg_contains is not None:
        ok = msg_contains.lower() in msg.lower()
    expected = "UP" if expect == UP else "DOWN"
    return record(label, ok, f"expected {expected}; heartbeat status={got} "
                             f"msg={msg!r}")


def main():
    global HOST, PORT, USER
    url = need_env("UPTIME_KUMA_V1_URL")
    username = need_env("UPTIME_KUMA_V1_USERNAME")
    password = need_env("UPTIME_KUMA_V1_PASSWORD")
    HOST = need_env("SFTP_TEST_HOST")
    PORT = int(need_env("SFTP_TEST_PORT"))
    USER = need_env("SFTP_TEST_USER")
    sftp_password = need_env("SFTP_TEST_PASSWORD")
    with open(need_env("SFTP_TEST_KEY_FILE"), encoding="ascii") as f:
        key = f.read()
    with open(need_env("SFTP_TEST_KEY_PP_FILE"), encoding="ascii") as f:
        key_pp = f.read()
    passphrase = need_env("SFTP_TEST_PASSPHRASE")

    print(f"connecting to {url}")
    # A 2.x container can drop the first socket while it finishes booting, so
    # connect and bootstrap are retried together, not just the connect.
    api = None
    for attempt in range(1, 6):
        api = connect_with_retry(url)
        try:
            bootstrap(api, username, password)
            break
        except SystemExit:
            raise
        except Exception as e:
            print(f"  bootstrap attempt {attempt}/5 failed: "
                  f"{type(e).__name__}: {e}")
            try:
                api.disconnect()
            except Exception:
                pass
            if attempt == 5:
                raise SystemExit("ABORT: could not bootstrap the server.")
            time.sleep(6)
    try:
        from packaging.version import parse as parse_version
        if parse_version(str(api.version)) < parse_version("2.5.4"):
            raise SystemExit(f"ABORT: server {api.version} is below 2.5.4.")

        print("\n-- password authentication --")
        run_case(api, "password correct -> UP", UP,
                 sshAuthMethod="password", sshPassword=sftp_password)
        run_case(api, "password omitted method -> UP", UP,
                 sshPassword=sftp_password)
        run_case(api, "password wrong -> DOWN", DOWN,
                 sshAuthMethod="password", sshPassword="not-the-password")

        print("\n-- private key authentication --")
        run_case(api, "key -> UP", UP,
                 sshAuthMethod="privateKey", sshPrivateKey=key)
        run_case(api, "key with passphrase -> UP", UP,
                 sshAuthMethod="privateKey", sshPrivateKey=key_pp,
                 sshPassphrase=passphrase)
        run_case(api, "key with wrong passphrase -> DOWN", DOWN,
                 sshAuthMethod="privateKey", sshPrivateKey=key_pp,
                 sshPassphrase="wrong")

        print("\n-- sftpPath --")
        run_case(api, "existing path -> UP", UP,
                 sshAuthMethod="password", sshPassword=sftp_password,
                 sftpPath="/config")
        run_case(api, "missing path -> DOWN", DOWN,
                 msg_contains="does not exist",
                 sshAuthMethod="password", sshPassword=sftp_password,
                 sftpPath="/no/such/path")

        print("\n-- unreachable port --")
        run_case(api, "closed port -> DOWN", DOWN,
                 sshAuthMethod="password", sshPassword=sftp_password,
                 port=PORT + 7)
    finally:
        if created_ids:
            print("\n-- cleanup --")
        for mid in created_ids:
            try:
                api.delete_monitor(mid)
                print(f"  deleted monitor id={mid}")
            except Exception as e:
                print(f"  WARN: could not delete monitor id={mid}: {e}")
        try:
            api.disconnect()
        except Exception:
            pass

    print("\n== summary ==")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"  {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
