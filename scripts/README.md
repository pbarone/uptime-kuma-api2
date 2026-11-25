# Scripts

Development-time tools for discovering Uptime Kuma field changes.

## Setup

The scripts parse Uptime Kuma's Vue/JS source to detect new monitor fields,
notification providers, etc. You need a local checkout of the Uptime Kuma source:

```bash
# Clone a specific tag, for example 3.0.0-beta.2
git clone --depth 1 --branch 3.0.0-beta.2 https://github.com/louislam/uptime-kuma.git scripts/uptime-kuma

# For comparison against another release, clone it beside the first:
git clone --depth 1 --branch <tag> https://github.com/louislam/uptime-kuma.git scripts/uptime-kuma-old
```

These directories are gitignored — they won't be committed.

## Live verification against a throwaway server

`run_disposable_kuma.ps1` starts a disposable Uptime Kuma container on a remote
Docker host, runs one `tests/live_test_*.py` script against it, and destroys the
container again:

```powershell
pwsh -File scripts/run_disposable_kuma.ps1 -Script tests/live_test_<name>.py
```

Use it for the checks that can only be answered by a real server — whether a
version gate is correct, what the server does with a field the library sends,
how a payload round-trips. The default image is `louislam/uptime-kuma:3.0.0-beta.2`.
No live script ships on the `v3` branch yet; the 1.x and 2.x ones stay on `main`.

Two things it does that are easy to get wrong by hand:

- **The container is removed in a `finally` block**, including when the script
  raises or readiness times out. A forgotten container holding a bound port on a
  shared host is the failure this prevents.
- **The Docker host and SSH user are never printed.** They are read from the
  gitignored root `.env` (`DOCKER-HOST` / `DOCKER-USER`) and every line of output
  is rewritten to `<docker-host>` / `<user>`, so a transcript can be pasted into
  an issue or a spec without scrubbing.

It also refuses port 3001 outright — that is the default Uptime Kuma port and
where a real instance is expected to live. The admin password is generated per
run rather than defaulted to a literal, so no credential is committed.

Requires `DOCKER-HOST` and `DOCKER-USER` in the root `.env`, key-based SSH to
that host, and Docker on it. Run `Get-Help scripts/run_disposable_kuma.ps1
-Detailed` for the full parameter list.

## Scripts

- `run_disposable_kuma.ps1` — Runs a live_test script against a throwaway container (see above)
- `build_inputs.py` — Parses Vue source to discover monitor form fields
- `build_models.py` — Extracts model definitions
- `build_monitor_types.py` — Discovers monitor type identifiers
- `build_notifications.py` — Discovers notification provider fields
- `build_notification_docstring.py` — Generates docstring entries for notification params
