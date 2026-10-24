# Local Home Assistant Implementation Plan

> **For agentic workers:** Execute inline with superpowers:executing-plans. This is environment configuration and documentation, with direct runtime verification rather than new implementation tests.

**Goal:** Run this checkout in a local Home Assistant instance using Mac Bluetooth.

**Architecture:** Separate `.venv-ha/` Python environment and ignored `.ha-dev/` runtime data. A symlink exposes the integration source to Home Assistant.

**Tech Stack:** Python 3.14, Home Assistant 2026.10.3, existing pytest and Ruff tooling.

**Spec:** `docs/superpowers/specs/2026-10-24-local-home-assistant-design.md`

## Global constraints

- Preserve `.venv/` and production proxies.
- Bind HTTP to `127.0.0.1:8123`; do not create an owner account or configure devices automatically.
- Ignore `.venv-ha/` and `.ha-dev/`; keep normal logging.
- Use the existing requirements and manifest dependency pins.

## Review focus

- macOS Bluetooth permission can prevent startup; report any user action needed.
- Runtime credentials, logs, and addresses must stay outside tracked files.
- Source changes require restart, not a copied integration or new release.
- The existing Python 3.15 environment must remain untouched.
- Hardware behavior and production device contention are separate from offline checks.

## Task 1: Install and document the local runtime

**Files:** `.gitignore`, `dev/configuration.yaml`, `README.md`; ignored `.venv-ha/` and `.ha-dev/`.

- [x] Add ignored runtime paths and a minimal UI/Bluetooth configuration.
- [x] Create `.venv-ha/` with Python 3.14 and install existing test requirements and manifest client.
- [x] Create `.ha-dev/`, seed its configuration, and link the integration source.
- [x] Document setup, start, stop, restart, configuration check, offline checks, Bluetooth permission, and device contention.
- [x] Verify version, dependency installation, ignored paths, configuration check, HTTP onboarding, and integration manifest discovery.
- [x] Run pytest, release validation, compileall, and Ruff checks; record actual outcomes.

## Execution notes

Approved design and user request authorize inline environment setup in this checkout. No integration behavior changes, commits, or production changes are needed. Keep this work in the requested checkout so the running instance tests the user's working source.

## Verified results

- Python 3.14.3; Home Assistant 2026.10.3; govee-ble-client 1.0.0b3.
- 32 offline tests passed (five upstream deprecation warnings).
- Release validator, compileall, Ruff lint/format, pip check, and Home Assistant configuration validation passed.
- Integration source symlink resolves correctly and runtime paths are ignored by Git.
- Home Assistant recognized the custom integration and served onboarding with HTTP 200 after restart. No owner account or Govee device was configured.
- macOS Bluetooth authorization reports allowed. Actual device polling/control remains unverified; the upstream scanner logs a BlueZ stop-discovery message on Mac startup.
- Installed Homebrew ffmpeg and jpeg-turbo; verified FFmpeg is on PATH and TurboJPEG loads. Native media errors from the initial startup disappeared after restart.
- Independent read-only review found the missing native prerequisites in the initial README draft; those are now documented. No other important findings.
- Runtime setup stayed in the requested checkout and preserved the pre-existing Python 3.15 `.venv/`. No commits or production changes were made.
