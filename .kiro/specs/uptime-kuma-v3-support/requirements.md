# Requirements Document

## Introduction

This spec covers the authentication, API-key and data-event work needed for the `v3` branch of uptime-kuma-api2 to operate against Uptime Kuma 3.0 and later. It implements three items from the tracking issue pbarone/uptime-kuma-api2#88: (1) authentication over Uptime Kuma 3.x's HTTP (Better Auth) flow, (2) the API-key methods adapted to the 3.x shape, and (3) the `stats` event, the compact login-time monitor list and the late-arriving heartbeat history.

On Uptime Kuma 3.x the socket.io events `login`, `loginByToken`, `setup`, `logout`, `changePassword`, `prepare2FA`, `save2FA`, `disable2FA`, `verifyToken`, `twoFAStatus`, `enableAPIKey` and `disableAPIKey` no longer exist. The server authenticates the socket only at handshake, using a session cookie obtained over HTTP. The `uptime` and `avgPing` events are replaced by `stats`, the login-time `monitorList` is compact, and heartbeat history is sent only after the client emits `dashboardRendered`.

The rule that no public name is removed or renamed applies to the 2.x line on `main`, not to `v3`. `v3` is the 3.x major release of the library and supports Uptime Kuma 3.x only. On `v3`, a public name or return shape is kept only when it maps naturally onto Uptime Kuma 3.x. It is dropped or changed when keeping it costs extra code, extra server calls, or misdescribes what the server does. Every such break is listed under BREAKING CHANGES in `CHANGELOG.md`. The library adds no runtime dependency: `requests` and `python-socketio` are already dependencies, and TOTP codes are supplied by the user. The property-based tests use `hypothesis` as a dev-only dependency.

Already done on `v3` and not re-specified here: the removal of the 1.x and 2.x code paths (#96) and the refusal of servers older than 3.0 at connect time using `GET /api/auth/ok` (#95). The audit of other 3.0 surfaces (monitor types, notifications, status pages, maintenance, tags, docker hosts), the 3.0.0b1 release and the swap at 3.0.0 final are out of scope.

Decisions A to D were made on the open questions of the first draft, and Decision E records the public API principle above. They are recorded at the end of this document under "Decisions" and are reflected in the requirements below. Server-side facts cited in this document come from the upstream repository `louislam/uptime-kuma` at tag 3.0.0-beta.2 (for example `server/server.js`) or from upstream issue and PR numbers.

## Glossary

- **Library**: The uptime-kuma-api2 Python package, import name `uptime_kuma_api`, on the `v3` branch.
- **UptimeKumaApi**: The public class in `uptime_kuma_api/api.py` through which callers use the Library.
- **Server**: An Uptime Kuma instance of version 3.0 or later.
- **Server_URL**: The base URL passed to UptimeKumaApi, for example `http://127.0.0.1:3001`.
- **Session_Cookie**: The `better-auth.session_token` cookie that the Server sets after a successful sign-in.
- **Two_Factor_Cookie**: The `better-auth.two_factor` cookie that the Server sets when a sign-in requires a second factor.
- **Socket**: The socket.io connection between UptimeKumaApi and the Server.
- **Auth_Flow**: The sequence of HTTP requests to `/api/auth/*` and `/api/setup` that authenticates a caller, followed by a Socket reconnect that carries the resulting credential in its handshake headers.
- **Origin_Header**: An HTTP `Origin` header whose value equals Server_URL.
- **TOTP_Code**: A time-based one-time password supplied by the caller. The Library never generates it.
- **UptimeKumaException**: The Library's base exception class. `Timeout` is its timeout subclass.
- **Legacy_Key**: An API key carried over from Uptime Kuma 2.x, listed by the Server with an id of the form `legacy-<n>`, which works only on the Prometheus `/metrics` endpoint.
- **Native_Key**: An API key created on Uptime Kuma 3.x through Better Auth, with a string id.
- **Stats_Event**: The `stats` socket.io event, with payload `{monitorId: {avgPing, avgPing720, avgPing1y, uptime24, uptime720, uptime1y}}`.
- **Compact_Monitor_List**: The login-time `monitorList` event payload, which contains only `id`, `name`, `type`, `active`, `parent`, `childrenIDs`, `tags`, `path`, `pathName`, `interval` and `weight` for each monitor.
- **Dashboard_Rendered_Event**: The `dashboardRendered` event that a client emits to make the Server send `heartbeatLists`.
- **Event**: The public enum in `uptime_kuma_api` whose members name the socket.io events that UptimeKumaApi handles.
- **Breaking_Change**: A removal or change of a public method, parameter, enum member or return shape that existed on the 2.x line.
- **Unit_Suite**: The pytest tests that do not extend `UptimeKumaTestCase`, which mock `requests` and the Socket and run in CI.
- **Throwaway_Container**: A disposable `louislam/uptime-kuma:3.0.0-beta.2` container started with `scripts/run_disposable_kuma.ps1`.

## Requirements

### Requirement 1: Password login over the Better Auth flow

**User Story:** As a script author, I want `login(username, password)` to work against Uptime Kuma 3.x, so that my existing automation keeps authenticating.

#### Acceptance Criteria

1. WHEN login is called with a username and a password, THE UptimeKumaApi SHALL send `POST {Server_URL}/api/auth/sign-in/username` with a JSON body containing `username`, `password` and `rememberMe`, and an Origin_Header.
2. WHEN the sign-in response sets a Session_Cookie, THE UptimeKumaApi SHALL reconnect the Socket with that cookie in a `Cookie` handshake header, combined with any headers the caller passed to the constructor.
3. WHEN the Socket has reconnected after sign-in, THE UptimeKumaApi SHALL wait for the post-login `info` event before login returns, so that the `version` property is available to the caller.
4. IF the sign-in response status is not a success status, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException that contains the error message returned by the Server.
5. IF the Server answers a Better Auth request with HTTP status 429, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException that states the request was rate limited.
6. IF the sign-in request fails with a network error or a timeout, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException or Timeout instead of a `requests` exception.
7. THE UptimeKumaApi SHALL send the sign-in request using the constructor's timeout and SSL-verification settings.
8. THE UptimeKumaApi SHALL return from login a dict with the same top-level shape as before, a `token` key whose value is the Session_Cookie value.

### Requirement 2: Two-factor login

**User Story:** As a script author with 2FA enabled, I want `login(username, password, token=...)` to complete a two-factor sign-in, so that my automation works on accounts that require a second factor.

#### Acceptance Criteria

1. WHEN the sign-in response contains `twoFactorRedirect` set to true and a TOTP_Code was passed as the `token` argument, THE UptimeKumaApi SHALL send `POST {Server_URL}/api/auth/two-factor/verify-totp` with a JSON body containing `code`, the Two_Factor_Cookie and an Origin_Header.
2. WHEN the verify-totp response sets a Session_Cookie, THE UptimeKumaApi SHALL continue the Auth_Flow as in Requirement 1.
3. IF the sign-in response contains `twoFactorRedirect` set to true and no TOTP_Code was passed, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException stating that a 2FA token is required.
4. IF the verify-totp response status is not a success status, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException that contains the error message returned by the Server.
5. THE UptimeKumaApi SHALL generate no TOTP_Code itself and SHALL add no runtime dependency to the Library.

### Requirement 3: Login without credentials when the Server has authentication disabled

**User Story:** As a script author connecting to a Server with `disableAuth` on, I want `login()` with no arguments to succeed, so that the no-auth setup keeps working.

#### Acceptance Criteria

1. WHEN login is called with neither a username nor a password, THE UptimeKumaApi SHALL wait for the `session` event that the Server emits at connect time and then return.
2. IF the `session` event does not arrive within the constructor's timeout, THEN THE UptimeKumaApi SHALL raise a Timeout.
3. THE UptimeKumaApi SHALL no longer register or wait for the `autoLogin` event.

### Requirement 4: Token login

**User Story:** As a script author who stored a login token, I want `login_by_token(token)` to re-authenticate, so that I do not have to store a password.

#### Acceptance Criteria

1. WHEN login is called successfully, THE UptimeKumaApi SHALL return in the `token` key the Session_Cookie value.
2. WHEN login_by_token is called with a Session_Cookie value, THE UptimeKumaApi SHALL reconnect the Socket with that value in a `Cookie` handshake header named `better-auth.session_token` and wait for the post-login `info` event.
3. IF the Server rejects the Session_Cookie value, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException.
4. IF the live verification against a Throwaway_Container shows that reconnecting with a Session_Cookie value does not authenticate the Socket, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException from login_by_token stating that login_by_token is not supported on Uptime Kuma 3.x and naming password login and the `Authorization` header as the supported alternatives, and THE Library documentation SHALL state that login_by_token is unsupported.

### Requirement 5: Logout

**User Story:** As a script author, I want `logout()` to end my session, so that the session token stops being valid.

#### Acceptance Criteria

1. WHEN logout is called, THE UptimeKumaApi SHALL send `POST {Server_URL}/api/auth/sign-out` with the Session_Cookie and an Origin_Header.
2. WHEN the sign-out response is a success status, THE UptimeKumaApi SHALL forget the stored Session_Cookie.
3. WHEN logout is called, THE UptimeKumaApi SHALL leave the Socket connected.
4. IF logout is called when no Session_Cookie is stored, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException stating that the session is not logged in.
5. IF the sign-out response status is not a success status, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException that contains the error message returned by the Server.

### Requirement 6: First-run setup and setup check

**User Story:** As a script author provisioning a new instance, I want `need_setup()` and `setup(username, password)` to work, so that I can create the first user automatically.

#### Acceptance Criteria

1. WHEN need_setup is called, THE UptimeKumaApi SHALL emit the `needSetup` socket event and return its boolean result.
2. WHEN setup is called, THE UptimeKumaApi SHALL send `POST {Server_URL}/api/setup` with a JSON body containing `username` and `password`, and an Origin_Header.
3. IF the setup response status is not a success status, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException that contains the error message returned by the Server.
4. THE UptimeKumaApi SHALL return from setup a dict, with the same top-level shape as before.

### Requirement 7: Change password

**User Story:** As a script author, I want `change_password(old_password, new_password)` to work, so that I can rotate credentials.

#### Acceptance Criteria

1. WHEN change_password is called, THE UptimeKumaApi SHALL send `POST {Server_URL}/api/auth/change-password` with a JSON body containing `currentPassword`, `newPassword` and `revokeOtherSessions`, the Session_Cookie and an Origin_Header.
2. THE UptimeKumaApi SHALL send `revokeOtherSessions` set to false, so that the session used by the Socket stays valid.
3. IF the change-password response status is not a success status, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException that contains the error message returned by the Server.
4. IF change_password is called when no Session_Cookie is stored, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException stating that the session is not logged in.

### Requirement 8: Two-factor enrollment and status

**User Story:** As a script author, I want the 2FA methods to work, so that I can enroll, check and disable 2FA from code.

#### Acceptance Criteria

1. WHEN twofa_status is called, THE UptimeKumaApi SHALL send `GET {Server_URL}/api/auth/get-session` with the Session_Cookie and return a dict containing the boolean `status` taken from `user.twoFactorEnabled`.
2. WHEN prepare_2fa is called with a password, THE UptimeKumaApi SHALL send `POST {Server_URL}/api/auth/two-factor/enable` with a JSON body containing `password`, the Session_Cookie and an Origin_Header, and return the `totpURI` and `backupCodes` that the Server returns.
3. WHEN verify_token is called with a TOTP_Code, THE UptimeKumaApi SHALL send `POST {Server_URL}/api/auth/two-factor/verify-totp` with a JSON body containing `code`, the Session_Cookie and an Origin_Header, and return a dict with the boolean `valid`.
4. WHEN save_2fa is called, THE UptimeKumaApi SHALL complete enrollment using the `two-factor/enable` and `verify-totp` requests of this requirement and return a dict containing a `msg` key.
5. WHEN disable_2fa is called with a password, THE UptimeKumaApi SHALL send `POST {Server_URL}/api/auth/two-factor/disable` with a JSON body containing `password`, the Session_Cookie and an Origin_Header.
6. IF any request of this requirement returns a non-success status, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException that contains the error message returned by the Server.
7. IF a method of this requirement is called when no Session_Cookie is stored, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException stating that the session is not logged in.

### Requirement 9: Shared HTTP behavior of the Auth_Flow

**User Story:** As a maintainer, I want every Better Auth request to be built the same way, so that cookie, origin and error handling cannot drift between methods.

#### Acceptance Criteria

1. THE UptimeKumaApi SHALL send an Origin_Header with every Auth_Flow request that carries a cookie.
2. THE UptimeKumaApi SHALL send the constructor's custom headers, timeout and SSL-verification setting with every Auth_Flow request.
3. IF an Auth_Flow request receives a response whose body is not valid JSON where JSON is expected, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException instead of a JSON decoding error.
4. THE UptimeKumaApi SHALL raise errors from the Auth_Flow only as UptimeKumaException or Timeout.
5. THE UptimeKumaApi SHALL leave no credential, cookie value or password in an exception message or log output.
6. WHEN the Socket reconnects as part of the Auth_Flow, THE UptimeKumaApi SHALL discard cached event data from the previous connection, so that the caller sees only data sent to the authenticated Socket.
7. WHEN the Socket reconnects as part of the Auth_Flow, THE UptimeKumaApi SHALL keep the same UptimeKumaApi instance usable by the caller, with no change to the instance's public attributes.

### Requirement 10: API-key listing and lookup

**User Story:** As a script author, I want `get_api_keys()` and `get_api_key(id_)` to work on Uptime Kuma 3.x, so that I can inventory keys.

#### Acceptance Criteria

1. WHEN get_api_keys is called, THE UptimeKumaApi SHALL emit the `getAPIKeyList` socket event and return the list that the Server returns in the callback, without waiting for an `apiKeyList` event.
2. THE UptimeKumaApi SHALL return Native_Key objects with their string `id` unchanged.
3. THE UptimeKumaApi SHALL return Legacy_Key objects with their `legacy-<n>` string `id` unchanged.
4. WHEN get_api_key is called with an id, THE UptimeKumaApi SHALL return the key whose `id` equals the given id, comparing as strings.
5. IF get_api_key is called with an id that no key has, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException stating that the api key does not exist.

### Requirement 11: API-key creation and deletion

**User Story:** As a script author, I want to create and delete API keys, so that I can manage credentials from code.

#### Acceptance Criteria

1. WHEN add_api_key is called, THE UptimeKumaApi SHALL emit `addAPIKey` and return the server response including the `key` and the string `keyID`.
2. THE UptimeKumaApi SHALL remove the `active` parameter from add_api_key, because the Server ignores it.
3. WHEN delete_api_key is called with the id of a Native_Key or a Legacy_Key, THE UptimeKumaApi SHALL emit `deleteAPIKey` with that id as a string and return the server response.
4. IF delete_api_key is called with an id that no key has, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException stating that the api key does not exist, before emitting `deleteAPIKey`.
5. THE UptimeKumaApi SHALL accept an integer or a string for the id argument of get_api_key and delete_api_key, converting it to a string before comparison.

### Requirement 12: API-key enable and disable are removed

**User Story:** As a script author, I want the library to expose only operations that Uptime Kuma 3.x supports, so that I am not offered methods that cannot work.

#### Acceptance Criteria

1. THE UptimeKumaApi SHALL remove the public methods enable_api_key and disable_api_key, with no stub that raises, because Uptime Kuma 3.x removed the feature by design (louislam/uptime-kuma#8256, closed as not a bug, with the maintainer answer that only removing a disabled key is supported).
2. THE UptimeKumaApi SHALL remove the code that emits `enableAPIKey` and `disableAPIKey`.
3. THE Library documentation SHALL remove enable_api_key and disable_api_key from `docs/api.rst`.
4. THE `CHANGELOG.md` entry SHALL list the removal of enable_api_key and disable_api_key under BREAKING CHANGES, with the reason and the upstream issue number.

### Requirement 13: API-key authentication

**User Story:** As a script author, I want to know how to authenticate with an API key on Uptime Kuma 3.x, so that I can avoid storing a password.

#### Acceptance Criteria

1. THE Library documentation SHALL state that a Native_Key authenticates the Socket when sent as an `Authorization: Bearer <key>` handshake header and that the Server setting `apiEnabled` must be on.
2. THE Library documentation SHALL state that a Legacy_Key works only on `/metrics` and cannot authenticate the Socket.
3. THE Library documentation SHALL replace the existing note in the login docstring that says an API key cannot authenticate the Socket.
4. THE UptimeKumaApi SHALL add no `api_key` parameter to login and no other new public parameter for API-key authentication.
5. THE Library documentation SHALL show a runnable example that passes the `Authorization` header through `UptimeKumaApi(url, headers=...)`.

### Requirement 14: Uptime and average ping from the stats event

**User Story:** As a script author, I want `uptime()` and `avg_ping()` to keep working, so that my dashboards and checks do not break.

#### Acceptance Criteria

1. WHEN a Stats_Event arrives, THE UptimeKumaApi SHALL store the payload for every monitor id in it.
2. WHEN uptime is called, THE UptimeKumaApi SHALL return a dict keyed by integer monitor id, each value holding `24`, `720` and `1y` mapped from `uptime24`, `uptime720` and `uptime1y`, the same shape that uptime returned before.
3. WHEN avg_ping is called, THE UptimeKumaApi SHALL return a dict keyed by integer monitor id, each value holding all three averages of the Stats_Event, `avgPing`, `avgPing720` and `avgPing1y`, with the key naming chosen in the design.
4. WHEN a later Stats_Event arrives for a monitor id that already has stored data, THE UptimeKumaApi SHALL replace the stored values for that monitor id with the new values and keep stored values for other monitor ids.
5. IF the Server has no monitors, THEN THE UptimeKumaApi SHALL return an empty result from uptime and avg_ping without waiting for the timeout.
6. IF no Stats_Event arrives within the constructor's timeout while monitors exist, THEN THE UptimeKumaApi SHALL raise a Timeout.
7. THE UptimeKumaApi SHALL keep the shape of the uptime return value unchanged, with the keys `24`, `720` and `1y`, because it maps directly onto `uptime24`, `uptime720` and `uptime1y`.
8. THE `CHANGELOG.md` entry SHALL list the change of the avg_ping return shape under BREAKING CHANGES.

### Requirement 15: Compact monitor list

**User Story:** As a script author, I want `get_monitors()` and `delete_monitor(id_)` to work against the compact login-time list, so that they stop raising `KeyError: 'notificationIDList'`.

#### Acceptance Criteria

1. WHEN get_monitors is called, THE UptimeKumaApi SHALL return one dict per monitor containing only the compact fields that Uptime Kuma 3.x sends in the Compact_Monitor_List, without raising an exception for a missing field and without sending a `getMonitor` request.
2. WHEN the cache receives a monitor in full form through `updateMonitorIntoList` or a similar event, THE UptimeKumaApi SHALL reduce that entry to the compact fields, so that every entry returned by get_monitors has the same compact shape and no caller receives a mix of compact and full entries.
3. THE UptimeKumaApi SHALL add no `full` parameter or other new parameter to get_monitors.
4. THE get_monitors docstring and the `CHANGELOG.md` BREAKING CHANGES entry SHALL give the migration `[api.get_monitor(m["id"]) for m in api.get_monitors()]` for callers that need full records.
5. WHEN get_monitor is called with an id, THE UptimeKumaApi SHALL keep returning the full monitor record.
6. WHEN delete_monitor is called with the id of an existing monitor, THE UptimeKumaApi SHALL emit `deleteMonitor` and return the server response without reading `notificationIDList` or any other non-compact field from the monitor cache.
7. IF delete_monitor is called with an id that is not in the monitor cache, THEN THE UptimeKumaApi SHALL raise an UptimeKumaException, as it did before.
8. THE UptimeKumaApi SHALL make every other internal reader of the monitor cache rely only on compact fields or call `getMonitor` itself.
9. WHEN a monitor is added, edited or deleted, THE UptimeKumaApi SHALL keep the cached monitor list consistent with the `updateMonitorIntoList` and `deleteMonitorFromList` events, as it did before.
10. THE `CHANGELOG.md` entry SHALL list the change of the get_monitors return shape under BREAKING CHANGES.

### Requirement 16: Heartbeat history after dashboardRendered

**User Story:** As a script author, I want `get_heartbeats()` and `get_important_heartbeats()` to keep working, so that I can read monitor history.

#### Acceptance Criteria

1. WHEN get_heartbeats or get_important_heartbeats is called and no heartbeat history has been received, THE UptimeKumaApi SHALL emit the Dashboard_Rendered_Event once and wait for the `heartbeatLists` event.
2. WHEN a `heartbeatLists` event arrives, THE UptimeKumaApi SHALL store its payload so that get_heartbeats and get_important_heartbeats return the same shapes as before.
3. WHEN get_heartbeats or get_important_heartbeats is called a second time on the same connection, THE UptimeKumaApi SHALL NOT emit the Dashboard_Rendered_Event again.
4. IF the Server has no monitors, THEN THE UptimeKumaApi SHALL return an empty result without emitting the Dashboard_Rendered_Event and without waiting for the timeout.
5. IF no `heartbeatLists` event arrives within the constructor's timeout while monitors exist, THEN THE UptimeKumaApi SHALL raise a Timeout.
6. WHEN the Socket reconnects as part of the Auth_Flow, THE UptimeKumaApi SHALL treat the Dashboard_Rendered_Event as not yet emitted for the new connection.

### Requirement 17: Version gating and compatibility between 3.x releases

**User Story:** As a user on an earlier Uptime Kuma 3.x release, I want the Library to keep working, so that a later 3.x release does not break me.

#### Acceptance Criteria

1. THE UptimeKumaApi SHALL treat Uptime Kuma 3.0 as the floor for every behavior in this spec and SHALL add no code path for Uptime Kuma 1.x or 2.x.
2. WHERE a later 3.x release changes a behavior in this spec, THE UptimeKumaApi SHALL gate the new behavior behind `parse_version(self.version)`, and SHALL keep the 3.0 behavior for earlier releases.
3. THE UptimeKumaApi SHALL keep a public method name, argument or return shape that existed before this spec only where it maps naturally onto Uptime Kuma 3.x, and SHALL list every Breaking_Change under BREAKING CHANGES in `CHANGELOG.md`.
4. THE Library SHALL add new public API only where Uptime Kuma 3.x needs it, and the decisions below settle that authentication needs none.
5. THE Library SHALL keep the version-gating mechanism, consisting of `parse_version`, the per-field floor tables and UnsupportedFieldWarning, for future 3.x releases.
5. THE Library SHALL add no runtime dependency.

### Requirement 18: Unit tests

**User Story:** As a maintainer, I want unit tests that fail against unchanged code, so that each requirement is protected from regression.

#### Acceptance Criteria

1. THE Unit_Suite SHALL cover every requirement in this document by mocking `requests` and the Socket, with no live server and no `UptimeKumaTestCase` subclass.
2. WHEN the new tests are run against the code as it was before this spec, THE Unit_Suite SHALL report a failure for each requirement that changes behavior.
3. THE Unit_Suite SHALL assert, for each Auth_Flow request, the URL, the JSON body, the presence of the Origin_Header and the cookie sent.
4. THE Unit_Suite SHALL cover the Auth_Flow success path, the failure path and the 429 path for login.
5. THE Unit_Suite SHALL cover a Stats_Event payload round trip using `hypothesis`: for generated payloads, feeding a Stats_Event to the handler and calling uptime and avg_ping SHALL return the mapped `24`, `720` and `1y` values and all three average values for every monitor id.
6. THE Unit_Suite SHALL cover a Compact_Monitor_List that omits `notificationIDList` for get_monitors and delete_monitor, and SHALL cover that a full-form cache entry is reduced to the compact fields.
7. WHERE a behavior differs between two 3.x releases, THE Unit_Suite SHALL test one version on each side of the gate.
8. THE Unit_Suite SHALL print only ASCII characters in any output of scripts it adds.

### Requirement 19: Live verification

**User Story:** As a maintainer, I want live verification against a real 3.x server, so that the mocked behavior matches the actual server.

#### Acceptance Criteria

1. THE live verification script SHALL run against a Throwaway_Container through `scripts/run_disposable_kuma.ps1` and SHALL NOT be added to the backup, create and cleanup cycle of the `tests/.env` target.
2. THE live verification script SHALL read its URL and credentials from `UPTIME_KUMA_V1_URL`, `UPTIME_KUMA_V1_USERNAME` and `UPTIME_KUMA_V1_PASSWORD` and SHALL refuse to run when the URL is unset.
3. THE live verification script SHALL abort unless the Server reports a 3.x version.
4. THE live verification script SHALL delete every resource it creates in a `finally` block.
5. THE live verification script SHALL round-trip each write: create a resource, read it back, and compare sent and returned fields.
6. THE live verification script SHALL exercise setup, login, a two-factor login with a TOTP_Code computed by the script from the secret in `totpURI` using the standard library only, change_password, logout, API-key add, list and delete, uptime, avg_ping, get_monitors, delete_monitor, get_heartbeats and get_important_heartbeats.
7. THE live verification script SHALL pace its Better Auth requests to stay under the Server limit of 10 requests per 60 seconds, or SHALL start a fresh container for each group of requests that would exceed it.
8. THE live verification script SHALL print only ASCII characters.

### Requirement 20: Documentation and changelog

**User Story:** As a user of the Library, I want the docs to describe how authentication works on Uptime Kuma 3.x, so that I can use it without reading the source.

#### Acceptance Criteria

1. THE Library SHALL update the docstrings of every method changed by this spec to follow the Sphinx format of the existing docstrings, including `:param:`, `:return:`, `:rtype:`, `:raises:` and an `Example::` block.
2. THE Library SHALL add a `CHANGELOG.md` entry under the `v3` release heading for each user-facing change, with the symptom, cause and fix for each bug.
3. THE Library SHALL state in the `CHANGELOG.md` entry each decision recorded under "Decisions", including the outcome of the login_by_token live verification.
4. THE Library SHALL document that login on Uptime Kuma 3.x reconnects the Socket, and that callers holding references from before login must not rely on previous event data.
5. THE Library SHALL document the Better Auth rate limit of 10 requests per 60 seconds and its effect on repeated login calls.
6. THE `CHANGELOG.md` entry SHALL contain a BREAKING CHANGES section that lists every Breaking_Change of this spec: the Event enum changes, the removal of enable_api_key and disable_api_key, the removal of the `active` parameter of add_api_key, the get_monitors return shape, the avg_ping return shape, and the removal of the `password` and `googleAnalyticsId` parameters of save_status_page.

### Requirement 21: Event enum

**User Story:** As a script author, I want the Event enum to list only events that Uptime Kuma 3.x sends, so that no member names an event that never arrives.

#### Acceptance Criteria

1. THE Event enum SHALL remove the members AUTO_LOGIN, AVG_PING, UPTIME and API_KEY_LIST, because their wire events no longer exist on Uptime Kuma 3.x (`server/server.js` at tag 3.0.0-beta.2).
2. THE Event enum SHALL add the members SESSION, STATS and HEARTBEAT_LISTS for the `session`, `stats` and `heartbeatLists` events.
3. THE UptimeKumaApi SHALL register handlers for SESSION, STATS and HEARTBEAT_LISTS and no handler for the removed members.
4. THE `CHANGELOG.md` entry SHALL list the removed and added Event members under BREAKING CHANGES.

### Requirement 22: save_status_page parameters

**User Story:** As a script author, I want save_status_page to accept only parameters that have an effect on Uptime Kuma 3.x, so that I am not misled by parameters that do nothing.

#### Acceptance Criteria

1. THE UptimeKumaApi SHALL remove the `password` and `googleAnalyticsId` parameters from save_status_page, because they have no effect on Uptime Kuma 3.x and PR #96 left them accepted and ignored.
2. THE `CHANGELOG.md` entry SHALL list the removal of both parameters under BREAKING CHANGES.

### Requirement 23: Steering and AGENTS.md wording

**User Story:** As a maintainer, I want the repository guidance to state the v3 public API principle, so that later work on `v3` does not apply the 2.x compatibility rule by mistake.

#### Acceptance Criteria

1. THE Library SHALL reword the statements in the `v3` steering files under `.kiro/steering/` that call the public API a contract on `v3`, so that they state the principle recorded in Decision E.
2. THE Library SHALL reword the same statement in `AGENTS.md`.
3. THE reworded text SHALL state that the no-removal and no-rename rule applies to the 2.x line on `main`.

### Requirement 24: Dev-only property-based testing dependency

**User Story:** As a maintainer, I want `hypothesis` available to the unit suite in CI without becoming a runtime dependency, so that property-based tests run in CI and users install nothing extra.

#### Acceptance Criteria

1. THE Library SHALL add `hypothesis` to `dev-requirements.txt`.
2. THE Library SHALL install `hypothesis` in `.github/workflows/test.yml` and `.github/workflows/publish.yml`, the two workflows that run the Unit_Suite.
3. THE Library SHALL NOT add `hypothesis` to `install_requires` in `setup.py`.

## Decisions

These decisions were made on the open questions of the first draft. The requirements above reflect them.

- **Decision A (API-key authentication on 3.x):** The Library documents the `Authorization: Bearer <key>` header passed through `UptimeKumaApi(url, headers=...)`. It adds no `api_key` parameter to `login()` and no other public API. Affects Requirements 13 and 17.
- **Decision B (login_by_token on 3.x):** `login_by_token(token)` reconnects the Socket with the Session_Cookie value. If the live test against `louislam/uptime-kuma:3.0.0-beta.2` shows that this does not authenticate the Socket, `login_by_token` is documented as unsupported and raises an UptimeKumaException. Affects Requirement 4.
- **Decision C (return value of login on 3.x):** `login()` returns `{"token": <Session_Cookie value>}`. The value is a Better Auth session token, not a JWT. Affects Requirements 1 and 4.
- **Decision D (logout and the Socket):** `logout()` does not disconnect the Socket. Affects Requirement 5.

- **Decision E (public API on v3):** The rule that no public name is removed or renamed applies to the 2.x line on `main`, not to `v3`. On `v3`, a public name or return shape is kept only when it maps naturally onto Uptime Kuma 3.x, and is dropped or changed when keeping it costs extra code, extra server calls, or misdescribes what the server does. Every break is listed under BREAKING CHANGES in `CHANGELOG.md`. The version-gating mechanism stays for future 3.x releases. Affects Requirements 10 to 12, 14, 15, 17 and 20 to 24.
- **Consequences of Decision E:** enable_api_key and disable_api_key are removed (louislam/uptime-kuma#8256); add_api_key loses `active`; `get_monitors()` returns compact records with no per-monitor fetch; `avg_ping()` returns all three windows; save_status_page loses `password` and `googleAnalyticsId`; the Event enum drops AUTO_LOGIN, AVG_PING, UPTIME and API_KEY_LIST and adds SESSION, STATS and HEARTBEAT_LISTS.

Other choices from the first draft are also accepted: a 2FA login without a code raises an exception, `save_2fa` maps to `two-factor/enable` plus `verify-totp` pending confirmation in the design phase, the live script computes TOTP codes with the standard library, and the live script stays under the Better Auth rate limit.