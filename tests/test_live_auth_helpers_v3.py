"""Offline unit tests for the helpers in tests/live_test_auth_v3.py.

No server, no UptimeKumaTestCase. The live script is loaded by path (its
filename is not a valid module name for pytest collection) and nothing in it
runs at import time. No real sleeping: pacing uses a fake clock.
"""
import ast
import importlib.util
import os

import pytest

from uptime_kuma_api import UptimeKumaException

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_test_auth_v3.py")

_spec = importlib.util.spec_from_file_location("live_test_auth_v3_under_test", SCRIPT)
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


# --- TOTP -------------------------------------------------------------------

@pytest.mark.parametrize("when, expected", [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
])
def test_totp_rfc6238_sha1_vectors(when, expected):
    assert live.totp_code(RFC_SECRET, when, digits=8) == expected


def test_totp_six_digit_is_truncation_of_eight_digit():
    assert live.totp_code(RFC_SECRET, 59) == "287082"
    assert live.totp_code(RFC_SECRET, 1111111109) == "081804"


def test_totp_accepts_lowercase_and_spaces():
    spaced = "gezd gnbv gy3t qojq gezd gnbv gy3t qojq"
    assert live.totp_code(spaced, 59, digits=8) == "94287082"


def test_parse_totp_uri_realistic():
    uri = ("otpauth://totp/Uptime%20Kuma:admin%40example.com"
           "?secret=" + RFC_SECRET + "&issuer=Uptime%20Kuma&algorithm=SHA1&digits=6&period=30")
    info = live.parse_totp_uri(uri)
    assert info["secret"] == RFC_SECRET
    assert info["digits"] == 6
    assert info["period"] == 30
    assert info["algorithm"] == "SHA1"
    assert "admin@example.com" in info["label"]
    assert info["issuer"] == "Uptime Kuma"


def test_parse_totp_uri_defaults_and_missing_secret():
    info = live.parse_totp_uri("otpauth://totp/x?secret=ABC")
    assert (info["digits"], info["period"], info["algorithm"]) == (6, 30, "SHA1")
    with pytest.raises(ValueError):
        live.parse_totp_uri("otpauth://totp/x?issuer=y")


def test_fresh_totp_waits_for_next_step_when_counter_used():
    fc = FakeClock(start=3000.0 + 5)  # period 30: counter 100, 25s left
    info = {"secret": RFC_SECRET, "digits": 6, "period": 30, "algorithm": "SHA1"}
    code, counter = live.fresh_totp(info, last_counter=100, clock=fc.clock, sleep=fc.sleep)
    assert counter == 101
    assert len(fc.sleeps) == 1
    assert code == live.totp_code(RFC_SECRET, fc.now)


def test_fresh_totp_no_wait_when_unused():
    fc = FakeClock(start=3000.0 + 5)
    info = {"secret": RFC_SECRET, "digits": 6, "period": 30, "algorithm": "SHA1"}
    _, counter = live.fresh_totp(info, last_counter=None, clock=fc.clock, sleep=fc.sleep)
    assert counter == 100
    assert fc.sleeps == []


# --- Config and guards ------------------------------------------------------

FULL_ENV = {
    live.ENV_URL: "http://127.0.0.1:3999/",
    live.ENV_USERNAME: "admin",
    live.ENV_PASSWORD: "secret-pw",
}


def test_read_config_ok_strips_trailing_slash():
    cfg = live.read_config(FULL_ENV)
    assert cfg == {"url": "http://127.0.0.1:3999", "username": "admin", "password": "secret-pw"}


@pytest.mark.parametrize("key", [live.ENV_URL, live.ENV_USERNAME, live.ENV_PASSWORD])
@pytest.mark.parametrize("blank", [False, True])
def test_read_config_refuses_each_unset_key(key, blank):
    env = dict(FULL_ENV)
    if blank:
        env[key] = "  "
    else:
        del env[key]
    with pytest.raises(live.ConfigError) as exc:
        live.read_config(env)
    assert key in str(exc.value)


def test_main_refuses_with_url_unset(monkeypatch):
    for k in (live.ENV_URL, live.ENV_USERNAME, live.ENV_PASSWORD):
        monkeypatch.delenv(k, raising=False)
    assert live.main() == 2


def test_main_refuses_when_only_url_unset(monkeypatch):
    monkeypatch.delenv(live.ENV_URL, raising=False)
    monkeypatch.setenv(live.ENV_USERNAME, "admin")
    monkeypatch.setenv(live.ENV_PASSWORD, "pw")
    assert live.main() == 2


@pytest.mark.parametrize("version", ["3.0.0-beta.2", "3.1.0", "3.0.0", "v3.2.1"])
def test_v3_versions_accepted(version):
    assert live.is_v3_version(version) is True
    live.require_v3(version)


@pytest.mark.parametrize("version", ["2.5.12", "1.23.2", "4.0.0", None, "", "garbage", 3, "3", "x3.0.0"])
def test_non_v3_versions_rejected(version):
    assert live.is_v3_version(version) is False
    with pytest.raises(live.AbortError):
        live.require_v3(version)


class _Resp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


def test_check_auth_ok():
    live.check_auth_ok("http://x", get=lambda url, timeout: _Resp(200, {"ok": True}))
    for resp in (_Resp(200, {"ok": False}), _Resp(404, {"ok": True}), _Resp(200, bad_json=True), _Resp(200, [])):
        with pytest.raises(live.AbortError):
            live.check_auth_ok("http://x", get=lambda url, timeout, r=resp: r)

    def boom(url, timeout):
        raise OSError("down")

    with pytest.raises(live.AbortError):
        live.check_auth_ok("http://x", get=boom)


# --- Pacing -----------------------------------------------------------------

def _max_in_window(stamps, window=60.0):
    worst = 0
    for i, t in enumerate(stamps):
        worst = max(worst, sum(1 for s in stamps[i:] if s < t + window))
    return worst


def test_pacer_never_exceeds_limit_in_any_60s_window():
    fc = FakeClock()
    pacer = live.RequestPacer(clock=fc.clock, sleep=fc.sleep)
    stamps = []
    for _ in range(50):
        pacer.acquire()
        stamps.append(fc.now)
    assert len(stamps) == 50
    assert _max_in_window(stamps) <= 9
    assert _max_in_window(stamps) < 10
    assert fc.sleeps  # it had to wait


def test_pacer_weighted_and_noted_requests_respect_limit():
    fc = FakeClock()
    pacer = live.RequestPacer(clock=fc.clock, sleep=fc.sleep)
    stamps = []
    pacer.note(1)
    stamps.append(fc.now)
    for i in range(30):
        n = 2 if i % 3 == 0 else 1
        pacer.acquire(n)
        stamps.extend([fc.now] * n)
    assert _max_in_window(stamps) <= 9


def test_pacer_does_not_sleep_under_limit_and_rejects_oversize():
    fc = FakeClock()
    pacer = live.RequestPacer(clock=fc.clock, sleep=fc.sleep)
    for _ in range(9):
        assert pacer.acquire() == 0.0
    assert fc.sleeps == []
    with pytest.raises(ValueError):
        pacer.acquire(10)


# --- paced_call -------------------------------------------------------------

def test_paced_call_retries_once_on_429_with_injected_sleep():
    fc = FakeClock()
    pacer = live.RequestPacer(clock=fc.clock, sleep=fc.sleep)
    calls = []

    def fn(x):
        calls.append(x)
        if len(calls) == 1:
            raise UptimeKumaException("Request failed: rate limited (429)")
        return {"ok": x}

    assert live.paced_call(pacer, 1, fn, 7) == {"ok": 7}
    assert calls == [7, 7]
    assert fc.sleeps == [live.RETRY_SLEEP]  # injected sleep, no real waiting


def test_paced_call_does_not_retry_other_errors():
    fc = FakeClock()
    pacer = live.RequestPacer(clock=fc.clock, sleep=fc.sleep)
    calls = []

    def fn():
        calls.append(1)
        raise UptimeKumaException("Invalid credentials")

    with pytest.raises(UptimeKumaException):
        live.paced_call(pacer, 1, fn)
    assert calls == [1]
    assert fc.sleeps == []


def test_paced_call_gives_up_after_max_retries():
    fc = FakeClock()
    pacer = live.RequestPacer(clock=fc.clock, sleep=fc.sleep)
    calls = []

    def fn():
        calls.append(1)
        raise UptimeKumaException("rate limited")

    with pytest.raises(UptimeKumaException):
        live.paced_call(pacer, 1, fn, retries=2)
    assert len(calls) == 3
    assert fc.sleeps == [live.RETRY_SLEEP] * 2


# --- compare_fields ---------------------------------------------------------

def test_compare_fields_match_absent_mismatch():
    sent = {"name": "a", "interval": 30, "type": live.MonitorType.PUSH}
    assert live.compare_fields(sent, {"name": "a", "interval": 30, "type": "push"}) == []
    problems = live.compare_fields(sent, {"name": "b"})
    kinds = {f: k for f, k, _ in problems}
    assert kinds == {"name": "MISMATCH", "interval": "ABSENT", "type": "ABSENT"}
    assert live.compare_fields(sent, {"name": "a", "interval": 31, "type": "push"}, ["interval"])[0][:2] == (
        "interval", "MISMATCH")
    assert live.compare_fields({"a": 1}, None)[0][:2] == ("a", "ABSENT")


# --- ASCII ------------------------------------------------------------------

def test_ascii_helpers():
    assert live.is_ascii("PASS ->") is True
    assert live.is_ascii("caf\u00e9") is False
    assert live.ascii_only("caf\u00e9 \u2713") == "caf? ?"


def test_script_has_no_non_ascii_bytes():
    with open(SCRIPT, "rb") as f:
        f.read().decode("ascii")


def test_every_string_literal_in_script_is_ascii():
    with open(SCRIPT, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    bad = [(n.lineno, n.value) for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str) and not n.value.isascii()]
    assert bad == []
