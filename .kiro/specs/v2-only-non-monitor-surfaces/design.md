# Design Document: v2-only-non-monitor-surfaces

## Overview

This feature extends the v2-only field governance -- the withhold-and-warn rule
established by issue #14 for monitor fields -- to the remaining library write
surfaces: status pages, maintenance, and settings.

The work is structured in three sequential phases:

1. **Upstream Inventory** -- examine `louislam/uptime-kuma` source and tags to
   identify which fields each surface gained at or after 2.0.
2. **Verification Run** -- test each candidate against a disposable 1.23.x
   container to confirm whether the server accepts or rejects it.
3. **Implementation** -- extend the withhold-and-warn rule where confirmed, and
   document the finding for surfaces with no v2-only fields.

The outcome may be "nothing to gate" for maintenance and settings. That is a
valid conclusion, and the design accommodates it as a first-class result.

### Design Decisions

1. **Reuse the existing warning class.** `UnsupportedFieldWarning` already
   covers this use case -- its name says "field" not "monitor field", and its
   escalation semantics (`simplefilter("error", ...)`) should apply uniformly.
   No new warning class.

2. **Separate registry, parallel helper.** The monitor registry
   (`_V2_ONLY_MONITOR_FIELDS`) carries type-restriction logic (`types` column,
   `_RAISE` behavior) that status pages do not need. A separate, simpler
   registry for status pages (`_V2_ONLY_STATUS_PAGE_FIELDS`) avoids polluting
   either with foreign concepts. The warning helper (`_warn_withheld_v2_fields`)
   is monitor-specific in its message wording and stacklevel; a parallel
   `_warn_withheld_status_page_fields` keeps each surface's warning text
   accurate.

3. **The analytics trio is documented as an exception, not gated.** The v2
   server requires `analyticsType` present and rejects its absence. Withholding
   is not an available outcome, so these fields are excluded from any registry.

4. **Inventory and verification produce spec-directory artifacts.** Results are
   recorded as markdown files in `.kiro/specs/v2-only-non-monitor-surfaces/` --
   they are evidence consumed by the implementation, not runtime deliverables.

5. **Withhold-and-warn lives in `save_status_page`, not in the builder.**
   `save_status_page` is a read-modify-write: it fetches the server's config,
   overlays `kwargs`, and passes the merged dict to `_build_status_page_data`.
   By the time the builder runs, caller-supplied values and server-returned values
   are indistinguishable. Placing the withheld computation in `save_status_page`
   from `kwargs` before `get_status_page` -- mirroring `edit_monitor` exactly
   -- keeps the server's own data untouched and costs no round trip on escalation.
   `_build_status_page_data` stays a pure builder.

## Architecture

The implementation follows the same layered pattern as `edit_monitor`:

```
+---------------------------------------------------------+
|  Caller: save_status_page(slug, ..., rssTitle="Feed")   |
+---------------------------+-----------------------------+
                            |
                            v
+---------------------------------------------------------+
|  save_status_page(slug, **kwargs)                       |
|    1. Compute withheld from kwargs BEFORE the round trip|
|    2. Emit UnsupportedFieldWarning if any withheld      |
|    3. get_status_page(slug) -- only then                |
|    4. Merge only surviving kwargs onto server config    |
|    5. _build_status_page_data(**merged) -- pure builder |
|    6. _call('saveStatusPage', data)                     |
+---------------------------------------------------------+
```

The withhold-and-warn step lives in `save_status_page`, computed from `kwargs`
before `get_status_page`, exactly as `edit_monitor` does (api.py ~2085). This
ensures:
- A value the server itself returned is never treated as a caller request.
- An escalated warning costs no wasted HTTP fetch (warned before
  `get_status_page`, same as `edit_monitor`'s "Warned before get_monitor so an
  escalated warning costs no round trip").
- `_build_status_page_data` stays a pure builder with no warning logic.

For maintenance and settings, if the inventory confirms no v2-only fields exist,
no structural change is made -- the finding is documented and tested negatively
(i.e. a test asserts the payload at v1 and v2 is identical for those surfaces).

## Components and Interfaces

### Registry: `_V2_ONLY_STATUS_PAGE_FIELDS`

A module-level dict mapping field name to version floor string, following the
`_V2_ONLY_MONITOR_FIELDS` pattern but simplified -- no `types` column (status
pages have no type variants) and no `_RAISE` behavior (no status-page field
changes a verdict).

```python
# Status-page fields Uptime Kuma only accepts from the stated version onward.
# Follows the same rule as _V2_ONLY_MONITOR_FIELDS: a caller-supplied value
# below the floor is withheld and reported once per call as an
# UnsupportedFieldWarning. The analytics trio is NOT here -- the v2 server
# requires analyticsType present and rejects its absence.
#
# CANDIDATES -- pending confirmation by the Upstream Inventory and Verification
# Run. The final set may be smaller (if a 1.23.x server accepts one) or larger
# (if the inventory finds additional fields).
_V2_ONLY_STATUS_PAGE_FIELDS = {
    # populated after the inventory and verification phases complete
}
```

### Helper: `_withheld_status_page_fields`

```python
def _withheld_status_page_fields(self, supplied: dict) -> list:
    """Names the version-gated status-page fields this call cannot send.

    :param dict supplied: Keys are field names, values are the caller-
                          supplied values. A None value means the caller
                          did not supply it.
    :return: Field names withheld, in registry declaration order.
    """
    parsed = self._parsed_version()
    withheld = []
    for name, floor in _V2_ONLY_STATUS_PAGE_FIELDS.items():
        if supplied.get(name) is None:
            continue
        if parsed < parse_version(floor):
            withheld.append(name)
    return withheld
```

### Helper: `_warn_withheld_status_page_fields`

```python
def _warn_withheld_status_page_fields(self, withheld, stacklevel) -> None:
    """Reports withheld status-page fields once, as a single
    UnsupportedFieldWarning.

    Same contract as _warn_withheld_v2_fields but with status-page-
    specific wording.
    """
    if not withheld:
        return
    version = self.version
    detail = ", ".join(
        f"{name} (requires {_V2_ONLY_STATUS_PAGE_FIELDS[name]} or newer)"
        for name in withheld
    )
    plural = "s" if len(withheld) > 1 else ""
    warnings.warn(
        f"the server reports version {version}, which does not support "
        f"{len(withheld)} requested status-page field{plural}, so "
        f"{'they were' if plural else 'it was'} not sent: {detail}",
        UnsupportedFieldWarning,
        stacklevel=stacklevel,
    )
```

### Integration into `save_status_page`

The withhold-and-warn step is placed in `save_status_page` before the
`get_status_page` round trip, mirroring `edit_monitor`'s pattern:

```python
def save_status_page(self, slug: str, **kwargs) -> dict:
    # Decide from kwargs BEFORE the merge, then merge only what survives.
    #
    # Deleting keys after the merge would be the wrong shape: del data[key]
    # cannot tell a key the caller supplied from one get_status_page returned,
    # so a status page already carrying a v2-only column would lose it on any
    # unrelated save. Computing the withheld set from kwargs keeps the server's
    # own data untouched.
    withheld = self._withheld_status_page_fields(kwargs)
    # stacklevel 3: _warn helper, this method, the caller.
    # Warned before get_status_page so an escalated warning costs no round trip.
    self._warn_withheld_status_page_fields(withheld, stacklevel=3)

    status_page = self.get_status_page(slug)
    status_page.pop("incident")
    status_page.pop("incidents", None)
    status_page.pop("maintenanceList")
    status_page.pop("autoRefreshInterval", None)
    status_page.pop("googleAnalyticsId", None)
    status_page.update({k: v for k, v in kwargs.items() if k not in withheld})
    data = self._build_status_page_data(**status_page)
    r = self._call('saveStatusPage', data)
    ...
```

`_build_status_page_data` remains a pure builder. Its existing
`if showOnlyLastHeartbeat is not None` / `if rssTitle is not None` guards
already keep these keys off a v1 payload -- that behaviour stays. No warning
logic is added to the builder.

### Maintenance and Settings -- No-Op Path

If the Upstream Inventory and Verification confirm no v2-only fields:
- No registry is added.
- No code change in `_build_maintenance_data` or `set_settings`.
- The finding is recorded in the spec directory and in `docs/api.rst`.
- A brief unit test asserts that the payload is identical at v1 and v2 versions
  (regression guard against accidental future version gates).

If the inventory *does* find v2-only fields, the same registry + helper pattern
applies, with surface-specific naming (`_V2_ONLY_MAINTENANCE_FIELDS` and/or
`_V2_ONLY_SETTINGS_FIELDS`).

## Data Models

### Registry Entry (Status Pages)

Simplified from the monitor pattern -- no `types` column, no behavior column:

| Field | Type | Description |
|-------|------|-------------|
| key   | str  | Field name as it appears in the config dict and as a parameter |
| value | str  | Version floor (e.g. `"2.0"`) -- the lowest server version that implements it |

### Upstream Inventory Record

One section per Non_Monitor_Surface in `upstream-inventory.md`:

| Column | Content |
|--------|---------|
| Field  | Parameter name |
| Floor  | Uptime Kuma tag that introduced it |
| Source | Server-side file and function that consumes it |
| Category | Unconditional_V2_Field / Opt_In_V2_Field / V1_Only_Field / Not_V2_Only |

### Verification Run Record

One row per candidate field in `v1-verification-results.md`:

| Column | Content |
|--------|---------|
| Field  | Parameter name |
| Surface | status_page / maintenance / settings |
| Method | Write method used (e.g. `_call('saveStatusPage', ...)`) |
| Verdict | REJECTED / ACCEPTED / ABSENT / MISMATCH / NOT_OBSERVED / NOT_APPLICABLE |
| Detail | What was sent and what (if anything) was returned |

## Error Handling

### Warning Emission

- **One warning per call.** Matches the monitor-field rule. A `save_status_page`
  call that withholds multiple confirmed fields emits one
  `UnsupportedFieldWarning` naming all of them.
- **Escalation to exception.** If the caller has set
  `warnings.simplefilter("error", UnsupportedFieldWarning)`, the warning becomes
  a raised exception. The payload is not sent -- `save_status_page` raises before
  `get_status_page`, so neither the fetch nor `_call('saveStatusPage', ...)` is
  reached.
- **No warning when nothing is withheld.** A call supplying only fields the server
  supports (or supplying `None` for v2-only fields) produces no warning.

### Analytics Trio -- No Error Path

The analytics trio (`analyticsType`, `analyticsId`, `analyticsScriptUrl`) is
sent unconditionally on v2. On v1, it is not sent (the `else` branch handles
v1). Neither path warns, because neither path withholds a caller-supplied value.
The v1 caller never had these fields, and the v2 server requires them.

### Verification Script Errors

The verification script (`tests/live_test_status_page_v1.py`) follows the
`live_test_v2_only_fields_v1.py` pattern for the injection technique and the
`live_test_conditions_v1.py` pattern for the safety-guard shape:
- Refuses to run if `UPTIME_KUMA_V1_URL` is unset.
- Aborts if the server does not report `1.23`.
- Deletes created resources in a `finally` block.
- Exits non-zero on any verification failure.
- Output is ASCII only.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all
valid executions of a system -- essentially, a formal statement about what the
system should do. Properties serve as the bridge between human-readable
specifications and machine-verifiable correctness guarantees.*

### Property 1: Opt-in v2 fields are withheld below the version floor

*For any* field in `_V2_ONLY_STATUS_PAGE_FIELDS`, *for any* server version below
that field's version floor, and *for any* non-None value supplied by the caller
in `kwargs`, the returned config dict SHALL NOT contain that field.

**Validates: Requirements 3.1**

### Property 2: Analytics trio is never gated

*For any* server version string in the supported range (1.21.3 through the latest
2.x), the analytics fields (`analyticsType`, `analyticsId`,
`analyticsScriptUrl`) SHALL NOT appear in any withholding list, and on a v2
server SHALL always be present in the returned config dict -- including when their
values are None.

**Validates: Requirements 3.2**

### Property 3: No warning when no opt-in field is supplied

*For any* server version string and *for any* call to `save_status_page` where
`kwargs` contains no key in `_V2_ONLY_STATUS_PAGE_FIELDS` (or only `None`
values for them), zero `UnsupportedFieldWarning` warnings SHALL be emitted.

**Validates: Requirements 3.5**

### Property 4: Fields are included without warning at or above their floor

*For any* field in `_V2_ONLY_STATUS_PAGE_FIELDS`, *for any* server version at or
above that field's version floor, and *for any* non-None value supplied by the
caller, the returned config dict SHALL contain that field with the supplied value,
and zero `UnsupportedFieldWarning` warnings SHALL be emitted for that field.

**Validates: Requirements 3.6**

### Property 5: Server-returned values are never treated as caller requests

*For any* v1 server version and *for any* set of `kwargs` that does NOT include
a key in `_V2_ONLY_STATUS_PAGE_FIELDS`, if `get_status_page` returns a config
containing such a key, zero `UnsupportedFieldWarning` warnings SHALL be emitted
and the server-returned value SHALL survive into the payload unchanged.

**Validates: Requirements 3.11**

## Testing Strategy

### Property-Based Testing

**Idiom:** Seeded `PBT_SEED` / `PBT_CASES` as used in
`tests/test_monitor_params_v2.py` (`hypothesis` is not a project dependency).

Each correctness property above maps to one property-based test using a
deterministic random generator seeded for reproducibility:

```python
# Seeded so a failure is reproducible; hypothesis is not a project dependency.
STATUS_PAGE_PBT_SEED = 20260808
STATUS_PAGE_PBT_CASES = 48
```

**Generators (deterministic `random.Random`):**
- Version strings below 2.0: drawn from `["1.21.3", "1.22.0", "1.23.0", "1.23.1", "1.23.2"]`
- Version strings at/above 2.0: drawn from `["2.0.0", "2.1.0", "2.4.0", "3.0.0"]`
- Field values: `random.choice([True, False, "some-string"])` for non-None values
- Field selections: random subset of confirmed registry keys

### Unit Tests (what CI runs)

All tests live in `tests/test_status_page_v2.py`, extending the existing test
class. No new test file.

**Test construction patterns:**

Builder-level tests (existing pattern for analytics trio, payload-identity):
```python
self.api_v1 = MagicMock(spec=UptimeKumaApi)
self.api_v1.version = "1.23.2"
self.api_v1._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api_v1)
self.build_v1 = UptimeKumaApi._build_status_page_data.__get__(self.api_v1)
```

Save-level tests (withhold-and-warn lives in `save_status_page`):
```python
self.api_v1 = MagicMock(spec=UptimeKumaApi)
self.api_v1.version = "1.23.2"
self.api_v1._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api_v1)
self.api_v1._build_status_page_data = UptimeKumaApi._build_status_page_data.__get__(self.api_v1)
self.api_v1._withheld_status_page_fields = UptimeKumaApi._withheld_status_page_fields.__get__(self.api_v1)
self.api_v1._warn_withheld_status_page_fields = UptimeKumaApi._warn_withheld_status_page_fields.__get__(self.api_v1)
self.save_v1 = UptimeKumaApi.save_status_page.__get__(self.api_v1)
# Mock the round trip and server call:
self.api_v1.get_status_page = MagicMock(return_value={...})
self.api_v1._call = MagicMock(return_value={...})
```

Assertions on withheld fields check the payload passed to
`self.api_v1._call('saveStatusPage', ...)` rather than the builder's return
value, because that is the data that reaches the server.

**Cases to cover (for each confirmed Opt_In_V2_Field):**

| # | Assertion | Validates |
|---|-----------|-----------|
| 1 | v1 (1.23.2) + confirmed field supplied -> absent from payload passed to `_call`, one warning emitted | Req 3.1 |
| 1b | v2.0.2 (below 2.1 floor) + confirmed field supplied -> absent from payload, one warning. Distinguishes 2.1 floor from 2.0 | Req 3.1, 7.3 |
| 2 | v1 + multiple confirmed fields -> absent from payload passed to `_call`, exactly one warning | Req 3.1 |
| 3 | v2 + confirmed field supplied -> in config with correct value, no warning | Req 3.6 |
| 4 | v1 + no opt-in fields supplied (all None) -> no warning at any version | Req 3.5 |
| 5 | v1 + v2: analytics trio present in payload (v2) / absent (v1), no warning | Req 3.2, 7.6 |
| 6 | v1 + `simplefilter("error", ...)` + opt-in field -> raises, no payload | Req 3.8, 7.7 |
| 7 | Warning message contains field name, version floor, server version | Req 7.4 |
| 8 | Payload at v1 and v2 with no opt-in fields identical to 2.4.0 behavior | Req 8.1 |
| 9 | v1 + mocked get_status_page returns gated key + caller passes only title -> no warning, value survives | Req 3.11, 7.12 |

If maintenance/settings have no v2-only fields:
| 10 | `_build_maintenance_data` produces identical payload at v1 and v2 | Req 4.3, 8.2 |
| 11 | `set_settings` produces identical payload at v1 and v2 (excluding existing 1.23/1.23.1 gates) | Req 5.2, 8.3 |

**Proving a test can fail (Requirement 7.10):** Each gating test is verified to
fail against the pre-implementation behavior (where `save_status_page` does not
compute `_withheld_status_page_fields` or emit a warning). The new tests assert
the *warning* was emitted, which the pre-implementation code does not do.

### Verification Script (manual, not CI)

`tests/live_test_status_page_v1.py` -- built on the
`tests/live_test_v2_only_fields_v1.py` model for the injection technique and the
`live_test_conditions_v1.py` model for the safety-guard shape (URL-unset refusal,
`1.23` assertion, `finally` teardown).

**How a gated field is put on the wire** (from
`live_test_v2_only_fields_v1.py`):

The builder is the code doing the gating -- on a pre-2.0 server it drops the
opt-in fields before the payload is built. The probe bypasses it:

```python
slug, config, icon, groups = api._build_status_page_data(**base)  # v1-safe payload
config[field] = value   # inject exactly one candidate field
api._call('saveStatusPage', (slug, config, icon, groups))
```

One field per status page, deliberately: a rejected insert names a single column,
and one bad column fails the whole statement, so probing several at once attributes
one rejection to all of them.

**Verdicts** (one per field, matching `live_test_v2_only_fields_v1.py`):
- `REJECTED` -- the server returned an error for the payload carrying the field
- `ACCEPTED` -- the field came back on read-back holding the value sent
- `ABSENT` -- the payload succeeded and the field did not come back
- `MISMATCH` -- the field came back holding a value other than the one sent
- `NOT_OBSERVED` -- the field was never exercised (incomplete run must read as
  incomplete)

`ACCEPTED` is the interesting one: it means the field is mis-gated and should be
excluded from the registry.

**Safety guards:**
- Reads `UPTIME_KUMA_V1_URL`, `UPTIME_KUMA_V1_USERNAME`, `UPTIME_KUMA_V1_PASSWORD`
- Confirms server reports `1.23`
- Deletes created resources in `finally`
- ASCII-only output (`PASS` / `FAIL` / `->`)

**How it is run.** Through `scripts/run_disposable_kuma.ps1`, which already exists
for exactly this purpose::

    pwsh -File scripts/run_disposable_kuma.ps1 -Script tests/live_test_status_page_v1.py

That runner supplies three properties this spec depends on and a hand-rolled
`ssh` + `docker run` does not: it destroys the container in a `finally` including
on readiness timeout, it reads the Docker host and SSH user from the gitignored
root `.env`, and it replaces both with the `<docker-host>` / `<user>` placeholders
in every line it prints. The last one is what makes its transcript directly
paste-able into `v1-verification-results.md` as requirement 2.8 requires, rather
than something that has to be scrubbed by hand and might not be.

If maintenance or settings have candidate fields, the same script exercises
those surfaces against the same container (using the corresponding builder to
produce a v1-safe payload and injecting one candidate key).

### Documentation Testing

`docs/make.bat html` must build without Sphinx warnings after the normative
location is broadened.