# v1 Verification Results: Status-Page Fields

Run date: 2025-01-27
Observed Server_Version: 1.23.2
Container: disposable `louislam/uptime-kuma:1.23.2` on `<docker-host>`
Runner: `pwsh -File scripts/run_disposable_kuma.ps1 -Script tests/live_test_status_page_v1.py`

## Status Pages

| Field | Verdict | Detail |
|---|---|---|
| `showOnlyLastHeartbeat` | `ABSENT` | the save succeeded and the field did not come back |
| `rssTitle` | `ABSENT` | the save succeeded and the field did not come back |

Both fields are confirmed v2-only: the 1.23.2 server accepts the payload
(no rejection) but silently discards the fields (they do not appear on
read-back). The gate is correct: withholding them on a pre-2.0 server is
appropriate because sending them has no effect.

## Maintenance

Verdict: `NOT_APPLICABLE`

The upstream inventory found no v2-only maintenance fields. Every field
`_build_maintenance_data` accepts today exists identically in both 1.23.2
and 2.5.0. No probes were needed.

## Settings

Verdict: `NOT_APPLICABLE`

The upstream inventory found no v2-only settings fields. The server's
`setSettings` handler stores arbitrary key-value pairs with no allowlist
or version-specific validation. No probes were needed.

## Summary

- 2 status-page fields probed, both `ABSENT` -- confirmed v2-only
- Maintenance: no candidates exist, `NOT_APPLICABLE`
- Settings: no candidates exist, `NOT_APPLICABLE`
- 0 fields `ACCEPTED` (no mis-gated field found)
- 0 fields `NOT_OBSERVED` (run is complete)

## Conclusion for implementation

`_V2_ONLY_STATUS_PAGE_FIELDS` should contain:
- `"showOnlyLastHeartbeat": "2.0"` (conservative floor -- field introduced
  in 2.1.0 but the library's existing `>= 2.0` gate is the correct
  boundary because a 2.0.x server would also ABSENT this field)
- `"rssTitle": "2.0"` (same reasoning)

No registry is needed for maintenance or settings.