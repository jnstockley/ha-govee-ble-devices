# Per-device Polling Options Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Allow independent polling intervals through each device's integration options.

**Architecture:** Use `OptionsFlowWithReload` to save per-entry options and reload
only the affected entry. Share interval validation/defaulting between the options
form and coordinator. Keep the existing brief BLE transaction model.

**Tech Stack:** Python 3.14, Home Assistant, pytest, Voluptuous, Ruff.

**Spec:** `docs/superpowers/specs/2026-10-25-polling-options-design.md`

## Global Constraints

- Whole seconds, 5–3600 inclusive, default 90; no additional entity.
- Field freshness: max(300 seconds, twice the selected interval).
- Preserve unrelated options, cleanup, control priority, and command verification.
- No notification implementation, dependency change, or release bump.

## Review Focus

- Existing entries without options retain 90-second polling.
- Fractional, nonfinite, and out-of-range input cannot become a saved interval.
- Invalid externally stored options fall back safely to the default.
- Updating one device does not change another device's interval.
- Slow polling does not expire observations early or restore availability after failure.

### Task 1: Implement and verify per-device polling options

**Files:** `const.py`, new `polling.py`, `config_flow.py`, `coordinator.py`,
`strings.json`, `translations/en.json`, `README.md`, and relevant tests under
`tests/`. Paths for integration files are under `custom_components/govee_ble_devices/`.

**Interfaces:**
- `CONF_POLL_INTERVAL = "poll_interval"`, default/min/max constants in `const.py`.
- `validate_poll_interval(value: Any) -> int` rejects invalid values.
- `poll_interval_seconds(options: Mapping[str, Any]) -> int` defaults invalid/missing values.
- `GoveeBLEOptionsFlow.async_step_init(user_input=None)` saves options using the standard reload flow.
- `GoveeBLECoordinator` consumes entry options for scheduling and field freshness.

- [x] Write regressions for form defaults, saved-value redisplay, accepted bounds,
  fractional/nonfinite/invalid input, unrelated-option preservation, selected-entry
  reload, coordinator default/independent intervals, and long-interval freshness.
- [x] Run the new tests and confirm failures identify the missing feature.
- [x] Implement shared validation, options flow, and coordinator behavior.
- [x] Run targeted regressions, then the full offline suite.
- [x] Add form copy/translations and README usage/limitations.
- [x] Run release validation, compileall, Ruff lint/format, and whitespace checks.
- [x] Review the complete diff against the approved design and resolve findings.

## Execution notes

Work on `feature/per-device-polling` in the existing checkout so the installed
development environment and HA source symlink remain usable. Execute inline;
the user's design approval authorizes implementation without another plan gate.
Do not commit or publish unless requested. Record progress in the local task
ledger, and leave the changes reviewable in the workspace.
