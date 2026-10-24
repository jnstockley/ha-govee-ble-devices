# Repository Guidelines

## Project Structure & Module Organization

The Home Assistant integration lives in `custom_components/govee_ble_devices/`. `coordinator.py` owns polling, controls, and session cleanup; `connection.py` selects ATT write modes; `notifications.py` bounds buffering and tracks observations. Entity platforms are in `sensor.py`, `fan.py`, `switch.py`, and `button.py`. Keep metadata in `manifest.json`, user-facing text in `strings.json` and `translations/en.json`, and branding in `brand/`.

Offline tests live in `tests/`, with shared fixtures in `conftest.py` and radio doubles in `helpers.py`. Release utilities live in `scripts/`; design documents and implementation plans live in `docs/superpowers/`.

## Build, Test, and Development Commands

Use Python 3.14 and a virtual environment:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-test.txt
python scripts/check_release.py --install-requirement
python -m pytest -q
python -m compileall -q custom_components scripts tests
ruff check custom_components scripts tests
ruff format --check custom_components scripts tests
```

The release validator checks HACS metadata and installs the pinned client. Pytest runs offline regressions; compileall checks syntax; Ruff checks lint and formatting. Apply formatting with `ruff format custom_components scripts tests`. CI additionally runs hassfest and HACS validation. Live testing requires Home Assistant and supported BLE hardware; there is no standalone application build.

## Coding Style & Naming Conventions

Use four-space indentation, type annotations, `snake_case` functions/modules, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Follow existing Home Assistant `async_*` conventions. Use asynchronous I/O and preserve cancellation. Let Home Assistant own discovery and adapter routing; serialize complete transactions per device, and never automatically replay uncertain control writes.

## Testing Guidelines

Use pytest and pytest-asyncio; async mode is configured in `pytest.ini`. Name files `test_*.py` and tests `test_<behavior>`. Add regression coverage for changed behavior, especially disconnects, partial replies, cleanup, and unload. Keep radio access simulated. No numeric coverage threshold is configured. Follow the README hardware matrix for Bluetooth or proxy changes, and distinguish offline results from hardware verification.

## Commit & Pull Request Guidelines

Use concise, imperative commit subjects, following history: `Use BLE retry connector for Govee connections`. No mandatory Conventional Commits prefix is established. PRs should explain the problem, resulting behavior, relevant issues, validation commands, and hardware limitations. For dependency changes, update the manifest pin and README together. Release tags must match the manifest version, prefixed with `v`.

## Diagnostics & Privacy

Redact Bluetooth addresses and packet captures before sharing logs. Avoid logging raw payloads or encryption material; restore normal logging after diagnosis.
