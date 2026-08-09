"""
Verification Run for v2-only STATUS PAGE fields -- issue #33, requirement 2.

Mirrors ``live_test_v2_only_fields_v1.py`` for status pages. That script probes
monitor fields; this one probes status-page fields. Both follow the same
structure: bypass the builder (which is the code doing the gating), inject one
candidate field per resource, read it back, and compare.

Not a pytest test. The filename deliberately starts with ``live_test_`` rather
than ``test_``: pytest's default discovery collects ``test_*.py`` and
``*_test.py``, so this prefix keeps the script out of every pytest run,
including a bare ``pytest``. Do not rename it. CI is unaffected by this file,
and ``scripts/check_sdist.py`` keeps it out of the published artifact.

SAFETY -- READ FIRST:
    This script CREATES status pages. Point it ONLY at a disposable, throwaway
    Uptime Kuma 1.23.x container that holds nothing you care about.

    It deliberately does NOT read the ``UPTIME_KUMA_URL`` key the 2.x
    live_test_* scripts use, so it cannot accidentally hit the 2.x instance
    those target. It reads its own ``UPTIME_KUMA_V1_*`` keys and refuses to run
    if the URL is unset -- there is no default.

    A second guard follows: the server must report a version starting with
    ``1.23``, or the run aborts before creating anything.

What this answers:
    The library gates ``showOnlyLastHeartbeat`` and ``rssTitle`` behind a
    ``>= 2.0`` comparison in ``_build_status_page_data`` and withholds them on
    a pre-2.0 server. Whether a 1.x server actually rejects each one was never
    verified. This run exists to find MIS-GATED fields, and its results decide
    the contents of ``_V2_ONLY_STATUS_PAGE_FIELDS``.

How a gated field is put on the wire:
    It cannot go through ``save_status_page``, because that method calls
    ``_build_status_page_data`` which drops gated fields on a pre-2.0 server.
    The probe therefore mirrors ``save_status_page``'s own sequence::

        slug, config, icon, groups = api._build_status_page_data(**base)
        config[field] = value   # inject exactly one gated field
        api._call('saveStatusPage', (slug, config, icon, groups))

    One field per status page, deliberately. A rejected insert names a single
    column, and one bad column fails the whole statement, so probing several at
    once would attribute one rejection to all of them.

Verdicts (requirement 2.1), one per field:
    REJECTED   the server returned an error for the payload carrying the field
    ACCEPTED   the field came back on read-back holding the value sent
    ABSENT     the payload succeeded and the field did not come back
    MISMATCH   the field came back holding a value other than the one sent
    NOT_OBSERVED  the field was never exercised (an incomplete run must read as
                  incomplete, requirement 2.9)

    ACCEPTED is the interesting one: it means the field is mis-gated.
    REJECTED and ABSENT both confirm the gate is correct, for different
    reasons -- the server refuses the column, or silently discards it.

Output is ASCII only. The Windows console defaults to cp1252 and raises
UnicodeEncodeError on check marks, box-drawing characters and arrows, which has
crashed a script mid-run before. Use PASS / FAIL / ->.

Configuration:
    Start a disposable container, for example::

        docker run -d --name kuma-v1-sp -p 3023:3001 louislam/uptime-kuma:1.23.2

    Then set these keys (in the environment or in tests/.env). They are
    referenced by name only; values are never printed by this script:

        UPTIME_KUMA_V1_URL=http://your-disposable-host:3023/
        UPTIME_KUMA_V1_USERNAME=admin
        UPTIME_KUMA_V1_PASSWORD=a-throwaway-password

    A fresh container has no admin user, so the script bootstraps it itself:
    ``need_setup()`` -> ``setup(username, password)`` -> ``login(...)``. Uptime
    Kuma requires a password of at least 6 characters.

    Teardown destroys all state, since the container runs without a volume::

        docker rm -f kuma-v1-sp

Usage:
    .venv/Scripts/python tests/live_test_status_page_v1.py

    Or via the disposable-container runner (preferred -- handles host lookup,
    container lifecycle, and output sanitization)::

        pwsh -File scripts/run_disposable_kuma.ps1 -Script tests/live_test_status_page_v1.py

Exit code is 0 when the run completed and every field reached a verdict. A
mis-gated (ACCEPTED) field does not fail the run -- it is a finding, and the
summary reports it as one.
"""
import json
import os
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException

REQUIRED_VERSION_PREFIX = "1.23"

REJECTED = "REJECTED"
ACCEPTED = "ACCEPTED"
ABSENT = "ABSENT"
MISMATCH = "MISMATCH"
NOT_OBSERVED = "NOT_OBSERVED"

# The two Opt_In_V2_Fields that _build_status_page_data gates behind >= 2.0.
# One probe per status page, one status page per field.
FIELDS = [
    ("showOnlyLastHeartbeat", True),
    ("rssTitle", "probe-rss-title"),
]

# field -> (verdict, detail)
verdicts = {name: (NOT_OBSERVED, "") for name, _ in FIELDS}


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
    """Read the v1 target from the environment, refusing to guess at anything."""
    url = os.environ.get("UPTIME_KUMA_V1_URL")
    if not url:
        raise SystemExit(
            "ABORT: UPTIME_KUMA_V1_URL is not set, and this script will not\n"
            "       default to any address.\n"
            "\n"
            "       It CREATES status pages, so it must only ever be pointed at a\n"
            "       disposable Uptime Kuma 1.23.x container. Start one with:\n"
            "\n"
            "         docker run -d --name kuma-v1-sp -p 3023:3001 \\\n"
            "             louislam/uptime-kuma:1.23.2\n"
            "\n"
            "       Then set UPTIME_KUMA_V1_URL, UPTIME_KUMA_V1_USERNAME and\n"
            "       UPTIME_KUMA_V1_PASSWORD in the environment or tests/.env.\n"
            "       UPTIME_KUMA_URL is deliberately NOT used: that key points at\n"
            "       the 2.x instance the other live_test_* scripts target."
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


def bootstrap(api: UptimeKumaApi, username: str, password: str) -> None:
    """Create the admin account if the container is fresh, then log in."""
    if api.need_setup():
        api.setup(username, password)
        print("  setup() bootstrapped the fresh container")
    else:
        print("  container already set up, skipping setup()")
    api.login(username, password)
    print(f"  login OK -> server reports version {api.version}")


def guard_server_is_v1(api: UptimeKumaApi) -> None:
    """Refuse to continue unless the server really is a 1.23.x instance.

    Every probe below is about v1 behaviour, so running them against a 2.x
    server would report a meaningless all-ACCEPTED. This also catches a
    mistargeted URL before anything is created.
    """
    version = api.version
    if not str(version).startswith(REQUIRED_VERSION_PREFIX):
        raise SystemExit(
            f"ABORT: the server reports version {version}, but this run only\n"
            f"       makes sense against Uptime Kuma {REQUIRED_VERSION_PREFIX}.x.\n"
            "       Nothing was created. Check UPTIME_KUMA_V1_URL -- it must point\n"
            "       at the disposable 1.23.x container, not at a 2.x instance."
        )
    print(f"  PASS  server version starts with {REQUIRED_VERSION_PREFIX} "
          f"({version})")


def probe(api: UptimeKumaApi, field: str, value, index: int,
          created_slugs: list) -> None:
    """Create a status page, inject one gated field, and record the verdict."""
    slug = f"sp-probe-{index:02d}-{field}".lower().replace("_", "-")[:150]
    title = f"Probe {index} {field}"
    label = f"{field}"

    # Step 1: create the status page (add_status_page takes only slug + title)
    try:
        api.add_status_page(slug, title)
    except Exception as e:
        verdicts[field] = (NOT_OBSERVED,
                           f"could not create status page: "
                           f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> add_status_page failed: {e}")
        return
    created_slugs.append(slug)

    # Step 2: fetch the status page to get its id and current config
    try:
        sp_data = api.get_status_page(slug)
    except Exception as e:
        verdicts[field] = (NOT_OBSERVED,
                           f"could not read status page back: "
                           f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> get_status_page failed: {e}")
        return

    # Step 3: build a v1-safe payload via _build_status_page_data, then inject
    # the candidate field into the config dict.
    #
    # _build_status_page_data requires the full set of positional/keyword args.
    # We use the values from get_status_page, stripping the keys that
    # save_status_page also strips before calling the builder.
    build_args = dict(sp_data)
    build_args.pop("incident", None)
    build_args.pop("incidents", None)
    build_args.pop("maintenanceList", None)
    build_args.pop("autoRefreshInterval", None)
    build_args.pop("googleAnalyticsId", None)

    try:
        result_tuple = api._build_status_page_data(**build_args)
    except Exception as e:
        verdicts[field] = (NOT_OBSERVED,
                           f"_build_status_page_data failed: "
                           f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> builder failed: {e}")
        return

    out_slug, config, icon, groups = result_tuple

    # Inject the candidate field -- this key is absent from what the builder
    # produces on a 1.23.x server, and the probe puts it back.
    config[field] = value

    # Step 4: call saveStatusPage directly with the modified config
    try:
        api._call('saveStatusPage', (out_slug, config, icon, groups))
    except UptimeKumaException as e:
        verdicts[field] = (REJECTED, str(e))
        print(f"  {REJECTED:<12} {label}")
        print(f"               -> {e}")
        return
    except Exception as e:
        verdicts[field] = (NOT_OBSERVED,
                           f"unexpected {type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> {type(e).__name__}: {e}")
        return

    # Step 5: read back and compare
    try:
        got = api.get_status_page(slug)
    except Exception as e:
        verdicts[field] = (NOT_OBSERVED,
                           f"could not read status page back after save: "
                           f"{type(e).__name__}: {e}")
        print(f"  {NOT_OBSERVED:<12} {label} -> read-back after save failed")
        return

    if field not in got:
        verdicts[field] = (ABSENT,
                           "the save succeeded and the field did not come back")
        print(f"  {ABSENT:<12} {label} (slug={slug})")
        return

    returned = got[field]
    if equivalent(value, returned):
        verdicts[field] = (ACCEPTED,
                           f"sent {value!r}, got {returned!r}")
        print(f"  {ACCEPTED:<12} {label} (slug={slug})  "
              f"MIS-GATED: sent {value!r}, got {returned!r}")
    else:
        verdicts[field] = (MISMATCH,
                           f"sent {value!r}, got {returned!r}")
        print(f"  {MISMATCH:<12} {label} (slug={slug})  "
              f"sent {value!r}, got {returned!r}")


def main() -> int:
    url, username, password = read_config()

    print(f"Target: {url}")
    print("This script CREATES status pages. Disposable v1 containers ONLY.")
    print()

    print(f"Connecting to {url} ...")
    api = UptimeKumaApi(url)

    created_slugs = []

    try:
        print()
        print("Step 1: bootstrap and login")
        bootstrap(api, username, password)

        print()
        print("Step 2: the server must be a 1.23.x instance")
        guard_server_is_v1(api)
        observed_version = api.version

        print()
        print(f"Step 3: probe {len(FIELDS)} v2-only status-page fields, "
              f"one per status page")
        for index, (field, value) in enumerate(FIELDS, start=1):
            probe(api, field, value, index, created_slugs)

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
        print("  the container itself is disposable: "
              "docker rm -f kuma-v1-sp")

    counts = {}
    for verdict, _ in verdicts.values():
        counts[verdict] = counts.get(verdict, 0) + 1

    print()
    print("=" * 68)
    print(f"  server {observed_version}, {len(FIELDS)} fields probed")
    for verdict in (REJECTED, ABSENT, MISMATCH, ACCEPTED, NOT_OBSERVED):
        if counts.get(verdict):
            print(f"    {verdict:<13} {counts[verdict]}")
    print("=" * 68)

    mis_gated = [f for f, (v, _) in verdicts.items() if v == ACCEPTED]
    if mis_gated:
        print()
        print("MIS-GATED -- a 1.23.x server accepted and returned these, so they")
        print("are not v2-only and must be left OUT of _V2_ONLY_STATUS_PAGE_FIELDS:")
        for field in mis_gated:
            print(f"  {field}")

    unobserved = [f for f, (v, _) in verdicts.items() if v == NOT_OBSERVED]
    if unobserved:
        print()
        print("NOT OBSERVED -- this run is incomplete for:")
        for field in unobserved:
            print(f"  {field}  {verdicts[field][1]}")

    print()
    print("Verdict table:")
    print()
    print("| Field | Verdict | Detail |")
    print("|---|---|---|")
    for field, value in FIELDS:
        verdict, detail = verdicts[field]
        print(f"| `{field}` | `{verdict}` | {detail} |")

    return 1 if unobserved else 0


if __name__ == "__main__":
    sys.exit(main())
