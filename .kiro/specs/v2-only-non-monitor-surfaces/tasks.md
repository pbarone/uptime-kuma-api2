# Implementation Plan: v2-only-non-monitor-surfaces

## Overview

Extend the v2-only field governance (withhold-and-warn via `UnsupportedFieldWarning`) from monitor fields to the remaining library write surfaces: status pages, maintenance, and settings. The work is sequenced as inventory, verification, implementation, testing, and documentation -- each phase consuming the output of the previous one.

## Tasks

- [x] 1. Upstream Inventory -- identify v2-only fields from server source
  - [x] 1.1 Examine the status-page write path in `louislam/uptime-kuma` source and tags to identify every field `_build_status_page_data` accepts that was introduced at or after 2.0, recording the tag, source file/function, and category (Unconditional_V2_Field / Opt_In_V2_Field / V1_Only_Field)
    - Use `gh` and `git` against `louislam/uptime-kuma` (not web search)
    - Also identify any v2 server fields the Library does not yet expose
    - Record findings in `.kiro/specs/v2-only-non-monitor-surfaces/upstream-inventory.md`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7_
  - [x] 1.2 Examine the maintenance write path (`_build_maintenance_data`) in `louislam/uptime-kuma` to identify any field introduced at or after 2.0
    - Record a negative finding explicitly if no v2-only fields exist
    - _Requirements: 1.6, 4.1, 4.2_
  - [x] 1.3 Examine the settings write path (`set_settings`) in `louislam/uptime-kuma` to identify any field introduced at or after 2.0, beyond the existing `1.23`/`1.23.1` gates
    - Record a negative finding explicitly if no v2-only fields exist
    - _Requirements: 1.6, 5.1_

- [x] 2. Verification Run -- test candidates against a disposable 1.23.x container
  - [x] 2.1 Write `tests/live_test_status_page_v1.py` following the `live_test_v2_only_fields_v1.py` injection technique and the `live_test_conditions_v1.py` safety-guard shape
    - Bypasses the builder to put gated fields on the wire: build v1-safe payload via `_build_status_page_data`, inject one candidate key into the returned `config`, then call `_call('saveStatusPage', ...)` directly
    - One field per status page (a rejected insert names a single column)
    - Reads `UPTIME_KUMA_V1_URL`, `UPTIME_KUMA_V1_USERNAME`, `UPTIME_KUMA_V1_PASSWORD`
    - Confirms server reports `1.23`; refuses to run if URL unset
    - Reads back via `get_status_page` and compares sent vs returned
    - Records per-field verdict: REJECTED / ACCEPTED / ABSENT / MISMATCH / NOT_OBSERVED
    - Deletes created resources in `finally` block; ASCII-only output (PASS/FAIL/->)
    - If maintenance or settings have candidates, exercises those surfaces too
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_
  - [x] 2.2 Run the verification script against a disposable 1.23.x container and record per-field results in `.kiro/specs/v2-only-non-monitor-surfaces/v1-verification-results.md`
    - Drive it with `pwsh -File scripts/run_disposable_kuma.ps1 -Script tests/live_test_status_page_v1.py`. That runner reads the Docker host and SSH user from the gitignored root `.env`, destroys the container in a `finally` (including on readiness timeout), and sanitizes every output line to the `<docker-host>` / `<user>` placeholders — so its transcript satisfies requirement 2.8 as captured. Do not `ssh` and `docker run` by hand: that bypasses the sanitizer and is how the host address reaches a tracked file
    - The `UPTIME_KUMA_V1_URL` / `_USERNAME` / `_PASSWORD` keys already exist in the gitignored `tests/.env`; point the URL at the port the runner publishes (default 3023) and leave the values out of every tracked file
    - State the observed Server_Version, run date, one verdict per field
    - Use `<docker-host>` and `<user>` placeholders for host/credential references
    - Mark any untested field as NOT_OBSERVED; mark surfaces with no candidates as NOT_APPLICABLE
    - _Requirements: 2.8, 2.9, 2.10_

- [x] 3. Checkpoint -- review inventory and verification results
  - Ensure the inventory and verification are complete before proceeding
  - Decide which fields are confirmed v2-only (REJECTED or ABSENT) vs mis-gated (ACCEPTED)
  - If maintenance or settings had candidates, decide whether the positive or negative branch applies
  - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [x] 4. Implementation -- add registry and withhold-and-warn for status pages
  - [x] 4.1 Add `_V2_ONLY_STATUS_PAGE_FIELDS` registry dict to `uptime_kuma_api/api.py`
    - Field name to version floor string, populated from confirmed candidates only
    - Simplified from monitor pattern: no `types` column, no `_RAISE` behavior
    - Exclude the analytics trio (server requires `analyticsType` present)
    - _Requirements: 3.10, 3.2_
  - [x] 4.2 Add `_withheld_status_page_fields(self, supplied)` method to `UptimeKumaApi`
    - Returns list of field names that cannot be sent at the current server version
    - Checks `supplied.get(name) is None` to skip unsupplied fields
    - Compares `self._parsed_version()` against each field's floor
    - _Requirements: 3.1_
  - [x] 4.3 Add `_warn_withheld_status_page_fields(self, withheld, stacklevel)` method
    - Emits one `UnsupportedFieldWarning` per call naming all withheld fields
    - Message includes field name, version floor, and observed server version
    - No-op when `withheld` is empty
    - _Requirements: 3.1, 3.5_
  - [x] 4.4 Integrate withhold-and-warn into `save_status_page` (NOT into `_build_status_page_data`)
    - Compute withheld from `kwargs` BEFORE `get_status_page`, mirroring `edit_monitor` (api.py ~2085)
    - Emit warning before the round trip so an escalated warning costs no fetch
    - Merge only surviving kwargs: `status_page.update({k: v for k, v in kwargs.items() if k not in withheld})`
    - Leave `_build_status_page_data` as a pure builder -- no warning logic added
    - Stacklevel 3: `_warn_withheld_status_page_fields` -> `save_status_page` -> caller
    - _Requirements: 3.1, 3.6, 3.7, 3.8, 3.11_
  - [x] 4.5 If maintenance inventory found no v2-only fields: document the no-op conclusion
    - _Requirements: 4.3, 4.5_
  - [x] 4.6 If settings inventory found no v2-only fields: document the no-op conclusion
    - _Requirements: 5.2, 5.4, 5.5_
  - [ ] 4.7 If maintenance inventory found confirmed v2-only fields: add registry, helper, and integrate into `add_maintenance`/`edit_maintenance` following the same pre-merge pattern
    - _Requirements: 4.4_
  - [ ] 4.8 If settings inventory found confirmed v2-only fields: add registry, helper, and integrate into `set_settings` following the same pattern
    - _Requirements: 5.3_

- [x] 5. Unit tests -- extend `tests/test_status_page_v2.py`
  - [x] 5.1 Add test: v1 + each confirmed Opt_In_V2_Field supplied -> not in config, one warning emitted naming field, floor, server version
    - _Requirements: 7.3, 7.4_
  - [x] 5.1b Add test: v2.0.2 (below the 2.1 floor) + confirmed field -> absent from payload, one warning. Distinguishes 2.1 floor from 2.0 floor.
    - _Requirements: 7.3_
  - [x] 5.2 Add test: v1 + multiple confirmed fields supplied -> not in config, exactly one warning
    - _Requirements: 7.3_
  - [x] 5.3 Add test: v2 + each confirmed Opt_In_V2_Field supplied -> in config with correct value, no warning
    - _Requirements: 7.3_
  - [x] 5.4 Add test: v1 + no opt-in fields supplied (all None) -> no warning at any version
    - _Requirements: 7.5_
  - [x] 5.5 Add test: analytics trio present in v2 payload / absent in v1 payload, no warning
    - _Requirements: 7.6_
  - [x] 5.6 Add test: v1 + `simplefilter("error", UnsupportedFieldWarning)` + opt-in field -> raises, no payload (escalation before `get_status_page`)
    - _Requirements: 7.7_
  - [x] 5.7 Add test: warning message contains field name, version floor, server version
    - _Requirements: 7.4_
  - [x] 5.8 Add test: payload at v1 and v2 with no opt-in fields identical to 2.4.0 behavior
    - _Requirements: 8.1_
  - [x] 5.9 Add test: v1 + mocked `get_status_page` returns config containing a confirmed field + caller passes only `title` -> no warning, server-returned value survives unchanged
    - _Requirements: 3.11, 7.12_
  - [x] 5.10 If maintenance has no v2-only fields: add test asserting identical payload at v1 and v2
    - _Requirements: 4.3, 8.2_
  - [x] 5.11 If settings has no v2-only fields: add test asserting identical payload at v1 and v2
    - _Requirements: 5.2, 8.3_
  - [ ]* 5.12 Write seeded PBT (`STATUS_PAGE_PBT_SEED` / `STATUS_PAGE_PBT_CASES`) for Property 1: Opt-in v2 fields withheld below floor
    - _Requirements: 3.1_
  - [ ]* 5.13 Write seeded PBT for Property 2: Analytics trio is never gated
    - _Requirements: 3.2_
  - [ ]* 5.14 Write seeded PBT for Property 3: No warning when no opt-in field supplied
    - _Requirements: 3.5_
  - [ ]* 5.15 Write seeded PBT for Property 4: Fields included without warning at/above floor
    - _Requirements: 3.6_
  - [ ]* 5.16 Write seeded PBT for Property 5: Server-returned values never treated as caller requests
    - _Requirements: 3.11_

- [x] 6. Checkpoint -- run `pytest -v` and ensure all tests pass
  - Verify each gating test fails against pre-implementation behavior (revert fix, observe failure, restore)
  - _Requirements: 7.10_

- [x] 7. Documentation -- broaden normative location and add changelog
  - [x] 7.1 Broaden the "Version-gated monitor fields" section in `docs/api.rst` to cover all non-monitor surfaces where the rule was extended
    - Name the analytics trio as an exception with reason
    - State "no v2-only surface" for maintenance/settings if that is the conclusion
    - _Requirements: 6.1, 6.2, 6.3, 6.4_
  - [x] 7.2 Add cross-reference from `save_status_page` docstring to normative location
    - _Requirements: 6.5_
  - [x] 7.3 Add `CHANGELOG.md` entry under the next release heading
    - State which surfaces gained withhold-and-warn, name every field whose behavior changes
    - State that the analytics trio is not gated and why
    - State that the change is non-breaking
    - _Requirements: 6.6, 8.9_
  - [x] 7.4 Verify `docs/make.bat html` builds without Sphinx warnings
    - _Requirements: 6.7_
  - [x] 7.5 Update `.kiro/steering/structure.md` live-scripts list: add `live_test_status_page_v1.py` and the already-missing `live_test_v2_only_fields_v1.py` to the v1-targeting section
    - Housekeeping: no requirement covers steering maintenance; this keeps the enumerated list accurate

- [x] 8. Final checkpoint -- full test suite and docs build
  - Run `pytest -v` and confirm green
  - Run `docs/make.bat html` and confirm no Sphinx warnings

## Notes

- The sequencing (inventory -> verification -> implementation) is load-bearing: Requirement 9 mandates each phase consumes the prior phase's output
- The outcome for maintenance and settings may be "nothing to gate" -- tasks 4.5/4.6 and 5.10/5.11 handle the negative branch; tasks 4.7/4.8 handle the positive branch
- If the inventory discovers additional v2-only fields, the registry (4.1) and tests expand accordingly
- If a candidate is ACCEPTED by the 1.23.x server during verification, it is excluded from gating (Req 2.2)
- All unit tests use the existing `MagicMock(spec=UptimeKumaApi)` pattern from `test_status_page_v2.py`
- The verification script (`live_test_status_page_v1.py`) is manual, not CI -- same as `live_test_v2_only_fields_v1.py`
- Each gating test must be verified to fail against pre-implementation behavior (Req 7.10)
- Tasks 5.12-5.16 (seeded PBT) are marked optional (`*`): the five properties are covered example-wise by the 11 tests in this PR; the PBT adds coverage breadth but not a new assertion. Follow-up work.
- Property-based tests use the seeded `PBT_SEED` / `PBT_CASES` idiom (`hypothesis` is not a project dependency)
- Issue #30 (Version_Comparison_Fix) is already closed (2026-08-07); no dependency blocker

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["3"] },
    { "id": 4, "tasks": ["4.1", "4.5", "4.6", "4.7", "4.8"] },
    { "id": 5, "tasks": ["4.2", "4.3"] },
    { "id": 6, "tasks": ["4.4"] },
    { "id": 7, "tasks": ["5.1", "5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9", "5.10", "5.11", "5.12", "5.13", "5.14", "5.15", "5.16"] },
    { "id": 8, "tasks": ["6"] },
    { "id": 9, "tasks": ["7.1", "7.2", "7.3", "7.5"] },
    { "id": 10, "tasks": ["7.4"] },
    { "id": 11, "tasks": ["8"] }
  ]
}
```