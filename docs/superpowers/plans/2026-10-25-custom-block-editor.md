# Custom Block Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Edit three custom blocks locally and apply their complete schedule to
H7102 and H7126, selecting Custom and confirming the result from device replies.

**Architecture:** A separate schedule module owns decoded values, draft edits,
validation, preservation, and versioned persistence. The existing coordinator
owns all BLE reads and writes. Standard entity platforms expose draft values
and an Apply button without introducing independent radio owners.

**Tech Stack:** Python 3.14, Home Assistant entities and Store, pinned
govee-ble-client==1.0.0b4, pytest/pytest-asyncio, Ruff.

**Spec:** `docs/superpowers/specs/2026-10-25-custom-block-editor-design.md`

## Global Constraints

- Exactly three blocks; H7102 speed 1–8, duration, oscillation; H7126
  Sleep/Low/High and duration.
- Finite durations 1–65534 whole minutes; block 3 is always continuous and has no duration control or Continuous switch.
- Editing performs no BLE writes. Apply saves the schedule and selects Custom.
- Preserve H7102 marker, reserved bits, and untouched bytes; reject H7126
  settings its public writer cannot preserve.
- Preserve cancellation, bounded notification collection, serialized transactions,
  session cleanup, existing pacing, and no automatic replay of uncertain writes.
- No dependency changes, deployment, release, or physical hardware operations.
- Keep pre-existing untracked files untouched. Commit only this feature's files
  if repository permissions permit; never include unrelated logs or plans.

## Review Focus

- A user edits during Apply: confirmation clears only unchanged submitted edits.
- Govee app changes an unedited block: fresh preflight merge preserves that change.
- Stored draft is malformed or belongs to another model: validate before exposing
  or submitting it, without breaking device setup.
- Schedule replies are missing while power readings succeed: existing entities
  retain normal availability and schedule values are not invented.
- Write succeeds but readback disagrees or the link drops: retain draft and report
  failure, without retrying the control.

## Files and responsibilities

- Create `custom_components/govee_ble_devices/custom_schedule.py`: field
  validation, raw schedule merge, draft snapshots/revisions, Store lifecycle.
- Modify `coordinator.py`: optional schedule observations, draft ownership,
  Apply transaction and bounded verification.
- Create `number.py` and `select.py`: local block number/select controls.
- Modify `switch.py`, `button.py`, `entity.py`: local schedule switch/button
  availability and identity while preserving existing entities.
- Modify `__init__.py`: storage initialization/removal, new platform lifecycle.
- Modify `strings.json`, `translations/en.json`, `README.md`: user-facing names
  and behavior, including restart persistence and hardware limitations.
- Create `tests/test_custom_schedule.py`, `tests/test_custom_entities.py`, and
  `tests/test_custom_apply.py`; extend existing fixture/setup tests as needed.

### Task 1: Schedule values, drafts, and persistence

**Interfaces:**
- `CustomScheduleEditor(hass: HomeAssistant, entry_id: str, model: str)`.
- `async_load() -> None`, `async_remove() -> None` manage Store version 1,
  key `govee_ble_devices.custom_schedule.<entry_id>`.
- `observe(settings: bytes) -> None` accepts the latest 16-byte saved settings.
- `value(block: int, field: str) -> int | str | bool | None` uses zero-based
  block indices and fields `speed`, `mode`, `duration` (blocks 1 and 2 only), `oscillation`.
- `async_edit(block: int, field: str, value: object) -> None` validates and
  persists a local edit without radio access.
- `snapshot() -> DraftSnapshot` captures immutable edited values and revisions.
- `merge(settings: bytes, snapshot: DraftSnapshot) -> bytes` validates and
  builds final settings while preserving untouched bytes.
- `async_confirm(snapshot: DraftSnapshot, settings: bytes) -> None` records
  confirmed settings and removes only edits still matching submitted revisions.
- `h7126_segments(settings: bytes) -> tuple` converts validated settings for
  the public H7126 writer.

- [x] Write failing tests for valid boundaries and rejection of bool-as-int,
  noninteger duration, speed 0/9, duration 0/65535, invalid mode, and continuous
  on blocks 1/2. Assert H7102 unrelated raw bytes and reserved bits survive merge;
  H7126 nonzero marker and unrepresentable untouched fields are rejected.
- [x] Add tests asserting drafts survive Store reload and remain isolated by
  entry; invalid persisted fields are ignored; an edit made after snapshot remains
  pending after confirmation. Add the fresh external-change merge regression.
- [x] Run `.venv-ha/bin/python -m pytest -q tests/test_custom_schedule.py` and
  confirm failure is due to the missing implementation.
- [x] Implement the interfaces. Store only validated local edits; serialize persistence so an older save cannot replace newer
  edits. Never invent unknown block values; always encode Continuous for block 3.
- [x] Run the same tests and confirm all pass. Review the diff and commit this
  task's files when permitted.

### Task 2: Schedule observations and Apply transaction

**Interfaces:**
- Coordinator `custom_editor: CustomScheduleEditor | None`, absent for H5179.
- `async_apply_custom() -> None` snapshots at request time, serializes as a
  control barrier, preflights, submits once, confirms, and publishes observations.
- `Readings["custom_settings"]` contains observed bytes, timestamped through
  existing observation machinery; editor observes valid updates when published.

- [x] Write failing tests using existing real-client radio doubles for both
  models: optional refresh schedule replies, missing/malformed schedules without
  losing valid ordinary readings, applying from each non-Custom mode, and
  power-on before schedule submission when initially off.
- [x] Add tests asserting no control is sent after missing preflight power or
  schedule, all resulting settings are validated before power-on, and only one
  schedule write occurs. Assert readback must confirm Custom and the submitted
  schedule. Test mismatch, timeout, disconnect, notification overflow, unload,
  cancellation, and pending edits retained on uncertainty.
- [x] Add tests for no pending edits activating the saved schedule, new edits
  during Apply surviving success, and Apply preventing speed coalescing across
  its transaction. Verify existing fan Custom preset uses saved settings without
  consuming draft edits.
- [x] Run `.venv-ha/bin/python -m pytest -q tests/test_custom_apply.py` and
  confirm failures identify missing behavior.
- [x] Implement optional schedule queries within bounded refresh collection.
  Share one notification owner per session. Do not fail ordinary readings for
  schedule-only timeout/malformed replies; keep transport/overflow failures fatal.
- [x] Implement Apply under `_transaction(control=True)` and `_session()`.
  Preflight power and schedule in the same session, merge the snapshot, power on
  only if needed, use H7102 `set_custom_settings` or H7126 `set_custom_schedule`,
  preserve pacing/settle, then issue bounded fresh schedule/mode queries.
  Drain prewrite replies before verification so stale responses cannot confirm
  success. Publish received timestamps rather than fabricating freshness.
- [x] Convert transport/validation/mismatch failures to actionable HA errors,
  retain drafts, and schedule observations without replay. Preserve cancellation
  and shutdown cleanup; distinguish persistence failure from unconfirmed radio
  state in error reporting.
- [x] Run `.venv-ha/bin/python -m pytest -q tests/test_custom_apply.py tests/test_coordinator.py`
  and confirm all pass. Review and commit the task's files when permitted.

### Task 3: Entities and setup lifecycle

**Interfaces:**
- New block entities call `custom_editor.value` and `async_edit` only; local
  edits notify coordinator listeners without manufacturing observed BLE fields.
- Apply button calls coordinator `async_apply_custom()`.
- Local entity availability depends on an initialized value, not BLE freshness.
  Apply availability requires a valid prior schedule and reachable device; actual
  preflight remains authoritative.

- [x] Write failing tests asserting H7102 gets five number entities and three
  schedule oscillation switches; H7126 gets two numbers and three mode selects.
  Neither model gets a final-block duration or Continuous switch. Each receives one Apply button. H5179 receives none.
- [x] Assert stable device/block/field identities, translated names, minute
  units, bounds/steps, valid mode choices, and unavailable unknown fields.
  Block 3 always runs continuously and exposes no duration/Continuous control. Assert edits invoke no radio methods.
- [x] Add setup/unload/removal tests: load editor Store before forwarding
  platforms, initialize editor from first observations, persist across reload,
  remove Store only on entry removal, and preserve existing filter reset/display
  behavior. Give fixture config entries stable `entry_id` values.
- [x] Run `.venv-ha/bin/python -m pytest -q tests/test_custom_entities.py tests/test_setup.py`
  and confirm missing-feature failures.
- [x] Implement number/select platforms and extend switch/button setup, with
  focused shared local-entity availability support. Register both new platforms.
  Initialize storage before first refresh, observe schedules before notifying
  listeners, and remove persisted drafts on config entry deletion.
- [x] Add English strings in both translation files. Run the same tests plus
  existing entity/config-flow regressions and confirm pass. Review and commit
  the task's files when permitted.

### Task 4: Documentation and complete verification

- [x] Update README capability matrix and custom-mode instructions: local
  staging, per-model fields, Apply powering on/selecting Custom, draft persistence,
  duration/Continuous behavior, existing preset semantics, and uncertainty after
  interrupted writes. Correct the existing installation note from b3 to the
  manifest's already-pinned b4 while touching relevant client documentation.
- [x] Run `.venv-ha/bin/python scripts/check_release.py` and independently
  confirm installed client metadata reports `1.0.0b4` without reinstalling it.
- [x] Run `.venv-ha/bin/python -m pytest -q`,
  `.venv-ha/bin/python -m compileall -q custom_components scripts tests`,
  `.venv-ha/bin/ruff check custom_components scripts tests`, and
  `.venv-ha/bin/ruff format --check custom_components scripts tests`.
  Resolve failures and rerun affected checks, then run `git diff --check`.
- [x] Obtain a fresh whole-change code review, resolve material findings, and
  rerun checks affected by fixes. Record offline evidence and physical model
  round-trip testing still needed; do not claim hardware verification.
- [x] Mark completed plan steps and deliver changed files and verification
  results. No push, release, or deployment is included.

## Execution outcome

Implemented directly in the current workspace on `feature/custom-block-editor`.
Changes were initially left uncommitted to preserve existing staging choices.
The user subsequently authorized committing the completed feature and documents.
The independent review found two material issues; regressions reproduced both,
then fixes required explicit H7126 saved-schedule replies and made malformed
storage envelopes fall back to an empty draft. Additional tests cover repeated
Apply snapshots, speed transaction barriers, malformed optional replies, and
postwrite readback timeout.

Final validation: 154 tests passed (five existing upstream deprecation warnings),
release validator passed against installed client 1.0.0b4, compileall passed,
Ruff lint and format checks passed, and both staged/unstaged whitespace checks
passed. Physical BLE tests, hassfest, and HACS CI validation were not run.

Minor limitation retained: HA Store logs and suppresses typical storage write
errors, so not every persistence failure becomes an action error. The explicit
OSError branch still handles failures HA lets propagate. No extra storage
framework was added.

## Follow-up implementation

User requested comparison with other official/custom integrations and clarified
that block 3 is always continuous. The design's comparison records primary-source
examples. Removed final duration and Continuous switch, simplified drafts, ignored
obsolete stored fields, and removed obsolete registry entities on successful setup.
Regression tests cover fixed final duration, preservation of other draft fields,
updated entity sets, and scoped registry cleanup. Existing Apply semantics remain.

Follow-up verification: 161 tests passed, five existing upstream warnings.
Independent review found no material issues; release validator, compileall, Ruff
lint/format, and staged/unstaged whitespace checks passed. No physical BLE tests.
