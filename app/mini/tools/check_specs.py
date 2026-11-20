#!/usr/bin/env python3
"""Exercise Mini footprint allocation and capacity invariants."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from generate import GenerationError, generated_files
from generate_physical import top_config

ROOT = Path(__file__).resolve().parents[1]
FABRIC = ROOT / "specs" / "fabric.json"
MIXED = ROOT / "tb" / "mixed_projects.json"


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GenerationError(message)


def check_mixed() -> None:
    files = generated_files(FABRIC, MIXED)
    layout = json.loads(files["layout.json"])
    projects = {project["name"]: project for project in layout["projects"]}
    require(projects["whole_cluster"]["units"] == 256, "16x16 project area is wrong")
    require(projects["square_8x8"]["units"] == 64, "8x8 project area is wrong")
    require(projects["rectangle_3x5"]["units"] == 15, "3x5 project area is wrong")
    require(projects["whole_cluster"]["address"] == 0, "fixed anchor address changed")
    anchor_addresses = {project["address"] for project in layout["projects"]}
    require(1 not in anchor_addresses, "a 16x16 continuation site became addressable")


def check_overlap() -> None:
    with tempfile.TemporaryDirectory(prefix="tenon-mini-overlap-") as temporary:
        path = Path(temporary) / "projects.json"
        write_json(
            path,
            {
                "schema_version": 1,
                "projects": [
                    {
                        "name": "first",
                        "module": "first",
                        "columns": 2,
                        "rows": 2,
                        "location": {"cluster": 0, "branch": 0, "site": 0},
                    },
                    {
                        "name": "second",
                        "module": "second",
                        "columns": 1,
                        "rows": 1,
                        "location": {"cluster": 0, "branch": 1, "site": 1},
                    },
                ],
            },
        )
        try:
            generated_files(FABRIC, path)
        except GenerationError:
            return
        raise GenerationError("overlapping projects were accepted")


def check_future_capacity() -> None:
    with tempfile.TemporaryDirectory(prefix="tenon-mini-future-") as temporary:
        for cluster_count in (10, 16):
            fabric_path = Path(temporary) / f"fabric-{cluster_count}.json"
            fabric = json.loads(FABRIC.read_text())
            fabric["cluster_count"] = cluster_count
            write_json(fabric_path, fabric)
            files = generated_files(fabric_path, MIXED)
            layout = json.loads(files["layout.json"])
            require(
                layout["address_width"] == 12,
                f"{cluster_count} clusters did not produce a 12-bit address",
            )
            require(
                layout["total_sites"] == cluster_count * 256,
                f"{cluster_count} clusters produced the wrong capacity",
            )
            physical = top_config(fabric, {"DIE_AREA": [0, 0, 640, 760]})
            instances = physical["MACROS"]["tenon_mini_cluster_tile"]["instances"]
            require(
                len(instances) == cluster_count,
                f"{cluster_count} clusters produced the wrong placement count",
            )


def main() -> int:
    check_mixed()
    check_overlap()
    check_future_capacity()
    print("PASS: Mini footprint and capacity checks completed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GenerationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
