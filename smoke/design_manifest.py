#!/usr/bin/env python3
"""Load and validate the per-design metadata for ICS55 smoke examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from layout_geometry import geometry

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "manifest.json"
SCHEMA_VERSION = 1
DIE_AREA_UNIT_UM2 = 1_000
DIE_AREA_TOLERANCE_UM2 = 0.001


class ManifestError(ValueError):
    """Raised when the smoke catalog or a local design manifest is invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise ManifestError(f"missing file: {path.relative_to(ROOT)}") from error
    except json.JSONDecodeError as error:
        raise ManifestError(f"invalid JSON in {path.relative_to(ROOT)}: {error.msg}") from error
    if not isinstance(value, dict):
        raise ManifestError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def _validate_keys(
    value: dict[str, Any],
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing:
        raise ManifestError(f"{label} is missing required keys: {', '.join(missing)}")
    if unknown:
        raise ManifestError(f"{label} has unsupported keys: {', '.join(unknown)}")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{label} must be a non-empty string")
    return value


def _require_positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ManifestError(f"{label} must be a positive integer")
    return value


def catalog_names() -> list[str]:
    catalog = _read_json(CATALOG_PATH)
    _validate_keys(catalog, {"schema_version", "designs"}, set(), "smoke/manifest.json")
    if catalog["schema_version"] != SCHEMA_VERSION:
        raise ManifestError("smoke/manifest.json has an unsupported schema_version")
    designs = catalog["designs"]
    if not isinstance(designs, list) or not designs:
        raise ManifestError("smoke/manifest.json designs must be a non-empty list")
    if any(not isinstance(name, str) or not name for name in designs):
        raise ManifestError("smoke/manifest.json design names must be non-empty strings")
    if len(set(designs)) != len(designs):
        raise ManifestError("smoke/manifest.json contains duplicate design names")
    for name in designs:
        if Path(name).name != name or name in {".", ".."}:
            raise ManifestError(f"invalid smoke design name: {name}")
    return designs


def manifest_path(name: str) -> Path:
    return ROOT / name / "manifest.json"


def _validate_port(port: Any, manifest_label: str) -> None:
    if not isinstance(port, dict):
        raise ManifestError(f"{manifest_label} ports entries must be JSON objects")
    _validate_keys(
        port,
        {"name", "direction", "width", "role"},
        {"bit_roles"},
        f"{manifest_label} port",
    )
    _require_string(port["name"], f"{manifest_label} port name")
    if port["direction"] not in {"input", "output", "inout"}:
        raise ManifestError(f"{manifest_label} port {port['name']} has an invalid direction")
    _require_positive_int(port["width"], f"{manifest_label} port {port['name']} width")
    _require_string(port["role"], f"{manifest_label} port {port['name']} role")
    bit_roles = port.get("bit_roles", [])
    if not isinstance(bit_roles, list):
        raise ManifestError(f"{manifest_label} port {port['name']} bit_roles must be a list")
    for bit_role in bit_roles:
        if not isinstance(bit_role, dict):
            raise ManifestError(f"{manifest_label} port {port['name']} bit_roles entries must be objects")
        _validate_keys(bit_role, {"bits", "role"}, set(), f"{manifest_label} bit role")
        _require_string(bit_role["bits"], f"{manifest_label} port {port['name']} bit range")
        _require_string(bit_role["role"], f"{manifest_label} port {port['name']} bit role")


def _validate_design(name: str, manifest: dict[str, Any]) -> None:
    label = str(manifest_path(name).relative_to(ROOT))
    _validate_keys(
        manifest,
        {
            "schema_version",
            "name",
            "top",
            "rtl",
            "kind",
            "description",
            "ports",
            "expected_behavior",
            "physical",
        },
        {"interface", "testbench"},
        label,
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ManifestError(f"{label} has an unsupported schema_version")
    if _require_string(manifest["name"], f"{label} name") != name:
        raise ManifestError(f"{label} name must match its directory and catalog entry")
    _require_string(manifest["top"], f"{label} top")
    rtl = _require_string(manifest["rtl"], f"{label} rtl")
    if Path(rtl).name != rtl or not rtl.endswith(".sv"):
        raise ManifestError(f"{label} rtl must name a local SystemVerilog file")
    if manifest["kind"] not in {"combinational", "sequential", "demonstration"}:
        raise ManifestError(f"{label} has an invalid kind")
    _require_string(manifest["description"], f"{label} description")
    if "interface" in manifest:
        _require_string(manifest["interface"], f"{label} interface")
    if "testbench" in manifest:
        testbench = _require_string(manifest["testbench"], f"{label} testbench")
        if Path(testbench).name != testbench or not testbench.endswith(".sv"):
            raise ManifestError(f"{label} testbench must name a local SystemVerilog file")
        if not (ROOT / name / testbench).is_file():
            raise ManifestError(f"{label} testbench file is missing")

    ports = manifest["ports"]
    if not isinstance(ports, list) or not ports:
        raise ManifestError(f"{label} ports must be a non-empty list")
    port_names: set[str] = set()
    for port in ports:
        _validate_port(port, label)
        port_name = str(port["name"])
        if port_name in port_names:
            raise ManifestError(f"{label} declares port {port_name} more than once")
        port_names.add(port_name)
    if not {"VDD", "VSS"}.issubset(port_names):
        raise ManifestError(f"{label} must document VDD and VSS")

    expected_behavior = manifest["expected_behavior"]
    if not isinstance(expected_behavior, list) or not expected_behavior:
        raise ManifestError(f"{label} expected_behavior must be a non-empty list")
    for expected in expected_behavior:
        _require_string(expected, f"{label} expected_behavior entry")

    physical = manifest["physical"]
    if not isinstance(physical, dict):
        raise ManifestError(f"{label} physical must be an object")
    _validate_keys(
        physical,
        {"required_layout_units", "layout_grid"},
        set(),
        f"{label} physical",
    )
    units = _require_positive_int(physical["required_layout_units"], f"{label} required_layout_units")
    layout_grid = physical["layout_grid"]
    if not isinstance(layout_grid, dict):
        raise ManifestError(f"{label} layout_grid must be an object")
    _validate_keys(layout_grid, {"columns", "rows"}, set(), f"{label} layout_grid")
    columns = _require_positive_int(layout_grid["columns"], f"{label} grid columns")
    rows = _require_positive_int(layout_grid["rows"], f"{label} grid rows")
    if columns * rows != units:
        raise ManifestError(f"{label} layout_grid does not match required_layout_units")

    design_dir = ROOT / name
    rtl_path = design_dir / rtl
    if not rtl_path.is_file():
        raise ManifestError(f"{label} rtl file is missing")
    config = _read_json(design_dir / "config.json")
    if config.get("DESIGN_NAME") != manifest["top"]:
        raise ManifestError(f"{label} top does not match config.json DESIGN_NAME")
    verilog_files = config.get("VERILOG_FILES")
    if not isinstance(verilog_files, list) or f"dir::{rtl}" not in verilog_files:
        raise ManifestError(f"{label} rtl does not match config.json VERILOG_FILES")
    die_area = config.get("DIE_AREA")
    if (
        not isinstance(die_area, list)
        or len(die_area) != 4
        or any(not isinstance(value, (int, float)) for value in die_area)
    ):
        raise ManifestError(f"{label} config.json DIE_AREA must contain four numbers")
    expected_die_area, expected_core_area = geometry(columns, rows)
    if any(
        abs(actual - expected) > DIE_AREA_TOLERANCE_UM2
        for actual, expected in zip(die_area, expected_die_area, strict=True)
    ):
        raise ManifestError(f"{label} layout_grid does not match config.json DIE_AREA")
    if units > 1:
        core_area = config.get("CORE_AREA")
        if (
            not isinstance(core_area, list)
            or len(core_area) != 4
            or any(not isinstance(value, (int, float)) for value in core_area)
            or any(
                abs(actual - expected) > DIE_AREA_TOLERANCE_UM2
                for actual, expected in zip(core_area, expected_core_area, strict=True)
            )
        ):
            raise ManifestError(f"{label} layout_grid does not match config.json CORE_AREA")


def load_design(name: str) -> dict[str, Any]:
    if name not in catalog_names():
        raise ManifestError(f"smoke design is not present in the catalog: {name}")
    manifest = _read_json(manifest_path(name))
    _validate_design(name, manifest)
    return manifest


def load_designs(selected: Iterable[str] | None = None) -> list[dict[str, Any]]:
    names = catalog_names()
    if selected is not None:
        selected_names = set(selected)
        unknown = sorted(selected_names - set(names))
        if unknown:
            raise ManifestError(f"unknown smoke designs: {', '.join(unknown)}")
        names = [name for name in names if name in selected_names]
    return [load_design(name) for name in names]


def write_design_manifest(name: str, manifest: dict[str, Any]) -> None:
    manifest_path(name).write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", action="append")
    args = parser.parse_args()
    designs = load_designs(args.design)
    print(f"PASS: validated {len(designs)} smoke design manifests.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManifestError as error:
        raise SystemExit(f"ERROR: {error}")
