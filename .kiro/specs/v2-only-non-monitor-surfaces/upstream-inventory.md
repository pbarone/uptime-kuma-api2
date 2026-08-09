# Upstream Inventory: v2-only Non-Monitor Surfaces

Examination date: 2025-01-27
Upstream tags examined: 1.23.2 (last v1), 2.0.0, 2.0.2, 2.1.0, 2.5.0 (latest)
Repository: louislam/uptime-kuma
Method: `gh api` + `Invoke-WebRequest` against raw file URLs per tag

## Status Pages

Server-side handler: `server/socket-handlers/status-page-socket-handler.js`
Server-side model: `server/model/status_page.js`

### Fields present in 2.1.0+ but absent in 2.0.2 and 1.23.2

| Field (config key) | Introduced in | Absent at | Server-side source | Category |
|---|---|---|---|---|
| `analyticsType` | 2.1.0 | 2.0.2 | `status-page-socket-handler.js` -> `statusPage.analytics_type` | Unconditional_V2_Field |
| `analyticsScriptUrl` | 2.1.0 | 2.0.2 | `status-page-socket-handler.js` -> `statusPage.analytics_script_url` | Unconditional_V2_Field |
| `showOnlyLastHeartbeat` | 2.1.0 | 2.0.2 | `status-page-socket-handler.js` -> `statusPage.show_only_last_heartbeat` | Opt_In_V2_Field |
| `rssTitle` | 2.1.0 | 2.0.2 | `status-page-socket-handler.js` -> `statusPage.rss_title` | Opt_In_V2_Field |

### Fields present across version boundaries (not v2-only)

| Field (config key) | Notes |
|---|---|
| `analyticsId` | Present at 2.0.0 as `config.googleAnalyticsId` -> `google_analytics_tag_id` column. At 2.1.0, same config key `analyticsId` -> new `analytics_id` column. The key name change is a library-side mapping, not a server-side introduction. The underlying functionality (an analytics identifier) exists at all examined versions. |
| `googleAnalyticsId` | Present at 1.23.2 through 2.0.2 (`google_analytics_tag_id` column). Removed at 2.1.0 (column gone). The library's `else` branch correctly sends this on pre-2.0 servers. **Defect filed as issue #41**: the library's `>= 2.0` branch omits this key from 2.0.x servers that still use it. |

### Fields present in 1.23.2 but removed/replaced in 2.1.0+

| Field (config key) | Removed/replaced in | Category |
|---|---|---|
| `googleAnalyticsId` | Replaced by analytics trio in 2.1.0 | V1_Only_Field (but see issue #41 -- should also be sent on 2.0.x) |
| `password` | Removed in 2.0.0 (status page auth removed) | V1_Only_Field |

### Fields present at 2.0.0 but absent at 1.23.2

| Field (config key) | Introduced in | Notes |
|---|---|---|
| `autoRefreshInterval` | 2.0.0 | Library pops this from `get_status_page` response but never exposes it as a `save_status_page` parameter. The 2.x server reads `config.autoRefreshInterval`. Future candidate for a new parameter (separate `feat`). Not a gating candidate because the library does not send it. |

### Fields the Library does not yet expose

| Field (config key) | Introduced in | Notes |
|---|---|---|
| `autoRefreshInterval` | 2.0.0 | See above. |

## Maintenance

Server-side handler: `server/socket-handlers/maintenance-socket-handler.js`
Server-side model: `server/model/maintenance.js` -> `Maintenance.jsonToBean()`

### Comparison of `jsonToBean` between 1.23.2 and 2.5.0

Both versions accept **identical fields**: `title`, `description`, `strategy`,
`intervalDay`, `timezoneOption`, `active`, `dateRange`, `durationMinutes`,
`cron`, `timeRange`, `weekdays`, `daysOfMonth`.

The only difference is that 2.5.0 adds date validation (throwing "Invalid start
date" / "Invalid end date" for unparseable or year>9999 dates), which is a
behavioral difference on existing fields, not a new field.

### Conclusion

**Maintenance has no v2-only surface.** Every field `_build_maintenance_data`
accepts today exists in both 1.23.2 and 2.5.0. No new field was introduced
at or after 2.0. No version gate is needed.

## Settings

Server-side handler: `server/server.js` -> `socket.on("setSettings", ...)`
Server-side storage: `server/settings.js` -> `Settings.setSettings(type, data)`

### How `setSettings` works server-side

The server's `setSettings` method stores **any key-value pair** the client
sends, iterating `Object.keys(data)` and writing each to the `setting`
table. There is no allowlist or schema validation on the key set. A v1.x
server will store any key the client sends and return it on `getSettings`.

### Conclusion

**Settings has no v2-only surface in terms of server-side field validation.**
The server accepts arbitrary keys at every version. The Library's existing
`1.23` and `1.23.1` gates on `chromeExecutable` and `nscd` are *client-side*
decisions about which fields are meaningful at which version, not reflections
of server-side rejection.

No settings field that `set_settings` accepts today was introduced at or
after 2.0 by the server (the server has never refused any settings key).
No version gate is needed beyond the existing ones.