---
inclusion: always
---

# Testing standards

Previously `inclusion: fileMatch` on `tests/**/*.py`, which meant the
ASCII-output rule below — the only place it is recorded outside a gitignored
file — did not load until a test file happened to be in context.

## Where tests go

New unit and regression tests go in the **unit suite** (the `*_v2.py` files,
`test_version_gates.py`, `test_status_page_incidents.py`, `test_monitor_builder.py`).
A test of version gating patches a synthetic floor (for example 3.1) onto the
registries in `api.py`, because no real gate is live on `v3` yet. These run
without a live server and are what CI executes. Do not add server-dependent
tests to the CI path.

## A regression test must be able to fail

When fixing a bug, prove the test actually catches it: revert the fix, watch the
new test go red, then restore the fix. A test that has only ever passed proves
nothing. (This caught real value on the `incidents` fix — the 10 tests were
confirmed to fail against the pre-fix code.)

## Mock the server for unit tests

Unit tests mock the version and transport (see `test_status_page_v2.py`,
`test_status_page_incidents.py`) — e.g. set `api.version` and patch
`requests.get` / the socket.io call. Where behavior is version-gated, test a `version` on each side of the gate (on this `v3` branch, two 3.x versions), so the contract for older 3.x servers is covered.

## Never wipe a real instance

Do NOT run `pytest tests/` (bare) or the inherited integration tests against any
instance you care about — their setup deletes all data. Name the v2 unit files
explicitly — the authoritative list is the `pytest` command in `CONTRIBUTING.md`
(mirrored in `.github/workflows/test.yml`, which is the one CI actually reads) —
or use a disposable Docker instance.

## Live verification (manual, real server)

For end-to-end checks against a real instance, use the scripted cycle and always
back up and dry-run first:

```
python tests/live_test_backup.py            # config snapshot first
python tests/live_test_create.py            # create + round-trip verify
python tests/live_test_cleanup.py --dry-run # ALWAYS dry-run before deleting
python tests/live_test_cleanup.py
```

The live create script's value is the **round-trip**: create a resource, read it
back, and compare sent vs returned fields (ABSENT vs MISMATCH). "The server
didn't reject it" is not verification.

## Live verification against a disposable container (separate, do not mix into the cycle above)

A live script that targets a throwaway container is a standalone one-off, **not**
a step in the cycle above. It reads its own `UPTIME_KUMA_V1_URL` /
`UPTIME_KUMA_V1_USERNAME` / `UPTIME_KUMA_V1_PASSWORD` keys, not the `tests/.env`
keys, so it must never be appended to the backup, create, cleanup sequence,
which would point it at the wrong server. The scripts that targeted 1.x and 2.x
containers were removed from `v3` and stay on `main`; none ships on `v3` yet.

A new one should delete what it creates in a `finally` block, refuse to run with
the URL unset, and abort unless the server reports the version it is written for,
so a mistargeted URL fails closed rather than mutating a real instance. Run it
through `scripts/run_disposable_kuma.ps1`, which starts the container
(`louislam/uptime-kuma:3.0.0-beta.2` by default) and destroys it afterwards.

## Script output must be ASCII

Test/utility scripts print to a Windows cp1252 console. Do not use non-ASCII
glyphs (check marks, box-drawing) in script output — they raise
`UnicodeEncodeError` and have crashed a script mid-run. Use `PASS`/`FAIL`/`->`.
