# Structure

## Layout

```
uptime_kuma_api/            # the package (import name)
  api.py                    # UptimeKumaApi — the large core; most logic lives here
  monitor_builder.py        # MonitorBuilder fluent builder
  notification_providers.py # provider option/metadata tables
  monitor_type.py, auth_method.py, ...  # enums
  __version__.py            # single source of the version number
  __init__.py               # public exports — anything here is public API
tests/                      # see taxonomy below
docs/                       # Sphinx: conf.py, api.rst (autodoc), index.rst, install.rst
scripts/                    # code-generation helpers (build_*.py); not shipped runtime
setup.py, CHANGELOG.md, README.md, .readthedocs.yaml
UPSTREAM_TRIAGE.md          # local-only working notes (gitignored)
.temp/                      # scratch files (gitignored) — see below
```

## Scratch files go in `.temp/`

Anything transient written into the working tree — a PR or issue body drafted for
`gh --body-file`, a probe script, captured command output — goes in `.temp/`,
which is gitignored as a whole. Create it if it isn't there.

Do not scatter temp files at the repo root. Nothing there is ignored by default,
so a file left behind shows up as untracked, `gh` warns about an uncommitted
change on every call, and it is one careless `git add` away from being committed.
`.temp/` makes a forgotten file harmless instead of a near miss, and it keeps the
draft openable in the editor for review, which a system temp directory would not.

Being gitignored does not make `.temp/` safe for secrets: treat it like
`tests/.backups/`, which is also ignored and still holds plaintext credentials.

## tests/ taxonomy — know which is which

The two categories are distinguished **mechanically**, not by a list anyone
maintains: a test needs a live server exactly when its class extends
`UptimeKumaTestCase`. `tests/conftest.py` marks those `integration` and
`pytest.ini` deselects that marker, so `pytest` is the unit suite and
`pytest -m integration` is the destructive one. Deliberately no filenames are
enumerated below — an earlier version of this section listed all nine unit files
and was one of seven copies that had to be edited in lockstep.

- **v2 unit tests** — every `tests/test_*.py` that does **not** extend
  `UptimeKumaTestCase`. No live server; they mock the version and transport.
  **This is the CI suite**, and what a bare `pytest` runs. Add regression tests
  here; no CI or docs change is needed when you do.
- **Inherited integration tests** — every `tests/test_*.py` that **does** extend
  `UptimeKumaTestCase` (`test_monitor.py`, `test_notification.py`,
  `test_status_page.py` and the rest). Require a live instance at
  `127.0.0.1:3001` and **wipe all its data** on setup. Never run in CI, and
  deselected from a plain `pytest`. Never point them at anything you care about.
- **Live scripts** (`live_test_backup.py`, `live_test_create.py`,
  `live_test_cleanup.py`, `live_test_ssl_verify.py`, `live_test_delete_id.py`):
  manual round-trip verification against a real 2.x instance, driven by
  `tests/.env`. Not tests, not run by CI. `live_test_ssl_verify.py` additionally
  needs an endpoint with a genuinely untrusted certificate, set via
  `UPTIME_KUMA_SELFSIGNED_URL`. `live_test_delete_id.py` creates and deletes one
  monitor, so point it at a disposable instance only.
- **Disposable-container live scripts** — the odd ones out. Each targets a
  **throwaway container at one specific server version**, not the real 2.x
  instance the scripts above use, and each reads its own `UPTIME_KUMA_V1_URL` /
  `UPTIME_KUMA_V1_USERNAME` / `UPTIME_KUMA_V1_PASSWORD` keys rather than the
  `tests/.env` keys, so none can accidentally hit the real target. Every one
  refuses to run if the URL is unset (no default) and aborts unless the server
  reports the version it is written for — a mistargeted URL fails closed rather
  than mutating something. They create monitors or status pages, so disposable
  instances only, and none belongs in the 2.x backup → create → cleanup cycle.
  Run them through `scripts/run_disposable_kuma.ps1`, which starts and destroys
  the container and keeps the Docker host out of its output.

  Named by the version each asserts rather than grouped as "v1", because that
  is what actually distinguishes them and the set is no longer one version line:

  - `live_test_conditions_v1.py`, `live_test_v2_only_fields_v1.py`,
    `live_test_status_page_v1.py` — assert `1.23`.
  - `live_test_status_page_analytics_v2_0.py` — asserts `2.0`. A 2.x container
    additionally needs `-DockerEnv UPTIME_KUMA_DB_TYPE=sqlite`, or it never
    mounts socket.io; see that runner's `.EXAMPLE`.

  The `UPTIME_KUMA_V1_*` key names are historical: they mean "the disposable
  container", whatever version it runs. The runner deliberately offers no way to
  change that prefix, so do not add one — the split from the `UPTIME_KUMA_*`
  keys is the safety property, not the `V1` in the name.

## Where changes usually go

- New monitor type / parameter → `_build_monitor_data` and validation in
  `api.py`, the enum in `monitor_type.py`, a matching `MonitorBuilder` setter,
  a v2 unit test, and `docs/api.rst` if a new public class.
- New notification provider → `notification_providers.py` tables + a v2 test.
- New public class/function → export in `__init__.py` **and** add to
  `docs/api.rst` (autodoc won't include it otherwise).

## Never commit

`tests/.env`, `tests/.backups/`, `tests/.live_test_ids.json`, `.temp/`, and
`UPSTREAM_TRIAGE.md` are gitignored and hold secrets or transient state.
