"""
Verification for get_important_heartbeats() on 2.x -- issue #63 / PR #77.

Not a pytest test. The filename deliberately starts with ``live_test_`` rather
than ``test_``: pytest's default discovery collects ``test_*.py`` and
``*_test.py``, so this prefix keeps the script out of every pytest run,
including a bare ``pytest``. Do not rename it. CI is unaffected by this file,
and ``scripts/check_sdist.py`` keeps it out of the published artifact.

SAFETY -- READ FIRST:
    This script CREATES two monitors named ``live-check-63-tcp`` and
    ``live-check-63-http`` and deletes them again in a ``finally`` block. Point
    it ONLY at a disposable container that holds nothing you care about.

    It reads the ``UPTIME_KUMA_V1_*`` keys, which is what
    ``scripts/run_disposable_kuma.ps1`` injects for the script it runs. Those
    keys mean "the disposable container", whatever version ``-Image`` selects.
    They are deliberately NOT the ``UPTIME_KUMA_*`` keys in tests/.env that
    point at a real instance, so this cannot reach it by misconfiguration.

    A second guard follows: the server must report 1.23.x, 2.0.x or 2.5.x, or
    the run aborts before creating anything. This is a deliberate exception to
    the one-version-line-per-script convention: the assertions branch on 1.x
    versus 2.x (paged calls on 2.x, the event cache on 1.x), so one script has
    to accept both lines. It still fails closed, because the allowlist is a
    fixed set of prefixes rather than "accept anything".

What this answers:
    ``get_important_heartbeats()`` waited for an ``importantHeartbeatList``
    event that 2.x no longer pushes at login, so it raised ``Timeout`` (#63).
    The fix (PR #77) makes it behave per server line:

      * 2.x: one ``monitorImportantHeartbeatListPaged`` call per monitor, with
        data ``(monitor_id, 0, 500)``; rows keep the server's keys, including
        ``monitorID``;
      * 1.x: unchanged, the event cache is used and there are zero paged calls.

    The checks spy on ``_call`` to count the paged calls and compare their data,
    compare each processed row set to a raw paged read, and check row shape
    (``monitorID``, ``important``, ``status`` as MonitorStatus, ``time``).

Two phases run the same checks, and both are needed:
    same-session      the connection that created the monitors. It has seen live
                      heartbeat events, which populate the event cache, so it
                      passes even on the unfixed library and masks the bug.
    fresh-connection  a second connection opened after the heartbeats exist.
                      This is the situation issue #63 describes, and the only
                      phase that reproduces it.

Known gap:
    Each monitor produces a single important heartbeat during the run, so
    newest-first ordering and the 500 row cap are checked but barely exercised
    live. Behaviour with more than 500 important beats, and servers between
    2.0.2 and 2.5.9, are not covered.

How to read the result:
    Exit 0 only if every check passed. Running it against an unfixed library on
    2.x is EXPECTED to exit 1, in the fresh-connection phase (Timeout); that is
    the script doing its job, not a broken script.

Output is ASCII only. The Windows console defaults to cp1252 and raises
UnicodeEncodeError on check marks, box-drawing characters and arrows. Use
PASS / FAIL / ->.

Usage (one run per image):
    pwsh -File scripts/run_disposable_kuma.ps1 `
        -Script tests/live_test_important_heartbeats.py `
        -Image louislam/uptime-kuma:1.23.2 -Port 3023

    pwsh -File scripts/run_disposable_kuma.ps1 `
        -Script tests/live_test_important_heartbeats.py `
        -Image louislam/uptime-kuma:2.5.9 -Port 3025 `
        -DockerEnv UPTIME_KUMA_DB_TYPE=sqlite

    The ``-DockerEnv`` is required for any 2.x image, or socket.io never
    mounts; see the runner's own notes.
"""

import os
import sys
import time

import uptime_kuma_api
from uptime_kuma_api import MonitorStatus, MonitorType, Timeout, UptimeKumaApi

ALLOWED = ("1.23.", "2.0.", "2.5.")
PAGED = "monitorImportantHeartbeatListPaged"

results = []
VERSION = "?"


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'} [{VERSION}] {name}" + (f" -> {detail}" if detail else ""))


def connect_with_retry(url, budget=150, delay=6):
    deadline = time.time() + budget
    attempt = 0
    last = None
    while time.time() < deadline:
        attempt += 1
        api = None
        try:
            api = UptimeKumaApi(url)
            # 2.x can accept the socket before it answers events
            api.need_setup()
            print(f"  connected on attempt {attempt}")
            return api
        except Exception as e:
            last = e
            if api is not None:
                try:
                    api.disconnect()
                except Exception:
                    pass
            print(f"  attempt {attempt} not ready ({type(e).__name__}), retry in {delay}s")
            time.sleep(delay)
    raise SystemExit(f"ABORT: could not connect -> {type(last).__name__}: {last}")


def phase(label, client, created, is2):
    print(f"--- phase: {label}")
    pfx = f"{label}: "
    calls = []
    orig = client._call

    def spy(event, data=None):
        if event == PAGED:
            calls.append(data)
        return orig(event, data)

    client._call = spy
    res = None
    try:
        res = client.get_important_heartbeats()
    except Timeout as e:
        print(f"FAIL get_important_heartbeats raised Timeout -> {e}")
        check(pfx + "get_important_heartbeats did not raise", False, "Timeout")
    except Exception as e:
        print(f"FAIL get_important_heartbeats raised {type(e).__name__} -> {e}")
        check(pfx + "get_important_heartbeats did not raise", False, type(e).__name__)
    client._call = orig

    if res is not None:
        check(pfx + "result is a dict", isinstance(res, dict))
        if isinstance(res, dict):
            check(pfx + "both created ids are keys", all(m in res for m in created),
                  f"keys={sorted(res)}")
            for m in created:
                rows = res.get(m, [])
                check(pfx + f"monitor {m} has >= 1 row", len(rows) >= 1, f"{len(rows)} rows")
                if rows:
                    check(pfx + f"monitor {m} rows monitorID == {m}",
                          all(x.get("monitorID") == m for x in rows))
                    check(pfx + f"monitor {m} rows important is True",
                          all(x.get("important") is True for x in rows))
                    check(pfx + f"monitor {m} rows status is MonitorStatus",
                          all(isinstance(x.get("status"), MonitorStatus) for x in rows))
                    check(pfx + f"monitor {m} rows time is str",
                          all(isinstance(x.get("time"), str) for x in rows))
                    print(f"  row keys monitor {m}: {sorted(rows[0].keys())}")
                    print(f"  row status monitor {m}: {[str(x['status']) for x in rows]}")
                    if is2:
                        times = [x["time"] for x in rows]
                        check(pfx + f"monitor {m} newest first",
                              all(a >= b for a, b in zip(times, times[1:])))
                        check(pfx + f"monitor {m} at most 500 rows", len(rows) <= 500)

    n_monitors = len(client.get_monitors())
    print(f"  spy saw {len(calls)} {PAGED} calls: {calls}; monitors on server: {n_monitors}")
    if is2:
        if res is not None:
            check(pfx + "one paged call per monitor", len(calls) == n_monitors,
                  f"{len(calls)} vs {n_monitors}")
            check(pfx + "paged call data exactly (id, 0, 500)",
                  sorted(calls) == sorted((m, 0, 500) for m in created)
                  and all(isinstance(c, tuple) for c in calls), f"{calls}")
            if isinstance(res, dict):
                for m in created:
                    raw = orig(PAGED, (m, 0, 500))["data"]
                    proc = res.get(m, [])
                    raw_keys = sorted(raw[0].keys()) if raw else []
                    proc_keys = sorted(proc[0].keys()) if proc else []
                    check(pfx + f"monitor {m} raw vs processed length", len(raw) == len(proc),
                          f"{len(raw)} vs {len(proc)}")
                    check(pfx + f"monitor {m} raw vs processed key set", raw_keys == proc_keys,
                          f"{raw_keys} vs {proc_keys}")
        if label == "fresh-connection":
            try:
                print(f"  info ListCount({created[0]}) -> "
                      f"{orig('monitorImportantHeartbeatListCount', created[0])}")
            except Exception as e:
                print(f"  info ListCount failed {type(e).__name__}: {e}")
            try:
                print(f"  info raw paged for id 999999 -> {orig(PAGED, (999999, 0, 500))}")
            except Exception as e:
                print(f"  info raw paged for id 999999 raised {type(e).__name__}: {e}")
    else:
        check(pfx + "1.x spy saw zero paged calls", len(calls) == 0, f"{len(calls)}")
        if isinstance(res, dict):
            check(pfx + "1.x result non-empty for both ids",
                  all(len(res.get(m, [])) > 0 for m in created))


def main():
    global VERSION
    url = os.environ.get("UPTIME_KUMA_V1_URL")
    user = os.environ.get("UPTIME_KUMA_V1_USERNAME")
    pw = os.environ.get("UPTIME_KUMA_V1_PASSWORD")
    if not url or not user or not pw:
        print("ABORT: UPTIME_KUMA_V1_URL / _USERNAME / _PASSWORD must be set "
              "(run via scripts/run_disposable_kuma.ps1)")
        return 2

    print(f"Connecting to {url} ...")
    api = connect_with_retry(url)
    api2 = None
    created = []
    try:
        print(f"library version {uptime_kuma_api.__version__}")
        print(f"library file    {uptime_kuma_api.__file__}")
        if api.need_setup():
            api.setup(user, pw)
        api.login(user, pw)

        VERSION = str(api.info()["version"])
        print(f"server version  {VERSION} (api.version={api.version})")
        if not VERSION.startswith(ALLOWED):
            print(f"ABORT: server version {VERSION} not in {ALLOWED}; creating nothing")
            return 2
        is2 = VERSION.startswith("2.")

        r1 = api.add_monitor(type=MonitorType.PORT, name="live-check-63-tcp",
                             hostname="127.0.0.1", port=1, interval=20, maxretries=0)
        created.append(int(r1["monitorID"]))
        r2 = api.add_monitor(type=MonitorType.HTTP, name="live-check-63-http",
                             url="http://127.0.0.1:3001", interval=20)
        created.append(int(r2["monitorID"]))
        print(f"created monitors {created}")

        deadline = time.time() + 90
        ready = False
        while time.time() < deadline:
            try:
                if is2:
                    ready = all(
                        len(api._call(PAGED, (m, 0, 500))["data"]) >= 1 for m in created)
                else:
                    hb = api.get_heartbeats()
                    ready = all(len(hb.get(m, [])) >= 1 for m in created)
            except Exception as e:
                print(f"  poll error {type(e).__name__}: {e}")
            if ready:
                break
            time.sleep(3)
        print(f"first heartbeats present on both monitors: {ready}")
        check("both monitors produced a first heartbeat", ready)

        phase("same-session", api, created, is2)

        api2 = UptimeKumaApi(url)
        api2.login(user, pw)
        phase("fresh-connection", api2, created, is2)
    finally:
        for c in (api2,):
            if c is not None:
                try:
                    c.disconnect()
                except Exception:
                    pass
        for m in created:
            try:
                api.delete_monitor(m)
            except Exception as e:
                print(f"  cleanup of monitor {m} failed: {type(e).__name__}: {e}")
        try:
            api.disconnect()
        except Exception:
            pass

    ok = bool(results) and all(results)
    print(f"SUMMARY [{VERSION}] {sum(results)}/{len(results)} checks passed")
    print("PASS all checks" if ok else "FAIL one or more checks failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
