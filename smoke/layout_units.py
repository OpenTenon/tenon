#!/usr/bin/env python3
"""Apply the closest-to-square exact-unit geometry to one smoke design."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from design_manifest import ManifestError, load_designs, write_design_manifest
from layout_geometry import closest_grid, geometry

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", required=True)
    parser.add_argument("--units", required=True, type=int)
    args = parser.parse_args()
    if args.units < 1:
        parser.error("--units must be at least one")

    try:
        design = load_designs([args.design])[0]
    except ManifestError as error:
        parser.error(str(error))

    columns, rows = closest_grid(args.units)
    die_area, core_area = geometry(columns, rows)
    config_path = ROOT / args.design / "config.json"
    config = json.loads(config_path.read_text())
    config["DIE_AREA"] = die_area
    config["CORE_AREA"] = core_area
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    design["physical"]["required_layout_units"] = args.units
    design["physical"]["layout_grid"] = {"columns": columns, "rows": rows}
    write_design_manifest(args.design, design)
    load_designs([args.design])
    print(
        f"{args.design}: {columns}x{rows} grid, {args.units} unit(s), "
        f"die={die_area}, core={core_area}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
