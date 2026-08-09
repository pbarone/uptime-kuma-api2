"""
Verification for the status-page analytics boundary -- issue #41.

Not a pytest test. The filename deliberately starts with ``live_test_`` rather
than ``test_``: pytest's default discovery collects ``test_*.py`` and
``*_test.py``, so this prefix keeps the script out of every pytest run,
including a bare ``pytest``. Do not rename it. CI is unaffected by this file,
and ``scripts/check_sdist.py`` keeps it out of the published artifact.

SAFETY -- READ FIRST:
    This script CREATES status pages. Point it ONLY at a disposable, throwaway
    Uptime Kuma 2.0.x container that holds nothing you care about.

    It reads the ``UPTIME_KUMA_V1_*`` keys, which is what
    ``scripts/run_disposable_kuma.ps1`` injects for the script it runs. Those
    keys mean "the disposable container", whatever version ``-Image`` selects --
    the ``V1`` in the name dates from when the only throwaway was 1.23.2. They
    are deliberately NOT the ``UPTIME_KUMA_*`` keys in tests/.env that point at
    a real instance, so this cannot reach it by misconfiguration.

    A second guard follows: the server must report a version starting with
    ``2.0``, or the run aborts before creating anything. That prefix is
    hardcoded on purpose. Making it a parameter that defaults to "accept
    anything" would delete the only thing standing between this script and a
    real instance, so a probe for a different version line gets its own file.

What this answers:
    api.py gated the three analytics keys behind ``>= 2.0``, but they first ship
    in 2.1.0 and a 2.0.x server still reads ``googleAnalyticsId``. The fix moves
    the boundary to ``2.1``. Two things were established from upstream source
    and tags but never observed on a running server:

      1. Does a 2.0.x server actually persist ``googleAnalyticsId``? If yes, the
         pre-fix code was losing caller data on every save, not merely sending a
         redundant key.
      2. Does a 2.0.x server REJECT a payload carrying the unknown
         ``analyticsType``, or silently ignore it? This decides severity: reject
         means ``save_status_page`` was outright broken on 2.0.x; ignore means it
         succeeded while dropping analytics config.

    The fix is the same either way -- the boundary comes from upstream source,
    not from this run. This run pins how bad the bug was.

How a key the builder will not send is put on the wire:
    Same technique as ``live_test_v2_only_fields_v1.py``. The builder is the code
    doing the gating, so a gated key cannot reach the server through
    ``save_status_page``. The probe mirrors that method's own sequence and
    injects one key into the built config::

        slug, config, icon, groups = api._build_status_page_data(**base)
        config[key] = value          # inject exactly one key
        api._call('saveStatusPage', (slug, config, icon, groups))

    One key per status page, deliberately. A rejected save names a single
    column, and one bad column fails the whole statement, so probing several at
    once would attribute one rejection to all of them.

Verdicts, one per probe:
    REJECTED   the server returned an error for the payload carrying the key
    ACCEPTED   the key came back on read-back holding the value sent
    ABSENT     the payload succeeded and the key did not come back
    MISMATCH   the key came back holding a value other than the one sent
    NOT_OBSERVED  the probe never ran (an incomplete run must read as
                  incomplete)

    Which verdict is the GOOD news differs per probe, so each one carries its
    own expectation and the summary reports agreement rather than raw counts.

Output is ASCII only. The Windows console defaults to cp1252 and raises
UnicodeEncodeError on check marks, box-drawing characters and arrows, which has
crashed a script mid-run here before. Use PASS / FAIL / ->.

Usage:
    pwsh -File scripts/run_disposable_kuma.ps1 `
        -Script tests/live_test_status_page_analytics_v2_0.py `
        -Image louislam/uptime-kuma:2.0.2 -Port 3025

    That runner starts the container, injects the credentials, removes the
    container in a finally block, and replaces the Docker host and SSH user with
    <docker-host> / <user> placeholders in everything it prints -- so its
    transcript can be pasted into an issue or a PR without scrubbing.

Exit code is 0 when every probe reached a verdict and every verdict matched its
expectation, 1 otherwise.
"""

import json
import os
import sys
import time

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from uptime_kuma_api import Timeout, UptimeKumaApi, UptimeKumaException

REQUIRED_VERSION_PREFIX = "2.0"

REJECTED = "REJECTED"
ACCEPTED = "ACCEPTED"
ABSENT = "ABSENT"
MISMATCH = "MISMATCH"
NOT_OBSERVED = "NOT_OBSERVED"

# Each probe: (key, value, how_sent, expected_verdicts, what_it_means)
#
# how_sent is "builder" when the fixed builder already places the key at 2.0.x
# (so the probe verifies the fix end to end), and "inject" when the key has to
# be put back by hand because the builder correctly withholds it.
PROBES = [
    (
        "googleAnalyticsId", "UA-PROBE-41", "builder", (ACCEPTED,),
        "a 2.0.x server persists it, so the pre-fix code was losing caller data",
    ),
    (
        "analyticsType", "google", "inject", (REJECTED, ABSENT),
        "REJECTED means save_status_page was broken on 2.0.x; "
        "ABSENT means it succeeded and dropped the analytics config",
    ),
    (
        "analyticsScriptUrl", "https://example.invalid/a.js", "inject",
        (REJECTED, ABSENT),
        "same question for the second new key",
    ),
    (
        "showOnlyLastHeartbeat", True, "inject", (REJECTED, ABSENT),
        "confirms its floor is 2.1 rather than 2.0",
    ),
]

# key -> (verdict, detail)
verdicts = {key: (NOT_OBSERVED, "") for key, _, _, _, _ in PROBES}


def equivalent(expected, actual) -> bool:
    """
    Compare a sent value against a returned value, tolerating the type coercion
    Uptime Kuma applies on the way through its database.

    Deliberately tolerant about representation (bool as 0/1, numbers as
    strings, lists as JSON strings) and strict about actual value differences.
    Same helper as the other live_test scripts, kept local so this script
    stands alone.
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
    if not url:
        raise SystemExit(
            "ABORT: UPTIME_KUMA_V1_URL is not set, and this script will not\n"
            "       default to any address.\n"
            "\n"
            "       It CREATES status pages, so it must only ever be pointed at\n"
            "       a disposable Uptime Kuma 2.0.x container. Start one with:\n"
            "\n"
            "         pwsh -File scripts/run_disposable_kuma.ps1 \\\n"
            "             -Script tests/live_test_status_page_analytics_v2_0.py \\\n"
            "             -Image louislam/uptime-kuma:2.0.2 -Port 3025\n"
            "\n"
            "       UPTIME_KUMA_URL is deliberately NOT used: that key points at\n"
            "       a real instance."
        )

    username = os.environ.get("UPTIME_KUMA_V1_USERNAME")
    password = os.environ.get("UPTIME_KUMA_V1_PASSWORD")
    missing = [
        name
        for name, value in (
            ("UPTIME_KUMA_V1_USERNAME", username),
            ("UPTIME_KUMA_V1_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            f"ABORT: {', '.join(missing)} not set.\n"
            "       A fresh container is bootstrapped with these credentials via\n"
            "       need_setup() / setup(); an already-initialised one is logged\n"
            "       into with them. Uptime Kuma requires at least 6 characters."
        )

    return url, username, password


def connect_with_retry(url: str, attempts: int = 20,
                       delay: int = 6) -> UptimeKumaApi:
    """Connect, retrying while the server is up over HTTP but not yet on socket.io.

    ``run_disposable_kuma.ps1`` waits for an HTTP 200 on ``/``, which Uptime Kuma
    answers as soon as it serves the frontend -- before the socket.io server
    accepts connections. On a fresh 2.0.x container the gap is the first-boot
    database migration, and it is long enough that connecting immediately fails
    with "Unexpected response from server". Observed, not hypothesised: the first
    run of this script died exactly there after the runner reported PASS ready.

    Retrying here rather than lengthening the runner's HTTP timeout, because the
    HTTP probe was not wrong -- it was measuring the wrong thing, and only a
    client that speaks socket.io can measure the right one.
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

    A successful socket.io connection is NOT the readiness signal. On a fresh
    2.x container engine.io accepts the connection before the application has
    registered its event handlers, so the first ``needSetup`` call times out
    against a server that is up, connected and silent. Observed on 2.0.2: the
    connect succeeded on the first try and ``need_setup()`` then raised Timeout.

    The real signal is "the server answers an event", which is what this waits
    for. ``needSetup`` is the right event to wait on because it is the first
    call any client makes and needs no authentication.
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


def guard_server_is_2_0(api: UptimeKumaApi) -> None:
    """Refuse to continue unless the server really is a 2.0.x instance.

    Every probe below is about the 2.0.x analytics boundary, so running them
    anywhere else would report meaningless verdicts. This also catches a
    mistargeted URL before anything is created.
    """
    version = api.version
    if not str(version).startswith(REQUIRED_VERSION_PREFIX):
        raise SystemExit(
            f"ABORT: the server reports version {version}, but this run only\n"
            f"       makes sense against Uptime Kuma "
            f"{REQUIRED_VERSION_PREFIX}.x.\n"
            "       Nothing was created. Check UPTIME_KUMA_V1_URL -- it must\n"
            "       point at the disposable 2.0.x container."
        )
    print(f"  PASS  server version starts with {REQUIRED_VERSION_PREFIX} "
          f"({version})")


def build_args_for(sp_data: dict) -> dict:
    """Strip the keys save_status_page strips before calling the builder."""
    args = dict(sp_data)
    for key in ("incident", "incidents", "maintenanceList",
                "autoRefreshInterval", "googleAnalyticsId"):
        args.pop(key, None)
    return args


def probe(api: UptimeKumaApi, key: str, value, how_sent: str, index: int,
          created_slugs: list) -> None:
    """Create a status page, get one key onto the wire, record the verdict."""
    slug = f"sp41-{index:02d}-{key}".lower().replace("_", "-")[:150]
    label = key

    try:
        api.add_status_page(slug, f"Probe {index} {key}")
    except Exception as e:
        verdicts[key] = (NOT_OBSERVED,
                         f"could not create status page: "
                         f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<13} {label} -> add_status_page failed: {e}")
        return
    created_slugs.append(slug)

    try:
        sp_data = api.get_status_page(slug)
    except Exception as e:
        verdicts[key] = (NOT_OBSERVED,
                         f"could not read status page back: "
                         f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<13} {label} -> get_status_page failed: {e}")
        return

    args = build_args_for(sp_data)

    # "builder": hand the value to the builder and let the fixed gate place it,
    # so the probe exercises the shipped path rather than a hand-made payload.
    if how_sent == "builder":
        args[key] = value

    try:
        out_slug, config, icon, groups = api._build_status_page_data(**args)
    except Exception as e:
        verdicts[key] = (NOT_OBSERVED,
                         f"_build_status_page_data failed: "
                         f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<13} {label} -> builder failed: {e}")
        return

    if how_sent == "builder" and key not in config:
        # The builder was supposed to place this key at this version. If it did
        # not, the gate is wrong and saying so is more useful than probing on.
        verdicts[key] = (NOT_OBSERVED,
                         f"the builder did not place {key} at "
                         f"{api.version} -- the gate under test is wrong")
        print(f"  {NOT_OBSERVED:<13} {label} -> builder omitted it at "
              f"{api.version}")
        return

    if how_sent == "inject":
        config[key] = value

    try:
        api._call('saveStatusPage', (out_slug, config, icon, groups))
    except UptimeKumaException as e:
        verdicts[key] = (REJECTED, str(e))
        print(f"  {REJECTED:<13} {label}")
        print(f"                -> {e}")
        return
    except Exception as e:
        verdicts[key] = (NOT_OBSERVED,
                         f"unexpected {type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<13} {label} -> {type(e).__name__}: {e}")
        return

    try:
        got = api.get_status_page(slug)
    except Exception as e:
        verdicts[key] = (NOT_OBSERVED,
                         f"could not read back after save: "
                         f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<13} {label} -> read-back after save failed")
        return

    if key not in got:
        verdicts[key] = (ABSENT,
                         "the save succeeded and the key did not come back")
        print(f"  {ABSENT:<13} {label} (slug={slug})")
        return

    returned = got[key]
    if equivalent(value, returned):
        verdicts[key] = (ACCEPTED, f"sent {value!r}, got {returned!r}")
        print(f"  {ACCEPTED:<13} {label} (slug={slug})  "
              f"sent {value!r}, got {returned!r}")
    else:
        verdicts[key] = (MISMATCH, f"sent {value!r}, got {returned!r}")
        print(f"  {MISMATCH:<13} {label} (slug={slug})  "
              f"sent {value!r}, got {returned!r}")


def main() -> int:
    url, username, password = read_config()

    print(f"Target: {url}")
    print("This script CREATES status pages. Disposable 2.0.x containers ONLY.")
    print()

    print(f"Connecting to {url} ...")
    api = connect_with_retry(url)

    created_slugs = []
    observed_version = "unknown"

    try:
        print()
        print("Step 1: bootstrap and login")
        needs_setup = need_setup_when_ready(api)
        bootstrap(api, needs_setup, username, password)

        print()
        print(f"Step 2: the server must be a "
              f"{REQUIRED_VERSION_PREFIX}.x instance")
        guard_server_is_2_0(api)
        observed_version = api.version

        print()
        print(f"Step 3: probe {len(PROBES)} keys, one per status page")
        for index, (key, value, how_sent, _, _) in enumerate(PROBES, start=1):
            probe(api, key, value, how_sent, index, created_slugs)

    finally:
        print()
        print("Cleanup")
        for slug in created_slugs:
            try:
                api.delete_status_page(slug)
            except Exception as e:
                print(f"  FAILED to delete status page {slug}: "
                      f"{type(e).__name__}: {e}")
        print(f"  deleted {len(created_slugs)} status page(s)")
        api.disconnect()
        print("  disconnected")

    print()
    print("=" * 70)
    print(f"  server {observed_version}, {len(PROBES)} keys probed")
    print("=" * 70)
    print()

    disagreements = []
    print("| Key | Verdict | Expected | Agrees | Detail |")
    print("|---|---|---|---|---|")
    for key, _, _, expected, _ in PROBES:
        verdict, detail = verdicts[key]
        agrees = verdict in expected
        if not agrees:
            disagreements.append((key, verdict, expected))
        print(f"| `{key}` | `{verdict}` | "
              f"{' or '.join(expected)} | "
              f"{'PASS' if agrees else 'FAIL'} | {detail} |")

    print()
    print("What each verdict means:")
    for key, _, _, _, meaning in PROBES:
        print(f"  {key}: {meaning}")

    if disagreements:
        print()
        print("DISAGREEMENTS -- the server did not behave as the fix assumes:")
        for key, verdict, expected in disagreements:
            print(f"  {key}: got {verdict}, expected "
                  f"{' or '.join(expected)}")
        print()
        print("FAIL")
        return 1

    print()
    print("PASS -- every probe matched its expectation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
