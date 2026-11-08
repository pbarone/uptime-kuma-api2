"""
Verification for the late monitor-field floors -- issue #71.

Not a pytest test. The filename deliberately starts with ``live_test_`` rather
than ``test_``: pytest's default discovery collects ``test_*.py`` and
``*_test.py``, so this prefix keeps the script out of every pytest run,
including a bare ``pytest``. Do not rename it. CI is unaffected by this file,
and ``scripts/check_sdist.py`` keeps it out of the published artifact.

SAFETY -- READ FIRST:
    This script CREATES monitors. Point it ONLY at a disposable, throwaway
    Uptime Kuma container that holds nothing you care about.

    It reads the ``UPTIME_KUMA_V1_*`` keys, which is what
    ``scripts/run_disposable_kuma.ps1`` injects for the script it runs. Those
    keys mean "the disposable container", whatever version ``-Image`` selects.
    They are deliberately NOT the ``UPTIME_KUMA_*`` keys in tests/.env that
    point at a real instance, so this cannot reach it by misconfiguration.

    A second guard follows: the server must report one of the exact versions in
    ``PROBED_VERSIONS``, or the run aborts before creating anything. The list is
    the releases either side of each floor this run checks, not a prefix, so a
    mistargeted URL fails closed.

What this answers:
    The registry floored 13 fields at ``2.0`` that the upstream migrations only
    add in 2.1 or 2.4. The fix moves those floors. This run checks the floors
    against real servers, one field per monitor:

      * on the newest release BELOW a field's floor (2.0.2 or 2.3.2) the server
        must REJECT or drop (ABSENT) the field, so withholding it is correct;
      * on the floor release itself (2.1.0 or 2.4.0) the server must ACCEPT and
        return it, so the floor is not set too high.

    The expectation for each field is derived from the registry
    (``_V2_ONLY_MONITOR_FIELDS``), so a run disagrees exactly when the registry
    is wrong for the server under test.

How a gated field is put on the wire:
    Same technique as ``live_test_v2_only_fields_v1.py``. ``add_monitor`` is the
    code doing the gating, so the probe mirrors its sequence and injects one
    field into the built payload::

        data = api._build_monitor_data(name=name, **base)
        _convert_monitor_input(data)
        _check_arguments_monitor(data)
        data[field] = value                  # inject exactly one field
        api._call('add', data)

    One field per monitor, deliberately. A rejected insert names a single
    column, and one bad column fails the whole statement, so probing several at
    once would attribute one rejection to all of them.

Output is ASCII only. The Windows console defaults to cp1252 and raises
UnicodeEncodeError on check marks, box-drawing characters and arrows. Use
PASS / FAIL / ->.

Usage (one run per image):
    pwsh -File scripts/run_disposable_kuma.ps1 `
        -Script tests/live_test_late_monitor_fields_v2.py `
        -Image louislam/uptime-kuma:2.0.2 -Port 3026 `
        -DockerEnv UPTIME_KUMA_DB_TYPE=sqlite

    Repeat with 2.1.0, 2.3.2 and 2.4.0. The ``-DockerEnv`` is required for any
    2.x image; see the runner's own notes.

Exit code is 0 when every probe reached a verdict and every verdict matched the
registry, 1 otherwise.
"""
import json
import os
import sys
import time
import warnings

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from packaging.version import parse as parse_version

from uptime_kuma_api import (
    MonitorType,
    Timeout,
    UnsupportedFieldWarning,
    UptimeKumaApi,
    UptimeKumaException,
)
from uptime_kuma_api.api import (
    _V2_ONLY_MONITOR_FIELDS,
    _check_arguments_monitor,
    _convert_monitor_input,
)

PROBED_VERSIONS = ("2.0.2", "2.1.0", "2.3.2", "2.4.0")

REJECTED = "REJECTED"
ACCEPTED = "ACCEPTED"
ABSENT = "ABSENT"
MISMATCH = "MISMATCH"
NOT_OBSERVED = "NOT_OBSERVED"

BASE = {
    MonitorType.HTTP: {
        "type": MonitorType.HTTP,
        "url": "http://127.0.0.1",
    },
    MonitorType.SNMP: {
        "type": MonitorType.SNMP,
        "hostname": "127.0.0.1",
        "port": 161,
        "snmpOid": "1.3.6.1.2.1.1.1.0",
        "snmpVersion": "2c",
    },
}

# The 13 fields issue #71 moves, each with a monitor type it applies to and a
# plausible value.
FIELDS = [
    ("wsSubprotocol", MonitorType.HTTP, "probe-subprotocol"),
    ("wsIgnoreSecWebsocketAcceptHeader", MonitorType.HTTP, True),
    ("domainExpiryNotification", MonitorType.HTTP, True),
    ("saveResponse", MonitorType.HTTP, True),
    ("saveErrorResponse", MonitorType.HTTP, True),
    ("responseMaxLength", MonitorType.HTTP, 1000),
    ("retryOnlyOnStatusCodeFailure", MonitorType.HTTP, True),
    ("screenshot_delay", MonitorType.HTTP, 5),
    ("snmp_v3_username", MonitorType.SNMP, "probe-user"),
    ("subtype", MonitorType.HTTP, "probe"),
    ("protocol", MonitorType.HTTP, "https"),
    ("bearer_token", MonitorType.HTTP, "probe-token"),
    ("gamedigToken", MonitorType.HTTP, "probe-gamedig-token"),
]

# field -> (verdict, detail)
verdicts = {name: (NOT_OBSERVED, "") for name, _, _ in FIELDS}


def equivalent(expected, actual) -> bool:
    """
    Compare a sent value against a returned value, tolerating the type coercion
    Uptime Kuma applies on the way through its database (bool as 0/1, numbers
    as strings). Same helper as live_test_v2_only_fields_v1.py, kept local so
    this script stands alone.
    """
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


def read_config() -> tuple:
    """Read the disposable target from the environment, refusing to guess."""
    url = os.environ.get("UPTIME_KUMA_V1_URL")
    username = os.environ.get("UPTIME_KUMA_V1_USERNAME")
    password = os.environ.get("UPTIME_KUMA_V1_PASSWORD")
    missing = [
        name
        for name, value in (
            ("UPTIME_KUMA_V1_URL", url),
            ("UPTIME_KUMA_V1_USERNAME", username),
            ("UPTIME_KUMA_V1_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            f"ABORT: {', '.join(missing)} not set, and this script will not\n"
            "       default to any address. It CREATES monitors, so it must\n"
            "       only ever be pointed at a disposable container. Use\n"
            "       scripts/run_disposable_kuma.ps1, which sets these keys."
        )
    return url, username, password


def connect_with_retry(url: str, attempts: int = 20,
                       delay: int = 6) -> UptimeKumaApi:
    """Connect, retrying while the server is up over HTTP but not on socket.io.

    The runner's HTTP probe passes before a fresh 2.x container finishes its
    first-boot migration and accepts socket.io connections. Same helper as
    live_test_status_page_analytics_v2_0.py, kept local so this script stands
    alone.
    """
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
        f"ABORT: could not connect to {url} after {attempts} attempts "
        f"over ~{attempts * delay}s.\n"
        f"       Last error: {type(last).__name__}: {last}\n"
        "       Nothing was created."
    )


def need_setup_when_ready(api: UptimeKumaApi, attempts: int = 15,
                          delay: int = 6) -> bool:
    """Poll ``needSetup`` until the server answers, and return what it says.

    A successful socket.io connection is NOT the readiness signal: on a fresh
    2.x container engine.io accepts the connection before the application has
    registered its event handlers. Same helper as
    live_test_status_page_analytics_v2_0.py, kept local so this script stands
    alone.
    """
    last = None
    for attempt in range(1, attempts + 1):
        try:
            answer = api.need_setup()
            if attempt > 1:
                print(f"  server answering events on attempt {attempt}")
            return answer
        except Timeout as e:
            last = e
            if attempt == attempts:
                break
            print(f"  attempt {attempt}/{attempts}: connected but not "
                  f"answering events yet, retrying in {delay}s")
            time.sleep(delay)
    raise SystemExit(
        f"ABORT: connected, but the server never answered needSetup after "
        f"{attempts} attempts.\n"
        f"       Last error: {type(last).__name__}: {last}\n"
        "       Nothing was created."
    )


def bootstrap(api: UptimeKumaApi, needs_setup: bool,
              username: str, password: str) -> None:
    """Create the admin account if the container is fresh, then log in."""
    if needs_setup:
        api.setup(username, password)
        print("  setup() bootstrapped the fresh container")
    else:
        print("  container already set up, skipping setup()")
    api.login(username, password)
    print(f"  login OK -> server reports version {api.version}")


def guard_server_version(api: UptimeKumaApi) -> None:
    """Refuse to continue unless the server is one of the probed releases."""
    version = api.version
    if version not in PROBED_VERSIONS:
        # main()'s finally block disconnects, so do not disconnect here as well.
        raise SystemExit(
            f"ABORT: the server reports version {version}, but this run only\n"
            f"       makes sense against {', '.join(PROBED_VERSIONS)}.\n"
            "       Nothing was created. Check UPTIME_KUMA_V1_URL."
        )
    print(f"  PASS  server version {version} is a probed release")


def expected_verdicts(field: str, version: str) -> tuple:
    """What a correct registry predicts this server does with the field.

    The decisive split is whether the insert is REJECTED. ABSENT is allowed on
    both sides: below the floor it means the server dropped the field, and at
    or above it the insert succeeded but ``Monitor.toJSON`` does not echo the
    field. Observed on 2.1.0-2.4.0 for ``screenshot_delay`` (toJSON gains it
    later) and ``snmp_v3_username`` (toJSON never returns it).
    """
    floor = _V2_ONLY_MONITOR_FIELDS[field].floor
    if parse_version(version) < parse_version(floor):
        return (REJECTED, ABSENT)
    return (ACCEPTED, ABSENT)


def build_base_payload(api: UptimeKumaApi, name: str, base: dict) -> dict:
    """Assemble the payload add_monitor would send, with no probed field."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnsupportedFieldWarning)
        data = api._build_monitor_data(name=name, **base)
    _convert_monitor_input(data)
    _check_arguments_monitor(data)
    return data


def probe(api: UptimeKumaApi, field: str, type_, value, index: int,
          created: list) -> None:
    """Put one field on the wire and record the server's verdict."""
    name = f"late-fields-probe-{index:02d}-{field}"[:150]
    label = f"{field} ({type_})"

    try:
        data = build_base_payload(api, name, BASE[type_])
    except Exception as e:
        verdicts[field] = (NOT_OBSERVED, f"could not build a base payload: "
                                         f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> base payload failed")
        return

    data[field] = value

    try:
        r = api._call("add", data)
    except Timeout as e:
        # Timeout subclasses UptimeKumaException, so it must be caught first:
        # a transport failure is not the server rejecting the column.
        verdicts[field] = (NOT_OBSERVED, f"timeout: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> Timeout: {e}")
        return
    except UptimeKumaException as e:
        verdicts[field] = (REJECTED, str(e))
        print(f"  {REJECTED:<12} {label}")
        print(f"               -> {e}")
        return
    except Exception as e:
        verdicts[field] = (NOT_OBSERVED, f"unexpected {type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> {type(e).__name__}: {e}")
        return

    monitor_id = r.get("monitorID")
    if monitor_id is None:
        verdicts[field] = (NOT_OBSERVED, f"add returned no monitorID: {r!r}")
        print(f"  {NOT_OBSERVED:<12} {label} -> no monitorID in {r!r}")
        return
    created.append(monitor_id)

    try:
        got = api.get_monitor(monitor_id)
    except Exception as e:
        verdicts[field] = (NOT_OBSERVED, f"could not read the monitor back: "
                                         f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> read-back failed")
        return

    if field not in got:
        verdicts[field] = (ABSENT,
                           "the add succeeded and the field did not come back")
        print(f"  {ABSENT:<12} {label} (id={monitor_id})")
        return

    returned = got[field]
    if equivalent(value, returned):
        verdicts[field] = (ACCEPTED, f"sent {value!r}, got {returned!r}")
        print(f"  {ACCEPTED:<12} {label} (id={monitor_id})")
    else:
        verdicts[field] = (MISMATCH, f"sent {value!r}, got {returned!r}")
        print(f"  {MISMATCH:<12} {label} (id={monitor_id})  "
              f"sent {value!r}, got {returned!r}")


def main() -> int:
    url, username, password = read_config()

    print(f"Target: {url}")
    print("This script CREATES monitors. Disposable containers ONLY.")
    print()

    print(f"Connecting to {url} ...")
    api = connect_with_retry(url)

    created = []
    observed_version = None

    try:
        print()
        print("Step 1: bootstrap and login")
        needs_setup = need_setup_when_ready(api)
        bootstrap(api, needs_setup, username, password)

        print()
        print("Step 2: the server must be a probed release")
        guard_server_version(api)
        observed_version = api.version

        print()
        print(f"Step 3: probe {len(FIELDS)} fields, one per monitor")
        for index, (field, type_, value) in enumerate(FIELDS, start=1):
            probe(api, field, type_, value, index, created)

    finally:
        print()
        print("Cleanup")
        for monitor_id in created:
            try:
                api.delete_monitor(monitor_id)
            except Exception as e:
                print(f"  FAILED to delete monitor {monitor_id}: "
                      f"{type(e).__name__}: {e}")
        print(f"  deleted {len(created)} monitor(s)")
        api.disconnect()
        print("  disconnected")

    disagreements = []
    print()
    print("=" * 68)
    print(f"  server {observed_version}, {len(FIELDS)} fields probed")
    print("=" * 68)
    print()
    print("| Field | Floor | Verdict | Matches registry |")
    print("|---|---|---|---|")
    for field, _, _ in FIELDS:
        verdict, _ = verdicts[field]
        ok = verdict in expected_verdicts(field, observed_version)
        if not ok:
            disagreements.append(field)
        print(f"| `{field}` | {_V2_ONLY_MONITOR_FIELDS[field].floor} | "
              f"`{verdict}` | {'PASS' if ok else 'FAIL'} |")

    if disagreements:
        print()
        print("FAIL -- these verdicts disagree with the registry floor:")
        for field in disagreements:
            print(f"  {field}  {verdicts[field][1]}")
        return 1
    print()
    print("PASS -- every verdict matches the registry floor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
