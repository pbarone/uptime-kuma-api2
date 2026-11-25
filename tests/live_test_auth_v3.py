"""
Live verification of the Uptime Kuma 3.x authentication and data-event paths
against a throwaway container (spec uptime-kuma-v3-support, Requirement 19).

Not a pytest test: the filename starts with ``live_test_``, which pytest's
default discovery (``test_*.py`` / ``*_test.py``) does not collect.

SAFETY -- READ FIRST:
    This script runs setup(), changes the admin password, turns 2FA on and off,
    and creates and deletes a monitor and an API key. Run it ONLY through the
    disposable-container runner, which starts a fresh Uptime Kuma 3.x container
    and destroys it afterwards. It is NOT part of the backup, create, cleanup
    cycle and must never be pointed at the instance in tests/.env.

    It reads its own keys, set by the runner for the child process only:
        UPTIME_KUMA_V1_URL, UPTIME_KUMA_V1_USERNAME, UPTIME_KUMA_V1_PASSWORD
    (the V1 in the names is historical: they mean "the disposable container").
    It refuses to run with any of them unset, probes GET /api/auth/ok before
    anything else, and aborts right after the first login unless the server
    reports a 3.x version.

Run (exact command; two -DockerEnv values need the -Command array form, because
under ``pwsh -File`` the second value binds positionally to -Name):

    pwsh -Command "& scripts/run_disposable_kuma.ps1 -Local -Script tests/live_test_auth_v3.py -DockerEnv @('UPTIME_KUMA_DB_TYPE=sqlite','UPTIME_KUMA_DISABLE_LEAK_PWD_CHECK=1')"

Steps, in order (each write is read back and compared, reporting ABSENT or
MISMATCH per field; "the server did not reject it" is not verification):
    need_setup, setup, login, version guard, prepare_2fa, TOTP code computed
    here from the totpURI secret (hmac, hashlib, struct, base64, time only),
    verify_token, save_2fa, logout, login without a code (must fail), login
    with a code, login_by_token with the stored token (prints a DECISION B
    line), disable_2fa, change_password, logout, login with the old password
    (must fail), login with the new one, push monitor create, push heartbeat,
    add_api_key / get_api_keys / get_api_key / delete_api_key, uptime,
    avg_ping, get_monitors, get_heartbeats, get_important_heartbeats,
    delete_monitor.

    get_heartbeats and get_important_heartbeats run before delete_monitor so
    they have a monitor with beats to read back.

Pacing: Better Auth allows 10 requests per 60 seconds. Every call that sends
Better Auth requests goes through ``RequestPacer`` (a sliding window that
sleeps before sending) and ``paced_call`` (sleeps and retries on a 429). A full
run therefore takes a few minutes.

Cleanup: the ``finally`` block deletes the monitor and the API key, and turns
2FA off, whatever happened above. The container is destroyed by the runner too.

Output is ASCII only (the Windows console is cp1252). PASS / FAIL / INFO / ->.
Exit code is 0 only if every check passed.
"""
import base64
import datetime
import hashlib
import hmac
import os
import re
import struct
import sys
import time
from collections import deque
from enum import Enum
from urllib import parse

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from uptime_kuma_api import (  # noqa: E402
    MonitorStatus,
    MonitorType,
    Timeout,
    UptimeKumaApi,
    UptimeKumaException,
)

ENV_URL = "UPTIME_KUMA_V1_URL"
ENV_USERNAME = "UPTIME_KUMA_V1_USERNAME"
ENV_PASSWORD = "UPTIME_KUMA_V1_PASSWORD"

# Better Auth: 10 requests per 60 seconds. Stay one under, since the window the
# server counts is not aligned with ours.
RATE_LIMIT = 10
RATE_WINDOW = 60.0
PACE_LIMIT = RATE_LIMIT - 1
PACE_SLACK = 1.0
RETRY_SLEEP = 61.0
MAX_429_RETRIES = 3

NAME_PREFIX = "[TEST] "


class ConfigError(Exception):
    """The environment does not allow a safe run."""


class AbortError(Exception):
    """The target is not what this script is written for."""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

results = []


def ascii_only(text) -> str:
    """Return ``text`` as a str with every non-ASCII character replaced by ``?``."""
    return str(text).encode("ascii", "replace").decode("ascii")


def is_ascii(text) -> bool:
    """True when ``text`` contains only ASCII characters."""
    try:
        str(text).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def say(text="") -> None:
    """Print one line, ASCII only."""
    print(ascii_only(text), flush=True)


def record(label: str, ok: bool, detail: str = "") -> bool:
    """Record one check and print it as it happens."""
    results.append((label, bool(ok), detail))
    say(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if detail:
        say(f"          {detail}")
    return bool(ok)


# ---------------------------------------------------------------------------
# Config and guards
# ---------------------------------------------------------------------------

def read_config(environ) -> dict:
    """Read the throwaway-container settings from ``environ``.

    :raises ConfigError: If the URL, username or password is unset or empty.
    """
    missing = [k for k in (ENV_URL, ENV_USERNAME, ENV_PASSWORD) if not (environ.get(k) or "").strip()]
    if missing:
        raise ConfigError(
            f"ABORT: {', '.join(missing)} not set. This script targets a throwaway "
            "container only; run it through scripts/run_disposable_kuma.ps1."
        )
    return {
        "url": environ[ENV_URL].strip().rstrip("/"),
        "username": environ[ENV_USERNAME],
        "password": environ[ENV_PASSWORD],
    }


def is_v3_version(version) -> bool:
    """True when ``version`` is a string whose major number is 3."""
    if not isinstance(version, str):
        return False
    m = re.match(r"\s*v?(\d+)\.", version)
    return bool(m) and int(m.group(1)) == 3


def require_v3(version) -> None:
    """:raises AbortError: Unless ``version`` is a 3.x version."""
    if not is_v3_version(version):
        raise AbortError(f"ABORT: server reports version {version!r}, not 3.x")


def check_auth_ok(url: str, timeout: float = 15, get=requests.get) -> None:
    """Pre-login guard: 3.x answers GET /api/auth/ok with JSON {"ok": true}.

    :raises AbortError: On anything else (1.x/2.x answer with the web UI's HTML).
    """
    try:
        r = get(f"{url}/api/auth/ok", timeout=timeout)
        body = r.json()
    except Exception as e:
        raise AbortError(f"ABORT: GET /api/auth/ok failed ({type(e).__name__})") from None
    if r.status_code != 200 or not isinstance(body, dict) or body.get("ok") is not True:
        raise AbortError("ABORT: GET /api/auth/ok did not answer {\"ok\": true}; not an Uptime Kuma 3.x server")


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------

class RequestPacer:
    """Sliding-window limiter: at most ``limit`` requests per ``window`` seconds.

    ``clock`` and ``sleep`` are injectable so the window logic can be tested
    without waiting.
    """

    def __init__(self, limit=PACE_LIMIT, window=RATE_WINDOW, slack=PACE_SLACK,
                 clock=time.monotonic, sleep=time.sleep):
        self.limit = limit
        self.window = window
        self.slack = slack
        self.clock = clock
        self.sleep = sleep
        self._stamps = deque()

    def _expire(self, now) -> None:
        while self._stamps and now - self._stamps[0] >= self.window + self.slack:
            self._stamps.popleft()

    def acquire(self, n: int = 1) -> float:
        """Block until ``n`` requests fit in the window, record them, return seconds slept."""
        if n < 1:
            return 0.0
        if n > self.limit:
            raise ValueError(f"cannot send {n} requests in one window of {self.limit}")
        slept = 0.0
        while True:
            now = self.clock()
            self._expire(now)
            if len(self._stamps) + n <= self.limit:
                for _ in range(n):
                    self._stamps.append(now)
                return slept
            wait = self._stamps[len(self._stamps) + n - self.limit - 1] + self.window + self.slack - now
            wait = max(wait, 0.01)
            self.sleep(wait)
            slept += wait

    def note(self, n: int = 1) -> None:
        """Record ``n`` requests sent outside ``acquire`` (for example by a constructor)."""
        now = self.clock()
        for _ in range(n):
            self._stamps.append(now)

    def reset(self) -> None:
        """Forget the window (after a 429 and a full-window sleep)."""
        self._stamps.clear()


def is_rate_limited(exc) -> bool:
    """True when ``exc`` is the library's 429 error."""
    return "rate limited" in str(exc).lower()


def paced_call(pacer, weight, fn, *args, retries=MAX_429_RETRIES, **kwargs):
    """Reserve ``weight`` requests in ``pacer``, call ``fn``, and on a 429 sleep a window and retry."""
    attempt = 0
    while True:
        pacer.acquire(weight)
        try:
            return fn(*args, **kwargs)
        except UptimeKumaException as e:
            if not is_rate_limited(e) or attempt >= retries:
                raise
            attempt += 1
            say(f"  INFO  429 from the server, sleeping {RETRY_SLEEP:.0f}s (retry {attempt}/{retries})")
            pacer.sleep(RETRY_SLEEP)
            pacer.reset()


# ---------------------------------------------------------------------------
# TOTP (RFC 6238), standard library only
# ---------------------------------------------------------------------------

def parse_totp_uri(uri: str) -> dict:
    """Parse an otpauth://totp URI into secret, digits, period, algorithm and label.

    :raises ValueError: If the URI has no secret.
    """
    parts = parse.urlsplit(uri)
    params = dict(parse.parse_qsl(parts.query))
    secret = params.get("secret")
    if not secret:
        raise ValueError("the totpURI has no secret")
    return {
        "secret": secret,
        "digits": int(params.get("digits", 6)),
        "period": int(params.get("period", 30)),
        "algorithm": params.get("algorithm", "SHA1").upper(),
        "label": parse.unquote(parts.path.lstrip("/")),
        "issuer": params.get("issuer"),
    }


def totp_code(secret: str, for_time: float, digits: int = 6, period: int = 30, algorithm: str = "SHA1") -> str:
    """Compute the RFC 6238 code for ``secret`` (base32) at ``for_time`` (epoch seconds)."""
    s = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(s + "=" * (-len(s) % 8))
    counter = int(for_time // period)
    digest = hmac.new(key, struct.pack(">Q", counter), getattr(hashlib, algorithm.lower())).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def fresh_totp(info: dict, last_counter=None, clock=time.time, sleep=time.sleep, margin: float = 2.0):
    """Return ``(code, counter)`` for a time step later than ``last_counter``.

    Waits for the next step when the current one was already used, or has less
    than ``margin`` seconds left (so the code does not expire in flight).
    """
    period = info["period"]
    now = clock()
    counter = int(now // period)
    left = (counter + 1) * period - now
    if (last_counter is not None and counter <= last_counter) or left < margin:
        sleep(left + 0.5)
        now = clock()
        counter = int(now // period)
    code = totp_code(info["secret"], now, info["digits"], period, info["algorithm"])
    return code, counter


# ---------------------------------------------------------------------------
# Round-trip compare
# ---------------------------------------------------------------------------

def _norm(value):
    return value.value if isinstance(value, Enum) else value


def compare_fields(sent: dict, got: dict, fields=None) -> list:
    """Compare what was sent with what was read back.

    :return: A list of ``(field, "ABSENT" | "MISMATCH", detail)``; empty when every field matches.
        ABSENT means the field is missing from ``got``; MISMATCH means it is present but different.
    """
    problems = []
    for field in (fields if fields is not None else list(sent)):
        if not isinstance(got, dict) or field not in got:
            problems.append((field, "ABSENT", f"sent {sent.get(field)!r}"))
        elif _norm(got[field]) != _norm(sent[field]):
            problems.append((field, "MISMATCH", f"sent {_norm(sent[field])!r}, got {_norm(got[field])!r}"))
    return problems


def check_round_trip(label: str, sent: dict, got: dict, fields=None) -> bool:
    """Record a PASS or FAIL for a compare_fields result."""
    problems = compare_fields(sent, got, fields)
    if problems:
        return record(label, False, "; ".join(f"{f} {kind} ({d})" for f, kind, d in problems))
    return record(label, True)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

class State:
    """What the run has created, so the finally block can undo it."""

    def __init__(self, cfg):
        self.url = cfg["url"]
        self.username = cfg["username"]
        self.password = cfg["password"]
        self.api = None
        self.pacer = RequestPacer()
        self.session = False      # the library holds a logged-in session
        self.twofa_info = None    # parsed totpURI once 2FA enrollment started
        self.twofa_on = False     # 2FA is (or may be) enabled on the account
        self.last_counter = None  # last TOTP step used
        self.monitor_id = None
        self.api_key_id = None


def connect(st: State, attempts: int = 12, delay: float = 5.0) -> None:
    """Create the API object, retrying the first connection while the container boots."""
    last = None
    for i in range(attempts):
        try:
            st.api = UptimeKumaApi(st.url, timeout=20)
            st.pacer.note(1)  # the constructor's /api/auth/ok probe
            return
        except UptimeKumaException as e:
            last = e
            say(f"  INFO  connect attempt {i + 1}/{attempts} failed: {e}")
            time.sleep(delay)
    raise AbortError(f"ABORT: could not connect to the server ({last})")


def login(st: State, with_code: bool = False) -> str:
    """Log in with the current credentials (2 requests when a code is needed, else 1)."""
    token = None
    if with_code:
        token, st.last_counter = fresh_totp(st.twofa_info, st.last_counter)
    result = paced_call(st.pacer, 2 if with_code else 1, st.api.login, st.username, st.password, token)
    st.session = True
    return result["token"]


def expect_failure(label: str, weight: int, st: State, fn, *args) -> bool:
    """Record PASS when ``fn`` raises UptimeKumaException, FAIL when it succeeds."""
    try:
        paced_call(st.pacer, weight, fn, *args)
    except UptimeKumaException as e:
        return record(label, True, f"rejected: {e}")
    return record(label, False, "the call succeeded but must be rejected")


def step(title: str) -> None:
    say()
    say(f"-> {title}")


def run_auth(st: State) -> None:
    api, pacer = st.api, st.pacer
    original_password = st.password

    step("need_setup / setup")
    if api.need_setup():
        r = paced_call(pacer, 1, api.setup, st.username, st.password)
        record("setup returns the success message", r == {"msg": "Added Successfully."}, f"got {r!r}")
        record("need_setup is False after setup", api.need_setup() is False)
    else:
        say("  INFO  server already set up; setup skipped")

    step("login and version guard")
    token = login(st)
    record("login returns a non-empty token", isinstance(token, str) and len(token) > 8)
    version = api.info().get("version")
    say(f"  INFO  server version {version}")
    require_v3(version)
    record("server reports a 3.x version", True, str(version))
    record("twofa_status is False before enrollment", paced_call(pacer, 1, api.twofa_status) == {"status": False})

    step("2FA enrollment")
    uri = paced_call(pacer, 1, api.prepare_2fa, st.password)["uri"]
    st.twofa_on = True
    st.twofa_info = parse_totp_uri(uri)
    info = st.twofa_info
    record("prepare_2fa uri has a secret", bool(info["secret"]))
    # 3.0.0-beta.2 labels the entry "Better Auth:<generated-id>@noreply.uptime-kuma.internal",
    # not the username, so only check that an account label is present.
    record("prepare_2fa uri carries an account label", ":" in info["label"] and "@" in info["label"],
           f"label {info['label']!r}")
    code, st.last_counter = fresh_totp(info, st.last_counter)
    record("verify_token accepts the computed TOTP code",
           paced_call(pacer, 1, api.verify_token, code, st.password) == {"valid": True})
    record("twofa_status is True after verify_token", paced_call(pacer, 1, api.twofa_status) == {"status": True})
    record("save_2fa confirms", paced_call(pacer, 1, api.save_2fa, st.password) == {"msg": "2FA Enabled."})

    step("two-factor login")
    paced_call(pacer, 1, api.logout)
    st.session = False
    expect_failure("login without a code is rejected", 1, st, api.login, st.username, st.password)
    token = login(st, with_code=True)
    record("login with a TOTP code returns a token", isinstance(token, str) and len(token) > 8)

    step("login_by_token")
    authenticated, why = False, ""
    try:
        api.login_by_token(token)
        monitors = api.get_monitors()
        authenticated = isinstance(monitors, list) and bool(api.info().get("version"))
        why = "socket authenticated and get_monitors answered"
    except (UptimeKumaException, Timeout) as e:
        why = f"{type(e).__name__}: {e}"
    say(f"  DECISION B: login_by_token authenticates the socket: {'YES' if authenticated else 'NO'} ({why})")
    if not authenticated:
        st.session = False
        token = login(st, with_code=True)

    step("disable 2FA")
    record("disable_2fa confirms", paced_call(pacer, 1, api.disable_2fa, st.password) == {"msg": "2FA Disabled."})
    st.twofa_on = False
    record("twofa_status is False after disable_2fa", paced_call(pacer, 1, api.twofa_status) == {"status": False})

    step("change_password / logout / relogin")
    new_password = original_password + "-2"
    r = paced_call(pacer, 1, api.change_password, st.password, new_password)
    st.password = new_password
    record("change_password confirms", r == {"msg": "Password has been updated successfully."}, f"got {r!r}")
    paced_call(pacer, 1, api.logout)
    st.session = False
    expect_failure("login with the old password is rejected", 1, st, api.login, st.username, original_password)
    token = login(st)
    record("login with the new password returns a token", isinstance(token, str) and len(token) > 8)


def wait_for_stats(api, monitor_id, push, seconds: float = 45.0, clock=time.monotonic, sleep=time.sleep):
    """Poll uptime() until the stats event carries ``monitor_id``; push again between polls."""
    end = clock() + seconds
    while True:
        try:
            stats = api.uptime()
        except Timeout:
            stats = {}
        for key in (monitor_id, str(monitor_id)):
            if key in stats:
                return key
        if clock() > end:
            return None
        push()
        sleep(2)


def run_data(st: State) -> None:
    api, pacer = st.api, st.pacer

    step("push monitor create and round-trip")
    # add_monitor does not accept pushToken; the library generates one, so read it back.
    sent = {"type": MonitorType.PUSH, "name": NAME_PREFIX + "auth v3 push", "interval": 30}
    r = api.add_monitor(**sent)
    st.monitor_id = r["monitorID"]
    got = api.get_monitor(st.monitor_id)
    check_round_trip("get_monitor returns what add_monitor sent", sent, got)
    push_token = got.get("pushToken")
    record("get_monitor returns a generated pushToken", bool(push_token))

    def push():
        try:
            resp = requests.get(f"{st.url}/api/push/{push_token}",
                                params={"status": "up", "msg": "OK", "ping": "5"}, timeout=15)
            return resp.status_code == 200 and resp.json().get("ok") is True
        except Exception:
            return False

    record("push endpoint accepts status=up", push())

    step("API key round-trip")
    # the server caps the lifetime (a 2099 date is rejected: "expiresIn is larger than the predefined maximum")
    expires_dt = datetime.datetime.now() + datetime.timedelta(days=30)
    expires = expires_dt.strftime("%Y-%m-%d %H:%M:%S")
    r = api.add_api_key(NAME_PREFIX + "auth v3 key", expires)
    st.api_key_id = r.get("keyID")
    record("add_api_key returns keyID and key", bool(st.api_key_id) and bool(r.get("key")))
    listed = [k for k in api.get_api_keys() if str(k["id"]) == str(st.api_key_id)]
    record("get_api_keys lists the new key", len(listed) == 1)
    if listed:
        check_round_trip("get_api_keys entry matches what was sent",
                         {"name": NAME_PREFIX + "auth v3 key", "active": True}, listed[0])
        exp = listed[0].get("expires")
        record("get_api_keys entry carries the expiry date",
               isinstance(exp, str) and exp.startswith(expires[:10]), f"expires {exp!r}, sent {expires!r}")
        check_round_trip("get_api_key equals the get_api_keys entry", listed[0], api.get_api_key(st.api_key_id))
    api.delete_api_key(st.api_key_id)
    gone = all(str(k["id"]) != str(st.api_key_id) for k in api.get_api_keys())
    record("delete_api_key removes the key", gone)
    if gone:
        st.api_key_id = None

    step("uptime / avg_ping")
    # 3.0.0-beta.2 sends stats at login for the monitor ids that existed at login
    # (server.js sendDeferredLists), and otherwise only on a 15-minute refresh. A
    # monitor created after login is therefore absent until the next login, so log
    # in again (one paced request) when the first wait does not find it.
    key = wait_for_stats(api, st.monitor_id, push, seconds=10)
    if key is None:
        say("  INFO  monitor not in stats yet; logging in again to get a fresh stats event")
        login(st)
        key = wait_for_stats(api, st.monitor_id, push)
    record("stats event carries the monitor", key is not None)
    if key is not None:
        up = api.uptime()[key]
        record("uptime has keys 24, 720, 1y", all(k in up for k in (24, 720, "1y")), f"got {up!r}")
        record("uptime 24h is a number between 0 and 1",
               isinstance(up.get(24), (int, float)) and 0 <= up[24] <= 1, f"got {up.get(24)!r}")
        ap = api.avg_ping()[key]
        record("avg_ping has keys 24, 720, 1y", all(k in ap for k in (24, 720, "1y")), f"got {ap!r}")
        record("avg_ping 24h is not None", ap.get(24) is not None, f"got {ap.get(24)!r}")

    step("get_monitors round-trip")
    compact = [m for m in api.get_monitors() if m["id"] == st.monitor_id]
    record("get_monitors lists the monitor", len(compact) == 1)
    if compact:
        check_round_trip("get_monitors entry matches what was sent", sent, compact[0], ["type", "name", "interval"])
        record("get_monitors entry is active", compact[0].get("active") is True)

    step("heartbeats")
    beats = api.get_heartbeats().get(st.monitor_id) or []
    record("get_heartbeats has beats for the monitor", len(beats) > 0, f"{len(beats)} beats")
    if beats:
        record("latest heartbeat status is UP", beats[-1].get("status") == MonitorStatus.UP,
               f"status {beats[-1].get('status')!r}")
    important = api.get_important_heartbeats().get(st.monitor_id) or []
    record("get_important_heartbeats has rows for the monitor", len(important) > 0, f"{len(important)} rows")
    if important:
        record("an important heartbeat is UP", any(h.get("status") == MonitorStatus.UP for h in important))

    step("delete_monitor round-trip")
    api.delete_monitor(st.monitor_id)
    gone = all(m["id"] != st.monitor_id for m in api.get_monitors())
    record("delete_monitor removes the monitor", gone)
    if gone:
        st.monitor_id = None


def cleanup(st: State) -> None:
    """Best effort: delete the monitor and key, turn 2FA off. Each step is independent."""
    api = st.api
    if api is None:
        return
    say()
    step("cleanup")
    needs = st.monitor_id is not None or st.api_key_id is not None or st.twofa_on
    if needs and not st.session:
        try:
            login(st, with_code=st.twofa_on and st.twofa_info is not None)
        except Exception as e:
            say(f"  INFO  cleanup login failed: {type(e).__name__}: {e}")
    if st.monitor_id is not None:
        try:
            api.delete_monitor(st.monitor_id)
            st.monitor_id = None
            say("  INFO  monitor deleted")
        except Exception as e:
            say(f"  INFO  monitor delete failed: {type(e).__name__}: {e}")
    if st.api_key_id is not None:
        try:
            api.delete_api_key(st.api_key_id)
            st.api_key_id = None
            say("  INFO  api key deleted")
        except Exception as e:
            say(f"  INFO  api key delete failed: {type(e).__name__}: {e}")
    if st.twofa_on:
        try:
            paced_call(st.pacer, 1, api.disable_2fa, st.password)
            st.twofa_on = False
            say("  INFO  2FA disabled")
        except Exception as e:
            say(f"  INFO  disable_2fa failed: {type(e).__name__}: {e}")
    try:
        api.disconnect()
    except Exception:
        pass


def main(environ=None) -> int:
    try:
        cfg = read_config(os.environ if environ is None else environ)
    except ConfigError as e:
        say(str(e))
        return 2

    say(f"Target: {cfg['url']} (throwaway container only)")
    st = State(cfg)
    try:
        check_auth_ok(cfg["url"])
        st.pacer.note(1)
        connect(st)
        run_auth(st)
        run_data(st)
    except AbortError as e:
        say(str(e))
        return 2
    except (UptimeKumaException, Timeout) as e:
        record("run completed without an unexpected error", False, f"{type(e).__name__}: {e}")
    finally:
        cleanup(st)

    failed = [r for r in results if not r[1]]
    say()
    say(f"{len(results) - len(failed)} passed, {len(failed)} failed")
    for label, _, detail in failed:
        say(f"  FAIL  {label}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
