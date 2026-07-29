#!/usr/bin/env python3
"""Apply the fixed 25x40 um smoke layout-unit geometry to one design."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UNIT_DIE_WIDTH_UM = 25.0
UNIT_DIE_HEIGHT_UM = 40.0
UNIT_CORE_WIDTH_UM = 10.0
UNIT_CORE_HEIGHT_UM = 14.0
CORE_X_MIN_UM = 7.4
SITE_HEIGHT_UM = 1.4


def core_y_min(units: int) -> float:
    ideal = 13.0 * units
    return round(math.floor(ideal / SITE_HEIGHT_UM) * SITE_HEIGHT_UM, 1)


def geometry(units: int) -> tuple[list[float], list[float]]:
    y_min = core_y_min(units)
    return (
        [0.0, 0.0, UNIT_DIE_WIDTH_UM, UNIT_DIE_HEIGHT_UM * units],
        [
            CORE_X_MIN_UM,
            y_min,
            CORE_X_MIN_UM + UNIT_CORE_WIDTH_UM,
            round(y_min + UNIT_CORE_HEIGHT_UM * units, 1),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", required=True)
    parser.add_argument("--units", required=True, type=int)
    args = parser.parse_args()
    if args.units < 1:
        parser.error("--units must be at least one")

    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    design = next((item for item in manifest["designs"] if item["name"] == args.design), None)
    if design is None:
        parser.error(f"unknown smoke design: {args.design}")

    die_area, core_area = geometry(args.units)
    config_path = ROOT / args.design / "config.json"
    config = json.loads(config_path.read_text())
    config["DIE_AREA"] = die_area
    config["CORE_AREA"] = core_area
    design["layout_units"] = args.units
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{args.design}: {args.units} unit(s), die={die_area}, core={core_area}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
