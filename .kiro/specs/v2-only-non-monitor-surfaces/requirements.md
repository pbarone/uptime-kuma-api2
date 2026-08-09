# Requirements Document

## Introduction

Issue #14 established that v2-only monitor fields are governed by a single rule:
withhold and warn via `UnsupportedFieldWarning`. That rule is implemented by
`_V2_ONLY_MONITOR_FIELDS` and is scoped — deliberately and explicitly — to the
monitor fields accepted by `_build_monitor_data` and `edit_monitor`. Issue #33
asks: what governs the rest?

Three non-monitor surfaces carry version-gated behavior today:

- **Status pages** (`_build_status_page_data`): a `>= 2.0` block with three
  distinct sub-behaviors — the analytics trio sent unconditionally, two opt-in
  v2 fields, and a v1-only `else` branch.
- **Maintenance** (`_build_maintenance_data`): no version gate at all.
- **Settings** (`set_settings`): gates at `1.23` and `1.23.1` only, no `>= 2.0`
  gate.

This feature answers: which of these fields are genuinely v2-only, whether the
#14 rule extends to them, where it cannot (the analytics trio being the known
case), and how the library documents the result. The work is sequenced as
inventory, verification, then implementation — exactly as it was for monitor
fields.

**The outcome may be "nothing to gate."** Maintenance and settings may have no
v2-only surface at all once the inventory is complete. That is a valid conclusion
and a requirement of this feature is that it records a negative finding rather
than inventing work. The work this feature guarantees is the inventory, the
verification, the decision, and the documentation — not the addition of gates.

## Glossary

- **Library**: The `uptime_kuma_api` package, specifically `UptimeKumaApi` in
  `uptime_kuma_api/api.py`.
- **Server_Version**: The version string the connected Uptime Kuma server
  reports via the `info` event, exposed as the `version` property and parsed for
  comparison by `_parsed_version()`.
- **Version_Floor**: The lowest Server_Version at which a given field is
  implemented by the server.
- **V2_Only_Field**: A field the Library sends only when `_parsed_version()` is
  at or above that field's Version_Floor of `2.0`.
- **Non_Monitor_Surface**: The status-page, maintenance and settings write paths
  — `_build_status_page_data` / `save_status_page`, `_build_maintenance_data` /
  `add_maintenance` / `edit_maintenance`, and `set_settings` — as distinct from
  the monitor write paths governed by `_V2_ONLY_MONITOR_FIELDS`.
- **Withheld**: A V2_Only_Field that the caller supplied a value for and that
  the Library left out of the payload because the Server_Version is below that
  field's Version_Floor.
- **Monitor_Rule**: The rule established by issue #14 and shipped in
  `_V2_ONLY_MONITOR_FIELDS`: withhold plus one `UnsupportedFieldWarning` per
  call.
- **Analytics_Trio**: The three status-page fields `analyticsType`,
  `analyticsId`, `analyticsScriptUrl`, which the v2 server validates and rejects
  when `analyticsType` is absent from the payload. Withholding is not an
  available outcome for these fields.
- **Unconditional_V2_Field**: A V2_Only_Field that the Library must send to the
  v2 server even when the caller supplied `None`, because the server rejects its
  absence. The Analytics_Trio are the known members.
- **Opt_In_V2_Field**: A V2_Only_Field that the Library sends only when the
  caller supplies a non-`None` value, because the server treats absence as "no
  change" or "use default". `showOnlyLastHeartbeat` and `rssTitle` in status
  pages are the known members.
- **V1_Only_Field**: A field the Library sends only when `_parsed_version()` is
  below `2.0`. `googleAnalyticsId` and `password` in status pages are the known
  members.
- **Verification_Run**: A manual run against a disposable Uptime Kuma 1.23.x
  container that records, per candidate field, whether the server accepts or
  rejects a payload carrying that field.
- **Upstream_Inventory**: The examination of Uptime Kuma server source and tags
  (`louislam/uptime-kuma`) that identifies which fields each surface gained at
  or after 2.0.

## Requirements

### Requirement 1: Inventory the v2-only surface from upstream source

**User Story:** As the maintainer, I want to know exactly which fields each
non-monitor surface gained at or after Uptime Kuma 2.0, so that I have a
complete candidate list before verification.

#### Acceptance Criteria

1. THE Upstream_Inventory SHALL identify, for each Non_Monitor_Surface, every
   field parameter accepted by the Library's write path today that the Uptime
   Kuma server first implemented at or after version 2.0, by examining the
   server's own source and tags (`louislam/uptime-kuma`).
2. THE Upstream_Inventory SHALL identify, for each Non_Monitor_Surface, every
   field parameter the Uptime Kuma 2.x server accepts that the Library does not
   yet expose as a parameter, because an unexposed v2-only field is a future
   candidate rather than an absence.
3. THE Upstream_Inventory SHALL record, for each candidate field, the Uptime Kuma
   tag (release) that introduced it and the server-side source file and function
   that consumes it.
4. THE Upstream_Inventory SHALL record its findings in a file in this spec's
   directory, one section per Non_Monitor_Surface, stating the examination date
   and the upstream commit or tag examined.
5. THE Upstream_Inventory SHALL distinguish, for each status-page candidate,
   whether the field is Unconditional_V2_Field, Opt_In_V2_Field, or
   V1_Only_Field, because those three categories receive different treatment.
6. IF the Upstream_Inventory finds no v2-only fields for a given
   Non_Monitor_Surface, THEN THE Upstream_Inventory SHALL record that finding
   explicitly rather than omitting the surface, so that a negative result is
   visible as a conclusion rather than an oversight.
7. THE Upstream_Inventory SHALL use `gh` and `git` against
   `louislam/uptime-kuma` to answer questions about when a field appeared, and
   SHALL NOT use web search for that purpose.

### Requirement 2: Verify each candidate against a real v1.23.x server

**User Story:** As the maintainer, I want to know whether a pre-2.0 server
actually rejects each candidate field, so that a field the server accepts is
corrected out of the gate list rather than being withheld from callers who could
have sent it.

#### Acceptance Criteria

1. THE Verification_Run SHALL record, for each candidate field identified by the
   Upstream_Inventory, the Non_Monitor_Surface it belongs to, the write method
   used to exercise it, and exactly one verdict from the set `REJECTED` (the
   server returned an error for the payload carrying the field), `ACCEPTED` (the
   field came back on read-back holding the value sent), `ABSENT` (the payload
   succeeded and the field did not come back) and `MISMATCH` (the field came back
   holding a value other than the one sent).
2. WHEN the Verification_Run records `ACCEPTED` for a candidate field, THE
   candidate field SHALL be excluded from any gating, because a field a 1.23.x
   server accepts and returns unchanged is not a V2_Only_Field.
3. THE Verification_Run SHALL target a disposable Uptime Kuma 1.23.x container
   addressed through its own `UPTIME_KUMA_V1_URL`, `UPTIME_KUMA_V1_USERNAME` and
   `UPTIME_KUMA_V1_PASSWORD` keys rather than the 2.x `tests/.env` keys, and
   SHALL confirm the reported Server_Version begins with `1.23` before sending
   any payload.
4. IF the target URL is unset, or the reported Server_Version does not begin with
   `1.23`, THEN THE Verification_Run SHALL send no payload, SHALL create no
   resource, and SHALL exit reporting `FAIL` with an indication that the target
   was not confirmed to be a 1.23.x server.
5. WHEN the server has accepted a payload carrying a candidate field, THE
   Verification_Run SHALL read that resource back and SHALL compare every field
   sent against the value returned, reporting `PASS` for a field whose returned
   value equals the value sent and `FAIL` for a field that is `ABSENT` or
   `MISMATCH`.
6. WHEN the Verification_Run has created a resource (status page, maintenance
   window), THE Verification_Run SHALL delete that resource before exiting,
   including on the path where sending or reading back a field raised an
   exception.
7. WHERE the Verification_Run is scripted, THE script output SHALL contain only
   characters in the ASCII range (code points 0 to 127), using `PASS`, `FAIL`
   and `->` as its status markers.
8. THE Verification_Run SHALL record its per-field results in a file in this
   spec's directory stating the observed Server_Version, the date of the run, and
   one verdict per field, and SHALL refer to the container host, the SSH user and
   any credential only through the `<docker-host>` and `<user>` placeholders.
9. IF a candidate field was not exercised during the Verification_Run, THEN THE
   recorded results SHALL name that field with a `NOT_OBSERVED` verdict rather than
   omitting it, so that an incomplete run is visible as incomplete.
10. THE Verification_Run SHALL complete and its per-field results SHALL be
    recorded before any implementation change is opened for review, so that the
    implementation is built from the corrected field list.

### Requirement 3: Status pages — extend the rule where possible, document where not

**User Story:** As a caller managing status pages across a mixed fleet, I want
the same predictability the Monitor_Rule gives me for monitor fields — where
that is achievable — and clear documentation where it is not.

#### Acceptance Criteria

1. WHEN a caller supplies a non-`None` value for an Opt_In_V2_Field of a status
   page (currently `showOnlyLastHeartbeat`, `rssTitle`) and the Server_Version is
   below `2.0`, THE Library SHALL withhold that field from the payload and emit
   one `UnsupportedFieldWarning` for the call, carrying the field name, the
   field's Version_Floor and the observed Server_Version, following the same
   Signal contract as the Monitor_Rule.
2. THE Library SHALL NOT withhold, warn about, or gate the Analytics_Trio at any
   Server_Version, because the v2 server rejects absent `analyticsType` and the
   Library already sends them unconditionally including when `None`.
3. THE Library documentation SHALL name the Analytics_Trio as an exception to the
   withhold-and-warn rule, SHALL state the reason — the v2 server requires
   `analyticsType` to be present and rejects its absence — and SHALL state this
   exception in the same normative location where the Monitor_Rule is documented
   (the "Version-gated monitor fields" section of `docs/api.rst` or a broadened
   equivalent).
4. THE Library SHALL leave the V1_Only_Fields (`googleAnalyticsId`, `password`)
   behaving as version 2.4.0 behaves — sent on a pre-2.0 server, omitted on a
   2.x server — and SHALL NOT emit a warning for them, because they are the
   reverse direction (v1-only, not v2-only).
5. WHEN a caller supplies no Opt_In_V2_Field for a status page, THE Library SHALL
   emit no `UnsupportedFieldWarning` for that call, at any Server_Version.
6. WHEN the Server_Version is at or above the Version_Floor of an
   Opt_In_V2_Field the caller supplied, THE Library SHALL place that field in the
   payload under the same key holding the same value as version 2.4.0, SHALL emit
   no warning and SHALL raise no exception.
7. THE Library SHALL produce, for every status-page field absent from any new
   gate and at every Server_Version in the supported range, the same payload and
   the same return value as version 2.4.0 produces.
8. WHERE a caller has configured `warnings.simplefilter("error",
   UnsupportedFieldWarning)` and a status-page call would withhold an
   Opt_In_V2_Field, THE Library SHALL let the resulting exception reach the
   caller and SHALL send no payload for that call.
9. IF the Verification_Run shows that one or more Opt_In_V2_Fields are
   `ACCEPTED` by a 1.23.x server, THEN THE Library SHALL NOT gate those fields
   and SHALL record the finding, reducing the gated set accordingly.
10. THE Library SHALL hold any confirmed status-page V2_Only_Fields in a registry
    following the same structural pattern as `_V2_ONLY_MONITOR_FIELDS` (field
    name to Version_Floor), private and unexported, so that adding a field is a
    data change.
11. THE Library SHALL compute the withheld set from the caller's own `kwargs`
    before the `get_status_page` round trip, and SHALL merge only what survives
    into the server-returned config, so that a value the server itself returned
    is never treated as a caller request. Editing an unrelated field on a status
    page that already carries a v2-only column SHALL NOT warn about or drop that
    column. (Mirrors the contract `edit_monitor` states and the nine-line comment
    at api.py ~2085 documents.)

### Requirement 4: Maintenance — record the finding

**User Story:** As the maintainer, I want the maintenance surface inventoried
and verified so that "no version gate" is a documented conclusion rather than an
unstated assumption.

#### Acceptance Criteria

1. THE Upstream_Inventory SHALL examine the maintenance write path in the Uptime
   Kuma server source and record whether any parameter `_build_maintenance_data`
   accepts today was introduced at or after 2.0.
2. THE Upstream_Inventory SHALL examine the Uptime Kuma 2.x server source and
   record whether the server accepts any maintenance field the Library does not
   yet expose, introduced at or after 2.0.
3. IF the Upstream_Inventory finds no v2-only maintenance fields, THEN THE
   Library SHALL NOT add any version gate to `_build_maintenance_data`, and THE
   recorded results SHALL state the conclusion: "maintenance has no v2-only
   surface" with the evidence.
4. IF the Upstream_Inventory and Verification_Run identify one or more confirmed
   v2-only maintenance fields, THEN THE Library SHALL apply the withhold-and-warn
   rule to those fields following the same contract as requirement 3 defines for
   status-page Opt_In_V2_Fields, including the `UnsupportedFieldWarning`,
   registry pattern, and documentation.
5. THE Library SHALL leave every existing maintenance parameter behaving as
   version 2.4.0 behaves at every Server_Version in the supported range, unless
   the Verification_Run confirms a field the v1 server rejects.

### Requirement 5: Settings — record the finding

**User Story:** As the maintainer, I want the settings surface inventoried and
verified so that its two existing gates (`1.23`, `1.23.1`) are the complete set
and any 2.0+ surface is known.

#### Acceptance Criteria

1. THE Upstream_Inventory SHALL examine the settings write path in the Uptime
   Kuma server source and record whether any parameter `set_settings` accepts
   today was introduced at or after 2.0, and whether the server accepts any
   settings field the Library does not yet expose, introduced at or after 2.0.
2. IF the Upstream_Inventory finds no v2-only settings fields, THEN THE Library
   SHALL NOT add any version gate beyond the existing `1.23` and `1.23.1` gates
   to `set_settings`, and THE recorded results SHALL state the conclusion with
   evidence.
3. IF the Upstream_Inventory and Verification_Run identify one or more confirmed
   v2-only settings fields, THEN THE Library SHALL apply the withhold-and-warn
   rule to those fields following the same contract as requirement 3 defines for
   status-page Opt_In_V2_Fields.
4. THE Library SHALL leave `set_settings` behaving as version 2.4.0 behaves at
   every Server_Version in the supported range for every field absent from any
   new gate.
5. THE Library SHALL leave the existing `1.23` gate on `chromeExecutable` and the
   `1.23.1` gate on `nscd` unchanged, because they are pre-2.0 gates and are
   outside this feature's class boundary.

### Requirement 6: Documentation — one rule stated once, exceptions named

**User Story:** As a contributor, I want one place that says what happens to a
v2-only field on any write path, so that the answer is not "read the monitor
spec and then read three more."

#### Acceptance Criteria

1. THE Library documentation SHALL broaden the title and scope of the normative
   location established by the Monitor_Rule (currently "Version-gated monitor
   fields" in `docs/api.rst`) to cover all Non_Monitor_Surfaces where the rule
   was extended, without creating a second normative location.
2. THE broadened normative location SHALL state, for each Non_Monitor_Surface
   where the rule applies, the registry that holds its gated fields, the write
   methods it governs, and every named exception.
3. THE broadened normative location SHALL name the Analytics_Trio as an exception
   with the reason: the v2 server requires `analyticsType` present and rejects
   its absence.
4. IF the Upstream_Inventory concludes that maintenance or settings has no
   v2-only surface, THEN THE broadened normative location SHALL state that
   conclusion briefly, so that a reader does not wonder whether it was forgotten.
5. THE `save_status_page` docstring SHALL carry a cross-reference to the
   normative location where the rule is stated, and SHALL NOT restate the rule as
   a second normative statement.
6. THE `CHANGELOG.md` entry for this change SHALL appear under the release
   heading of the version that ships it, SHALL state which Non_Monitor_Surfaces
   gained the withhold-and-warn rule, SHALL name every field whose behavior
   changes, and SHALL state that the Analytics_Trio is not gated and why.
7. WHEN the documentation is built with `make html` (`docs/make.bat html` on
   Windows), THE build SHALL render the broadened normative location and resolve
   every cross-reference without emitting a Sphinx warning.
8. THE normative location, the docstring cross-references and the CHANGELOG entry
   SHALL ship in the same merged change, following the precedent set by the
   Monitor_Rule spec (requirement 3.7 of `v2-only-fields-rule`).

### Requirement 7: The change is covered by the unit suite

**User Story:** As a maintainer, I want the extended rule pinned by tests, so
that a later contributor who alters it fails a test rather than silently
reverting it.

#### Acceptance Criteria

1. THE Library tests for status-page gating SHALL live in
   `tests/test_status_page_v2.py`, alongside the existing status-page version
   tests.
2. THE Library tests SHALL add no new test file — they extend a file already
   collected by a bare `pytest`.
3. THE Library tests SHALL assert, for each confirmed Opt_In_V2_Field of status
   pages, that withholding occurs at mocked Server_Versions of `1.23.2` and
   `2.0.2` (both below the `2.1` floor), and that inclusion occurs at a mocked
   Server_Version of `2.4.0`. The `2.0.2` case is the only test that
   distinguishes a `2.1` floor from a `2.0` floor.
4. WHEN the Library withholds an Opt_In_V2_Field on a status-page call, THE
   Library tests SHALL assert that exactly one `UnsupportedFieldWarning` is
   emitted and that the warning names the withheld field, the field's
   Version_Floor and the observed Server_Version.
5. THE Library tests SHALL assert that a status-page call supplying no
   Opt_In_V2_Field emits no warning at any mocked Server_Version.
6. THE Library tests SHALL assert that the Analytics_Trio is present in the
   payload at both `1.23.2` and `2.4.0` mocked Server_Versions, and that no
   warning is emitted for them.
7. WHERE a caller has configured `warnings.simplefilter("error",
   UnsupportedFieldWarning)`, THE Library tests SHALL assert that a status-page
   call which would withhold an Opt_In_V2_Field raises instead and sends no
   payload.
8. IF confirmed v2-only fields are found for maintenance or settings, THEN THE
   Library tests SHALL assert the same withhold-and-warn contract for those
   surfaces, in the existing test file for that surface or in
   `test_status_page_v2.py` if no dedicated file exists.
9. THE Library tests SHALL reach no live server and open no network connection.
   Builder-level tests (analytics trio, payload identity) SHALL construct a
   `MagicMock(spec=UptimeKumaApi)` with `version` set to the mocked
   Server_Version and the real `UptimeKumaApi._parsed_version` and
   `UptimeKumaApi._build_status_page_data` bound onto it, following the
   existing classes in the file. Save-level tests (withhold, escalation,
   server-returned-value contract) SHALL additionally bind
   `save_status_page`, `_withheld_status_page_fields` and
   `_warn_withheld_status_page_fields` onto the mock, and SHALL mock
   `get_status_page` and `_call` so assertions can inspect the payload
   passed to `_call('saveStatusPage', ...)`.
10. THE Library tests SHALL fail when run against version 2.4.0 behavior for any
    newly-gated field, so that a test which has only ever passed does not stand
    as evidence for the rule.
11. THE Library tests SHALL produce output containing only ASCII characters.
12. THE Library tests SHALL assert that when `save_status_page` is called with
    only non-v2-only kwargs and a mocked `get_status_page` returns a config
    containing a confirmed Opt_In_V2_Field, THE Library emits no
    `UnsupportedFieldWarning` and the server-returned value survives into the
    payload unchanged. (Pins the requirement 3.11 contract.)

### Requirement 8: Backward compatibility is preserved

**User Story:** As a caller on Uptime Kuma 1.x, I want the Library to keep
managing status pages, maintenance and settings exactly as it does today for
every call that succeeds today, so that this rule does not cost me working
functionality.

#### Acceptance Criteria

1. WHEN a caller calls `save_status_page` supplying no Opt_In_V2_Field, THE
   Library SHALL build the same payload, issue the same server calls, and return
   the same value as version 2.4.0, at each of the mocked Server_Versions
   `1.23.2` and `2.4.0`.
2. WHEN a caller calls `add_maintenance` or `edit_maintenance` supplying the same
   arguments as today, THE Library SHALL build the same payload and return the
   same value as version 2.4.0, at every Server_Version in the supported range.
3. WHEN a caller calls `set_settings` with the same arguments as today, THE
   Library SHALL build the same payload and return the same value as version
   2.4.0, at every Server_Version in the supported range.
4. THE Library SHALL add no new public name to `uptime_kuma_api/__init__.py`
   beyond what is needed — if the existing `UnsupportedFieldWarning` serves all
   surfaces, no additional warning class is added.
5. THE Library SHALL leave `add_status_page` unchanged, because it takes only
   `slug` and `title` and has no v2-only fields.
6. THE Library SHALL leave `get_status_page`, `get_status_pages` and
   `delete_status_page` unchanged, because this feature changes only the fields
   sent on write paths.
7. THE Library SHALL leave `get_maintenance`, `get_maintenances` and
   `delete_maintenance` unchanged for the same reason.
8. THE Library SHALL leave `get_settings` unchanged.
9. THE `CHANGELOG.md` entry SHALL state that the change is non-breaking —
   every call that succeeds today still succeeds and returns the same value,
   with a warning added for any newly-gated field — and SHALL accompany a
   minor version bump recorded as a `feat` if any field gains a gate, or a
   patch version bump with a `docs` commit type if the outcome is
   documentation-only.

### Requirement 9: Sequencing — inventory before verification before implementation

**User Story:** As the maintainer, I want the phases ordered so that each
consumes the output of the one before it, and a negative finding in an early
phase can halt unnecessary later work.

#### Acceptance Criteria

1. THE Upstream_Inventory SHALL complete and its findings SHALL be recorded before
   the Verification_Run is executed.
2. THE Verification_Run SHALL complete and its per-field results SHALL be recorded
   before any implementation change is opened for review.
3. IF the Upstream_Inventory finds no v2-only fields for a given
   Non_Monitor_Surface, THEN THE Verification_Run SHALL skip that surface and
   record `NOT_APPLICABLE` rather than testing fields that do not exist.
4. IF the Verification_Run confirms that every candidate field for a given
   Non_Monitor_Surface is `ACCEPTED` by the v1 server, THEN THE implementation
   SHALL NOT gate any field on that surface and SHALL document the finding.
5. THE Version_Comparison_Fix (issue #30) has landed (closed 2026-08-07).
   This feature inherits its semantics for pre-release and unparseable
   server versions through `_parsed_version()` and adds no second
   comparison route.

## Cross-Spec References

- **v2-only-fields-rule (issue #14):** This feature extends the rule that spec
  ratified. Requirements here reference the Monitor_Rule's contracts — the
  Warning_Category (`UnsupportedFieldWarning`), the withhold-and-warn Signal, the
  `simplefilter("error", ...)` escalation, and the registry pattern — as
  established rather than restated.
- **uptime-kuma-v2-support-backlog, requirement 13.3:** That requirement forbids
  warnings on the silent-omit path. The Monitor_Rule already narrowed it
  (documented in v2-only-fields-rule requirement 3.4). This feature may further
  narrow it if status-page Opt_In_V2_Fields gain warnings. If so, the same
  annotation pattern applies.
- **Issue #30 (Version_Comparison_Fix):** Closed 2026-08-07. This feature
  inherits its semantics through `_parsed_version()` and adds no second
  comparison route.
- **Issue #28 (per-monitor-type floors):** Out of scope. Monitor types and
  monitor fields are not Non_Monitor_Surfaces.

## Out of Scope

- **Monitor fields.** Governed by `_V2_ONLY_MONITOR_FIELDS` (issue #14, shipped).
- **Monitor types.** Governed by `_V2_ONLY_MONITOR_TYPES` (issue #28).
- **New parameters.** If the Upstream_Inventory finds v2 server fields the
  Library does not yet expose, this feature records them as candidates for future
  work. It does not add them as parameters — that is a separate `feat`.
- **The analytics trio's server-side constraint.** This feature documents that
  withholding is not available; it does not attempt to make it available.
- **Verification script for the analytics trio on v1.** The existing
  `test_status_page_v2.py` already asserts that `analyticsType` is omitted from
  the v1 payload. The v1 server never sees these keys, so there is nothing to
  verify against it.
