# Implementation Plan: Uptime Kuma 3.x authentication, API keys and data events

## Overview

This plan implements requirements.md and design.md for the `v3` branch of uptime-kuma-api2. The implementation language is Python 3.10+ (the design uses Python). Almost all runtime changes are in `uptime_kuma_api/api.py` and `uptime_kuma_api/event.py`. The plan is ordered so that every top-level task leaves the bare `pytest` unit suite green, which makes each top-level task one coherent commit (the commit seams required by `.kiro/steering/git-and-releasing.md`).

Working rules for every task:

- Create a `<type>/<desc>` branch from `v3` before the first edit (for example `feat/v3-auth-and-data-events`). Never commit to `v3` or `main` directly. Each top-level task is one commit, or one PR if the work is split, with a Conventional Commit message whose body says why the step was taken (suggested type is given per task). Do not commit from inside a sub-task unless the user asks.
- Unit tests mock `requests` and the socket and must not extend `UptimeKumaTestCase`. For each regression test, prove it can fail: run it against the code before the implementation sub-task (or revert the change), see it go red, then restore. Existing unit tests that assert removed 2.x behavior (for example socket-event login, `enable_api_key`, the `avgPing` and `uptime` events) are updated in the same task that changes the behavior, so the suite stays green.
- Run `pytest` bare, or name unit test files explicitly. Never run `pytest -m integration`, `pytest tests/`, or the inherited `UptimeKumaTestCase` tests against any instance you care about.
- Script and test output must be ASCII only (`PASS`, `FAIL`, `->`).
- Event members are removed in the task that replaces their last consumer (AUTO_LOGIN in task 3, API_KEY_LIST in task 5, UPTIME and AVG_PING in task 6) so the suite stays green. The new members (SESSION, STATS, HEARTBEAT_LISTS) are added first, in task 2. Task 13.3 verifies the final enum against Requirement 21.
- Keep `parse_version`, the per-field floor tables and `UnsupportedFieldWarning` untouched (Requirement 17.5). No 3.x release gate is added by this spec because every behavior here starts at 3.0 (design, Version gating).
- No new runtime dependency. `hypothesis` is dev-only.

## Tasks

- [ ] 1. Add the hypothesis dev dependency (commit type: `ci`)
  - [ ] 1.1 Wire hypothesis into dev requirements and both CI workflows
    - Add `hypothesis` to `dev-requirements.txt`
    - Add it to the `pip install` step of both the `quick` job and the `full` job in `.github/workflows/test.yml`, and to the same step in `.github/workflows/publish.yml`
    - Leave `install_requires` in `setup.py` unchanged
    - Do not change the Python matrix, so the five required check names stay in sync with `protect-main`
    - _Requirements: 24.1, 24.2, 24.3_

  - [ ] 1.2 Write a unit test for the dependency wiring
    - New file `tests/test_dev_dependency_v3.py` that reads `dev-requirements.txt`, the two workflow files and `setup.py` as text and asserts hypothesis is listed in the first three and absent from `install_requires`
    - Confirm it fails before 1.1 is applied
    - _Requirements: 24.1, 24.2, 24.3_

- [ ] 2. Add the new Event members, handlers and cache reset (commit type: `refactor`)
  - [ ] 2.1 Add SESSION, STATS and HEARTBEAT_LISTS and the connection-state plumbing
    - In `uptime_kuma_api/event.py` add `SESSION = "session"`, `STATS = "stats"` and `HEARTBEAT_LISTS = "heartbeatLists"`. Do not remove any existing member yet
    - In `api.py` add `_event_session`, `_event_stats` (per-monitor-id merge keyed by int id) and `_event_heartbeat_lists` (fills only monitor ids absent from the `Event.HEARTBEAT_LIST` cache), register all three with `self.sio.on`, and add a `STATS` slot to `_event_data`
    - Factor the `_event_data` initialization out of `__init__` into `_reset_event_cache()`, which also resets `self._dashboard_rendered_sent` to False, and initialize `_dashboard_rendered_sent` in the constructor
    - Add the private `_ensure_heartbeat_history()` helper described in the design (emit `dashboardRendered` once per connection, skip when the monitor list is empty) but do not call it yet
    - _Requirements: 9.6, 14.1, 14.4, 16.2, 16.6, 21.2, 21.3_

  - [ ] 2.2 Write unit tests for the new events and cache reset
    - New file `tests/test_event_state_v3.py` (socket mocked): the three members exist with the right wire names, the handlers are registered, `_event_stats` replaces only the ids it carries, `_event_heartbeat_lists` never overwrites an existing id, and `_reset_event_cache()` clears every slot and the dashboard-rendered flag
    - _Requirements: 9.6, 14.4, 16.2, 16.6, 21.2, 21.3_

- [ ] 3. Authenticate over the Better Auth HTTP flow (commit type: `feat!`)
  - [ ] 3.1 Add the auth session, request helper and socket reconnect
    - In `UptimeKumaApi.__init__` create `self._auth_session` (a separate `requests.Session`, `verify` taken from the normalized `ssl_verify`, constructor headers applied) and `self._session_cookie = None`
    - Add `_auth_request(method, path, json_body=None, *, use_session_cookie=False)` as designed: always sends `Origin`, uses the constructor timeout, maps `requests` timeouts to `Timeout` and other `RequestException` to `UptimeKumaException`, maps HTTP 429 to an exception that names the rate limit, maps invalid JSON and non-2xx responses to `UptimeKumaException` with the server `message` or `msg`, and never puts a cookie, password, code or key in a message
    - Add `_reconnect_with_cookie(cookie_value)`: `sio.disconnect()`, merge a `Cookie: better-auth.session_token=<value>` header into a fresh copy of `self.headers`, `_reset_event_cache()`, `sio.connect(...)`, then wait for `Event.INFO`. Do not mutate `self.headers` or any public attribute
    - _Requirements: 1.2, 1.3, 1.5, 1.6, 1.7, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

  - [ ] 3.2 Write unit tests for the auth helpers
    - New file `tests/test_auth_http_v3.py` with `requests` and the socket mocked: Origin always sent, timeout and verify forwarded, custom headers forwarded, 429 message, non-JSON body, non-2xx with and without `message`, `requests` timeout becomes `Timeout`, connection error becomes `UptimeKumaException`, no secret appears in any exception message, the reconnect sends the expected Cookie header, resets the cache, leaves `self.headers` and the public attributes unchanged, and a second reconnect does not accumulate cookies
    - _Requirements: 1.2, 1.3, 1.5, 1.6, 1.7, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 18.3_

  - [ ] 3.3 Rewrite login for password, two-factor and no-auth sessions
    - `login(username, password, token=None)` posts `/api/auth/sign-in/username` with `username`, `password` and `rememberMe`, follows `twoFactorRedirect` with `POST /api/auth/two-factor/verify-totp` `{"code": token}`, raises when a code is needed and none was given, stores `_session_cookie`, reconnects with the cookie and returns `{"token": <cookie value>}`
    - With neither username nor password, wait for `Event.SESSION` and raise `Timeout` if it does not arrive
    - Remove `Event.AUTO_LOGIN`, `_event_auto_login` and its registration. Update any existing unit test that relied on them
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.8, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 21.1, 21.3_

  - [ ] 3.4 Write unit tests for login
    - New file `tests/test_login_v3.py`: success path (URL, JSON body, Origin, cookie sent on reconnect, `{"token": ...}` return), failure path with the server message, 429 path, network error, 2FA success, 2FA with no code, 2FA with a rejected code, no-credential login success and `Timeout`, and that `Event.AUTO_LOGIN` no longer exists. Confirm the new tests fail against the previous `login`
    - _Requirements: 1.1, 1.4, 1.5, 1.6, 1.8, 2.1, 2.3, 2.4, 3.1, 3.2, 3.3, 18.3, 18.4_

  - [ ] 3.5 Rewrite login_by_token and logout
    - `login_by_token(token)` reconnects with the cookie via `_reconnect_with_cookie`, stores `_session_cookie`, and raises `UptimeKumaException` if the server does not authenticate the socket. This is Decision B's primary branch; task 12.5 applies the fallback only if the live check fails
    - `logout()` posts `/api/auth/sign-out` with the stored cookie, clears `_session_cookie` on success, leaves the socket connected, and raises "the session is not logged in" before any request when no cookie is stored
    - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ] 3.6 Write unit tests for login_by_token and logout
    - New file `tests/test_session_v3.py`: reconnect header, rejected cookie raises, sign-out URL, cookie and Origin, cookie forgotten on success, kept on failure, socket not disconnected, not-logged-in error raised without any HTTP call
    - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 5.4, 5.5, 18.3_

  - [ ] 3.7 Rewrite setup, need_setup and change_password
    - `need_setup()` keeps emitting `needSetup`. `setup(username, password)` posts `/api/setup` with Origin and returns a dict with the same top-level shape as before. `change_password(old_password, new_password)` posts `/api/auth/change-password` with `currentPassword`, `newPassword` and `revokeOtherSessions` set to false, the stored cookie and Origin, and raises when no session cookie is stored
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 7.4_

  - [ ] 3.8 Write unit tests for setup, need_setup and change_password
    - New file `tests/test_account_v3.py`: URLs, JSON bodies, `revokeOtherSessions` is false, Origin and cookie present where required, non-success statuses raise with the server message, not-logged-in error, return shapes
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 7.4, 18.3_

  - [ ] 3.9 Rewrite the two-factor methods
    - Add `_twofa_request(path, json_body)` wrapping `_auth_request(..., use_session_cookie=True)`
    - `twofa_status()` reads `user.twoFactorEnabled` from `GET /api/auth/get-session`. `prepare_2fa(password)` posts `two-factor/enable` and returns `{"uri": totpURI}` as designed. `verify_token(token, password)` posts `two-factor/verify-totp` with `code` only and returns `{"valid": True}`. `save_2fa(password)` re-reads the session and returns `{"msg": "2FA Enabled."}` or raises when 2FA is not verified. `disable_2fa(password)` posts `two-factor/disable` and returns `{"msg": "2FA Disabled."}`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ] 3.10 Write unit tests for the two-factor methods
    - New file `tests/test_twofa_v3.py`: URL, JSON body, Origin and cookie per method, return shapes, non-success statuses raise with the server message, not-logged-in error for each method, and the prepare, verify, save sequence
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 18.3_

- [ ] 4. Checkpoint - authentication phase is green
  - Run bare `pytest` and confirm the suite passes, then run `python scripts/check_sdist.py`. Ask the user if questions arise.

- [ ] 5. Adapt the API-key methods (commit type: `feat!`)
  - [ ] 5.1 Rewrite the API-key methods and remove enable and disable
    - `get_api_keys()` emits `getAPIKeyList` and returns the callback list without waiting for an event, mapping `enabled` to `active`, `createdAt` and `expiresAt` to the existing key names, recomputing `status`, and keeping string ids (`legacy-<n>` included) unchanged
    - `get_api_key(id_)` and `delete_api_key(id_)` accept int or str and compare as strings. `delete_api_key` raises "api key does not exist" before emitting `deleteAPIKey`. `add_api_key(name, expires)` drops `active` and returns the response with `key` and the string `keyID`
    - Delete `enable_api_key`, `disable_api_key` and the code that emits `enableAPIKey` and `disableAPIKey`, with no stub. Remove `Event.API_KEY_LIST`, `_event_api_key_list` and its registration. Update existing unit tests that referenced any of these
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.3, 11.4, 11.5, 12.1, 12.2, 21.1, 21.3_

  - [ ] 5.2 Write unit tests for the API-key methods
    - New file `tests/test_api_key_v3.py` with the socket mocked and callback bodies containing both Native_Key and Legacy_Key entries: list shape, no event wait, get by int and str id, missing id raises, add without `active`, delete of a legacy and a native key emits a string id, delete of an unknown id raises before emitting, and `enable_api_key` and `disable_api_key` are absent (`AttributeError`)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.3, 11.4, 11.5, 12.1, 12.2_

  - [ ] 5.3 Write property test for API-key list normalization
    - **Property 4: API-key list normalization preserves id and derives active from enabled**
    - New file `tests/test_api_key_properties_v3.py` using hypothesis, at least 100 examples, tagged with a comment naming the property
    - **Validates: Requirements 10.1, 10.2, 10.3**

  - [ ] 5.4 Write property test for string-id lookup
    - **Property 5: get_api_key finds the key matching as a string regardless of the id argument's type**
    - Add to `tests/test_api_key_properties_v3.py`'s sibling file `tests/test_api_key_lookup_properties_v3.py`
    - **Validates: Requirements 10.4, 11.5**

  - [ ] 5.5 Write property test for lookup of an absent id
    - **Property 6: get_api_key raises for an id no key has**
    - Add to `tests/test_api_key_lookup_properties_v3.py`
    - **Validates: Requirements 10.5**

  - [ ] 5.6 Remove enable_api_key and disable_api_key from docs/api.rst
    - Remove both entries from `docs/api.rst`. The remaining API-key documentation is handled in task 13
    - _Requirements: 12.3_

- [ ] 6. Serve uptime() and avg_ping() from the stats event (commit type: `feat!`)
  - [ ] 6.1 Rewrite uptime and avg_ping around Event.STATS
    - Both methods read `Event.STATS` through `_get_event_data` and add `Event.STATS` to the monitor-events list that short-circuits for a server with no monitors. `uptime()` returns `{id: {24, 720, "1y"}}` from `uptime24`, `uptime720`, `uptime1y`. `avg_ping()` returns `{id: {24, 720, "1y"}}` from `avgPing`, `avgPing720`, `avgPing1y`
    - Remove `Event.UPTIME`, `Event.AVG_PING`, `_event_uptime`, `_event_avg_ping` and their registrations. Update the existing `test_avg_ping.py` and `test_uptime.py` style tests that assumed the old events
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 21.1, 21.3_

  - [ ] 6.2 Write unit tests for uptime and avg_ping
    - New file `tests/test_stats_v3.py`: concrete payload mapping, None averages, a later event replaces only its own ids, empty server returns immediately without waiting, `Timeout` when monitors exist and no `stats` arrives, and the old events no longer exist
    - _Requirements: 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_

  - [ ] 6.3 Write property test for the uptime mapping
    - **Property 1: Stats-to-uptime mapping preserves every monitor id and maps fields correctly**
    - New file `tests/test_stats_uptime_properties_v3.py` (hypothesis, at least 100 examples)
    - **Validates: Requirements 14.1, 14.2, 14.4**

  - [ ] 6.4 Write property test for the avg_ping mapping
    - **Property 2: Stats-to-avg_ping mapping preserves every monitor id and carries all three windows**
    - New file `tests/test_stats_avg_ping_properties_v3.py`
    - **Validates: Requirements 14.1, 14.3**

  - [ ] 6.5 Write property test for stats replacement
    - **Property 3: A later stats event for a monitor id replaces that id's values and leaves other ids untouched**
    - New file `tests/test_stats_merge_properties_v3.py`
    - **Validates: Requirements 14.4**

- [ ] 7. Return compact records from get_monitors (commit type: `feat!`)
  - [ ] 7.1 Make get_monitors compact and verify the cache readers
    - Add `_COMPACT_MONITOR_FIELDS` and `_to_compact_monitor`. `get_monitors()` reduces every cache entry (compact or full) to the eleven compact fields, tolerates a missing field, makes no `getMonitor` call, applies `int_to_bool` to `active` and `parse_monitor_type`, and drops `parse_auth_method`
    - Confirm `delete_monitor` reads only compact fields, still raises for an id not in the cache, and that `add_monitor_tag`, `delete_monitor_tag` and `get_important_heartbeats` read only compact fields from `get_monitors()` (change them only if one reads a non-compact field). Leave `get_monitor`, `_event_update_monitor_into_list` and `_event_delete_monitor_from_list` unchanged
    - Write the docstring with the migration `[api.get_monitor(m["id"]) for m in api.get_monitors()]` and add no `full` parameter
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9_

  - [ ] 7.2 Write unit tests for the compact monitor list
    - Extend or add `tests/test_monitor_cache_v3.py`: a compact `monitorList` without `notificationIDList` works for `get_monitors` and `delete_monitor`, a full entry from `updateMonitorIntoList` is reduced, no `getMonitor` call is emitted, an unknown id raises, `get_monitor` still returns the full record, and add, edit and delete events keep the cache consistent. Confirm the tests fail against the previous code with `KeyError: 'notificationIDList'`
    - _Requirements: 15.1, 15.2, 15.5, 15.6, 15.7, 15.9, 18.6_

  - [ ] 7.3 Write property test for the compact key set
    - **Property 7: get_monitors always returns exactly the compact key set, regardless of which cache entries were full**
    - New file `tests/test_monitor_cache_properties_v3.py`
    - **Validates: Requirements 15.1, 15.2**

- [ ] 8. Request heartbeat history with dashboardRendered (commit type: `fix`)
  - [ ] 8.1 Call the dashboardRendered helper from get_heartbeats
    - `get_heartbeats()` calls `_ensure_heartbeat_history()` before reading `Event.HEARTBEAT_LIST`. It emits `dashboardRendered` once per connection, never for a server with no monitors, and the existing `_get_event_data` wait raises `Timeout` when nothing arrives. `get_important_heartbeats()` keeps its own per-monitor paged fetch
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6_

  - [ ] 8.2 Write unit tests for heartbeat history
    - New file `tests/test_heartbeat_lists_v3.py`: one emit across two calls, no emit and no wait for an empty server, `Timeout` when no `heartbeatLists` arrives, the flag resets after a reconnect, and `get_important_heartbeats` works with compact monitors
    - _Requirements: 16.1, 16.3, 16.4, 16.5, 16.6_

  - [ ] 8.3 Write property test for the heartbeat merge
    - **Property 8: heartbeatLists never overwrites a monitor id heartbeat() already populated**
    - New file `tests/test_heartbeat_properties_v3.py`
    - **Validates: Requirements 16.2**

- [ ] 9. Checkpoint - data-event phases are green
  - Run bare `pytest` and confirm the suite passes, then run `python scripts/check_sdist.py`. Ask the user if questions arise.

- [ ] 10. Remove dead save_status_page parameters (commit type: `feat!`)
  - [ ] 10.1 Remove password and googleAnalyticsId from save_status_page
    - Remove both parameters and any code or docstring text that mentions them. Update existing callers in unit tests
    - _Requirements: 22.1_

  - [ ] 10.2 Write unit tests for the save_status_page signature
    - Extend `tests/test_status_page_v2.py` (or add `tests/test_status_page_v3.py`): passing either removed keyword raises `TypeError`, and the remaining parameters still reach the request payload
    - _Requirements: 22.1_

- [ ] 11. Reword the v3 API-contract guidance (commit type: `docs`)
  - [ ] 11.1 Update steering and AGENTS.md wording
    - Reword the statements that call the public API a contract on `v3` in `.kiro/steering/product.md`, `.kiro/steering/coding-standards.md`, any other `v3` steering file that repeats it (search `.kiro/steering/` for "contract"), and `AGENTS.md`, so they state Decision E: a public name or return shape is kept only when it maps naturally onto Uptime Kuma 3.x and each break is listed under BREAKING CHANGES, and the no-removal and no-rename rule applies to the 2.x line on `main`
    - Documentation only, no runtime change
    - _Requirements: 23.1, 23.2, 23.3_

- [ ] 12. Verify against a disposable 3.0.0-beta.2 container (commit type: `test`)
  - [ ] 12.1 Confirm the container runner supports local Docker
    - Read `scripts/run_disposable_kuma.ps1` and its `.EXAMPLE`. If it can only target the SSH Docker host, add a local mode that runs `louislam/uptime-kuma:3.0.0-beta.2` on the local Docker bound to `127.0.0.1`, destroys the container afterwards, and keeps any host address out of its output. Keep the `UPTIME_KUMA_V1_*` key names and do not add a way to change the prefix. Document a first boot that shows `setup-database` with `-DockerEnv UPTIME_KUMA_DB_TYPE=sqlite`
    - _Requirements: 19.1_

  - [ ] 12.2 Write the live verification script
    - New file `tests/live_test_auth_v3.py`, standalone and not part of the backup, create, cleanup cycle. It reads `UPTIME_KUMA_V1_URL`, `UPTIME_KUMA_V1_USERNAME` and `UPTIME_KUMA_V1_PASSWORD`, exits when the URL is unset, aborts unless the server reports a 3.x version, deletes everything it creates in a `finally` block, and prints ASCII only
    - Exercise in order: `setup`, `login`, `prepare_2fa`, a TOTP code computed from the `totpURI` secret with the standard library only (`hmac`, `hashlib`, `struct`, `base64`, `time`), `verify_token`, `save_2fa`, logout then a two-factor `login`, `login_by_token` with the stored token (record whether the socket authenticates), `change_password`, `logout`, API-key `add_api_key`, `get_api_keys`, `get_api_key`, `delete_api_key`, `uptime`, `avg_ping`, `get_monitors`, `delete_monitor`, `get_heartbeats` and `get_important_heartbeats`
    - Round-trip every write (create, read back, compare sent and returned fields, reporting ABSENT or MISMATCH), not only "no error"
    - Pace Better Auth requests to stay under 10 per 60 seconds, with a pacing helper that sleeps and retries on 429, or split the run across fresh containers per group of requests
    - _Requirements: 4.4, 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7, 19.8_

  - [ ] 12.3 Write unit tests for the live script's offline logic
    - New file `tests/test_live_auth_helpers_v3.py` (no server, no `UptimeKumaTestCase`, importing helpers only): the standard-library TOTP generator matches the RFC 6238 test vectors, the script refuses to run with the URL unset, aborts on a non-3.x version, the pacing helper keeps requests under 10 per 60 seconds using a fake clock, and every string literal in the script is ASCII
    - _Requirements: 18.8, 19.2, 19.3, 19.7, 19.8_

  - [ ] 12.4 Run the live script against a local throwaway container
    - Start the container through `scripts/run_disposable_kuma.ps1` (local Docker, `127.0.0.1` only), run the script, and capture the result in `.temp/` (gitignored). Record the `login_by_token` outcome (authenticates or not), any round-trip ABSENT or MISMATCH lines, and fix any real defect they reveal in `api.py` with a regression test that fails first. Confirm the container is destroyed
    - _Requirements: 4.4, 19.1, 19.5, 19.6_

  - [ ] 12.5 Apply the Decision B outcome
    - If `login_by_token` authenticated the socket, keep the primary branch and add one unit assertion that mirrors the live finding. If it did not, make `login_by_token` raise `UptimeKumaException` stating it is not supported on Uptime Kuma 3.x and naming password login and the `Authorization` header as alternatives, update `tests/test_session_v3.py` accordingly, and note that the docs in task 13 must state it is unsupported
    - _Requirements: 4.2, 4.3, 4.4_

- [ ] 13. Documentation and changelog (commit type: `docs`)
  - [ ] 13.1 Finish the Sphinx docstrings
    - Make every method changed by this spec follow the existing docstring format (`:param:`, `:return:`, `:rtype:`, `:raises:`, `Example::`), and replace the login docstring note that says an API key cannot authenticate the Socket
    - _Requirements: 13.3, 20.1_

  - [ ] 13.2 Update the Sphinx pages
    - In `docs/` document: that `login` reconnects the Socket and callers must not rely on event data from before login, the Better Auth limit of 10 requests per 60 seconds and its effect on repeated `login` calls, API-key authentication with `Authorization: Bearer <key>` through `UptimeKumaApi(url, headers=...)` with a runnable example and the `apiEnabled` server setting, that a Legacy_Key works only on `/metrics`, and whether `login_by_token` is supported (per task 12.5). Add no `api_key` parameter to `login`
    - _Requirements: 4.4, 13.1, 13.2, 13.4, 13.5, 20.4, 20.5_

  - [ ] 13.3 Write the final Event enum test
    - New file `tests/test_event_enum_v3.py`: assert AUTO_LOGIN, AVG_PING, UPTIME and API_KEY_LIST are gone, SESSION, STATS and HEARTBEAT_LISTS exist, and no handler is registered for a removed member
    - _Requirements: 21.1, 21.2, 21.3_

  - [ ] 13.4 Write the CHANGELOG entry
    - Under the `v3` release heading in `CHANGELOG.md`, add Features, Bugfixes (symptom, cause and fix for each bug, including `KeyError: 'notificationIDList'`) and a BREAKING CHANGES section that lists: the Event enum removals and additions, removal of `enable_api_key` and `disable_api_key` with the reason and `louislam/uptime-kuma#8256`, removal of the `active` parameter of `add_api_key`, the compact `get_monitors()` return with the migration `[api.get_monitor(m["id"]) for m in api.get_monitors()]`, the `avg_ping()` return shape change, and removal of `password` and `googleAnalyticsId` from `save_status_page`
    - Record Decisions A to E, including the outcome of the `login_by_token` live check
    - _Requirements: 12.4, 14.8, 15.4, 15.10, 20.2, 20.3, 20.6, 21.4, 22.2_

  - [ ] 13.5 Update the README support note on v3
    - Update the README support table or note on `v3` only, to describe the Uptime Kuma 3.x support floor for this branch, matching the existing README style
    - _Requirements: 17.1, 20.3_

- [ ] 14. Final checkpoint - Ensure all tests pass
  - Run bare `pytest` (unit suite only, never `-m integration`), `python scripts/check_sdist.py`, and `python -m build` followed by `python -m twine check dist/*` after deleting any `*.egg-info`. Confirm `.temp/`, `tests/.env` and other gitignored files are not staged. Ask the user if questions arise.

## Notes

- Every unit test sub-task is required because the repository standard is that each behavior change has a regression test proven to fail against the unchanged code. Property tests are separate sub-tasks, one per design property, each at least 100 examples.
- No sub-task is a version-gate test, because this spec adds no 3.x release gate (Requirement 18.7 applies only where behavior differs between two 3.x releases, and none does).
- Branch and PR flow: PRs target `v3`, which requires the five checks `full (3.10)` to `full (3.14)`. Do not change the Python matrix in this spec.
- Task 12.4 needs a local Docker daemon. If none is available, run it on the disposable Docker host recorded in the gitignored root `.env`, and never write that host address or user into a tracked file.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3"] },
    { "id": 4, "tasks": ["3.4", "3.5"] },
    { "id": 5, "tasks": ["3.6", "3.7"] },
    { "id": 6, "tasks": ["3.8", "3.9"] },
    { "id": 7, "tasks": ["3.10"] },
    { "id": 8, "tasks": ["4"] },
    { "id": 9, "tasks": ["5.1"] },
    { "id": 10, "tasks": ["5.2", "5.3", "5.4", "5.6", "6.1"] },
    { "id": 11, "tasks": ["5.5", "6.2", "6.3", "6.4", "7.1"] },
    { "id": 12, "tasks": ["6.5", "7.2", "7.3", "8.1"] },
    { "id": 13, "tasks": ["8.2", "8.3"] },
    { "id": 14, "tasks": ["9"] },
    { "id": 15, "tasks": ["10.1", "11.1", "12.1"] },
    { "id": 16, "tasks": ["10.2", "12.2"] },
    { "id": 17, "tasks": ["12.3", "12.4"] },
    { "id": 18, "tasks": ["12.5"] },
    { "id": 19, "tasks": ["13.1", "13.2", "13.3", "13.4", "13.5"] },
    { "id": 20, "tasks": ["14"] }
  ]
}
```
