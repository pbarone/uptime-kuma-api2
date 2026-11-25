# AGENTS.md

Guidance for AI coding agents working in this repository. (Kiro users: the
authoritative, richer guidance is in `.kiro/steering/`; this file mirrors the
essentials for other tools.)

## What this is

`uptime-kuma-api2` — a Python wrapper for the Uptime Kuma Socket.IO API. A
maintained continuation of `lucasheld/uptime-kuma-api` under the original MIT
license. **Import package:** `uptime_kuma_api` (never rename it). **PyPI name:**
`uptime-kuma-api2`.

## Non-negotiables

- **This branch supports Uptime Kuma 3.x only.** This is the `v3` branch: the Uptime Kuma 3.x line during the Uptime Kuma 3.0 beta ([#88](https://github.com/pbarone/uptime-kuma-api2/issues/88)). Uptime Kuma 1.x and 2.x are supported by the 2.x line on `main` (`uptime-kuma-api2<3`), where backward compatibility with 1.x stays sacred. Here the support floor is Uptime Kuma 3.0: don't add code paths for 1.x or 2.x servers, and gate behavior that differs between 3.x releases behind `parse_version(self.version)`. Public method names, arguments and return shapes are still a contract for callers, so keep them where Uptime Kuma 3.x allows it.
- **`pytest -m integration` deletes all data on the instance it reaches.** The
  inherited integration tests wipe every monitor, notification, proxy, tag,
  status page, docker host, maintenance and API key during setup. `pytest.ini`
  deselects them by default, so a bare `pytest` is safe — but never opt in
  against an instance you care about. Use `./run_tests.sh`, which creates and
  destroys its own containers.
- **Don't add public API surface casually.** New parameters are additive and
  optional; new public classes must be exported in `__init__.py` and added to
  `docs/api.rst`.

## Working conventions

- Reproduce a bug in the code before fixing it; issue titles often misdescribe
  the real defect.
- Every bug fix gets a regression test proven to fail against the unfixed code.
- Conventional Commits for messages (`fix:`, `feat:`, `docs:`, ...); `!` for
  breaking changes.
- Branch → PR → CI → merge; never commit to `main` directly; never force-push.
  Create the branch before your first edit — protection is server-side, so a
  local commit on `main` succeeds and undoing it needs `reset --hard`.
  Commit at task/phase boundaries, not once at the end — squash is not used, so
  the per-commit bodies are the reasoning trail and are unrecoverable after the push.
- Update `CHANGELOG.md` for user-facing changes.

## Commands

Unit tests (what CI runs):

```
pytest -v
```

No file list: `tests/conftest.py` derives an `integration` marker from the
`UptimeKumaTestCase` base class and `pytest.ini` deselects it, so a bare `pytest`
is the unit suite. Adding a test file needs no CI or docs change.

```
python scripts/check_sdist.py    # sdist contents (also gated in publish.yml)
```

See `CONTRIBUTING.md` for full setup and `.kiro/steering/` for detailed
standards.
