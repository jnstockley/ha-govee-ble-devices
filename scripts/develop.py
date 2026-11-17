"""Initialize, check, or run a private Home Assistant instance from this checkout."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def initialize(config_dir: Path) -> None:
    """Seed configuration once and link source without replacing local files."""
    config_dir = config_dir.resolve()
    components = config_dir / "custom_components"
    components.mkdir(parents=True, exist_ok=True)
    target = ROOT / "custom_components/govee_ble_devices"
    link = components / "govee_ble_devices"
    if link.is_symlink() and link.resolve() == target:
        pass
    elif link.exists() or link.is_symlink():
        raise FileExistsError(f"Refusing to replace {link}; move it aside first")
    else:
        link.symlink_to(target, target_is_directory=True)
    seed = config_dir / "configuration.yaml"
    if not seed.exists():
        # Exclusive creation protects a concurrently created local configuration.
        with seed.open("x", encoding="utf-8") as file:
            file.write((ROOT / "dev/configuration.yaml").read_text(encoding="utf-8"))


def main() -> None:
    """Run with the same Python environment that owns Home Assistant."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "check", "run"))
    parser.add_argument("--config", type=Path, default=ROOT / ".ha-dev")
    parser.add_argument(
        "--client",
        type=Path,
        help="Explicit editable govee-ble-client checkout override (init only)",
    )
    args = parser.parse_args()
    if args.client and args.command != "init":
        parser.error("--client is only supported with init")
    if args.client:
        client = args.client.resolve()
        metadata = tomllib.loads((client / "pyproject.toml").read_text())
        if metadata.get("project", {}).get("name") != "govee-ble-client":
            parser.error("--client must point to a govee-ble-client checkout")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--editable", str(client)],
            check=True,
        )
    initialize(args.config)
    if args.command == "init":
        print(f"Development configuration: {args.config.resolve()}")
        return
    command = [
        sys.executable,
        "-m",
        "homeassistant",
        "--config",
        str(args.config.resolve()),
    ]
    if args.command == "check":
        command.extend(["--script", "check_config"])
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
