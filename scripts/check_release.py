"""Validate the HACS release contract without Home Assistant installed."""

from __future__ import annotations

import argparse
import ast
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components/govee_ble_devices/manifest.json"
HACS = ROOT / "hacs.json"
VERSION = re.compile(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?\Z")


def validate_brand_images(brand_dir: Path) -> None:
    """Require PNG icon headers with standard and high-DPI dimensions.

    Home Assistant falls back to these icons for logo and dark variants.
    The existing artwork works on both light and dark backgrounds.
    """
    for filename, size in (("icon.png", 256), ("icon@2x.png", 512)):
        path = brand_dir / filename
        if not path.is_file():
            raise ValueError(f"Missing brand image: {filename}")
        data = path.read_bytes()
        if (
            len(data) < 33
            or data[:8] != b"\x89PNG\r\n\x1a\n"
            or data[8:16] != b"\x00\x00\x00\rIHDR"
        ):
            raise ValueError(f"Invalid PNG header: {filename}")
        if struct.unpack(">II", data[16:24]) != (size, size):
            raise ValueError(f"Brand image {filename} must be {size}x{size}")


def validate(tag: str | None = None, install_requirement: bool = False) -> str:
    """Check the installable layout, metadata and release version."""
    integration_dir = MANIFEST.parent
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    hacs = json.loads(HACS.read_text(encoding="utf-8"))
    version = manifest["version"]
    if not VERSION.fullmatch(version):
        raise ValueError(f"Invalid integration version: {version}")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Tag {tag} does not match manifest version v{version}")
    if manifest["domain"] != integration_dir.name:
        raise ValueError("Manifest domain does not match the integration directory")
    constants = ast.parse((integration_dir / "const.py").read_text(encoding="utf-8"))
    declared_domains = [
        ast.literal_eval(node.value)
        for node in constants.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "DOMAIN"
            for target in node.targets
        )
    ]
    if declared_domains != [manifest["domain"]]:
        raise ValueError("Python DOMAIN does not match the manifest")
    if not manifest.get("issue_tracker") or not manifest.get("codeowners"):
        raise ValueError("HACS requires an issue tracker and a code owner")
    for matcher in manifest.get("bluetooth", []):
        local_name = matcher.get("local_name", "")
        if any(char in local_name[:3] for char in "*?["):
            raise ValueError(
                f"Bluetooth local_name matcher starts with a pattern: {local_name}"
            )
    if not hacs.get("name"):
        raise ValueError("hacs.json needs a name")
    if hacs["name"] != manifest["name"]:
        raise ValueError("HACS name does not match the integration name")
    for filename in ("__init__.py", "config_flow.py", "strings.json", "icons.json"):
        if not (integration_dir / filename).is_file():
            raise ValueError(f"Missing integration file: {filename}")
    validate_brand_images(integration_dir / "brand")
    for filename in ("LICENSE", "README.md"):
        if not (ROOT / filename).is_file():
            raise ValueError(f"Missing repository file: {filename}")
    requirements = manifest.get("requirements", [])
    if len(requirements) != 1 or not re.fullmatch(
        r"govee-ble-client==\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", requirements[0]
    ):
        raise ValueError("Pin exactly one released govee-ble-client package")
    for path in integration_dir.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    if install_requirement:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", requirements[0]], check=True
        )
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from govee_ble_client import "
                    "ConnectionStrategy, GattConnection, H5179Profile, H7102Client, "
                    "H7102Profile, H7126Profile; "
                    "from inspect import signature; "
                    "assert {'connector', 'device_resolver', 'services'} "
                    "<= signature(GattConnection).parameters.keys()"
                ),
            ],
            check=True,
        )
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--install-requirement", action="store_true")
    args = parser.parse_args()
    try:
        print(
            f"Validated Govee BLE release {validate(args.tag, args.install_requirement)}"
        )
    except (KeyError, ValueError, subprocess.CalledProcessError) as err:
        parser.exit(1, f"Release validation failed: {err}\n")
