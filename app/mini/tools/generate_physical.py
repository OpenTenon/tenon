#!/usr/bin/env python3
"""Generate reproducible ICS55 hierarchy configs for the Mini reference population."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FABRIC = ROOT / "specs" / "fabric.json"
LAYOUT = ROOT / "generated" / "layout.json"
CLUSTER_CONFIG = ROOT / "physical" / "cluster" / "config.json"
TOP_CONFIG = ROOT / "physical" / "top" / "config.json"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def common_config(
    design: str,
    sources: list[str],
    pnr_sdc: str,
    pins: str,
    pdn: str,
    signoff_sdc: str | None = None,
) -> dict[str, Any]:
    return {
        "meta": {"version": 3, "flow": "Classic"},
        "DESIGN_NAME": design,
        "VERILOG_FILES": sources,
        "EXTRA_VERILOG_MODELS": [],
        "PAD_LIBS": None,
        "PAD_VERILOG_MODELS": None,
        "PAD_GDS": [],
        "PAD_LEFS": [],
        "PAD_SPICE_MODELS": [],
        "PAD_CDLS": [],
        "VDD_NETS": ["VDD"],
        "GND_NETS": ["VSS"],
        "FP_SIZING": "absolute",
        "FP_MACRO_HORIZONTAL_HALO": 2,
        "FP_MACRO_VERTICAL_HALO": 2,
        "IO_PIN_ORDER_CFG": pins,
        "IO_PIN_H_LAYER": "MET3",
        "IO_PIN_V_LAYER": "MET2",
        "IO_PIN_H_LENGTH": 0.4,
        "IO_PIN_V_LENGTH": 0.4,
        "IO_PIN_H_THICKNESS_MULT": 2,
        "IO_PIN_V_THICKNESS_MULT": 2,
        "ERRORS_ON_UNMATCHED_IO": "both",
        "FP_TRACKS_INFO": "dir::../tracks.info",
        "PNR_SDC_FILE": pnr_sdc,
        "SIGNOFF_SDC_FILE": signoff_sdc or pnr_sdc,
        "CLOCK_PERIOD": 20,
        "PDN_CFG": pdn,
        "PDN_ENABLE_GLOBAL_CONNECTIONS": True,
        "PDN_ENABLE_PINS": True,
        "PDN_ENABLE_RAILS": True,
        "PDN_CORE_RING": False,
        "PDN_CORE_RING_CONNECT_TO_PADS": False,
        "RUN_CTS": True,
        # The 2048-site reference population models every empty site as a
        # maximum-area macro. Report its electrical quality, but do not insert
        # tens of thousands of buffers into placeholder interconnect. CTS NDR
        # is also disabled because GRT otherwise removes it net-by-net before
        # routing this intentionally worst-case population.
        "CTS_APPLY_NDR": "none",
        "RUN_POST_GPL_DESIGN_REPAIR": False,
        "RUN_POST_CTS_RESIZER_TIMING": False,
        "RUN_POST_GRT_RESIZER_TIMING": False,
        "GRT_ADJUSTMENT": 0.25,
        "GRT_ALLOW_CONGESTION": False,
        "DRT_THREADS": 16,
        "DRT_OPT_ITERS": 8,
        "RUN_MAGIC_STREAMOUT": False,
        "ERROR_ON_PDN_VIOLATIONS": True,
        "ERROR_ON_TR_DRC": True,
        "ERROR_ON_DISCONNECTED_PINS": True,
    }


def cluster_config(fabric: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    projects = [project for project in layout["projects"] if project["anchor"]["cluster"] == 0]
    if len(projects) != 256 or any(
        not project["reference"] or project["columns"] != 1 or project["rows"] != 1
        for project in projects
    ):
        raise ValueError("the reference cluster flow requires 256 reference 1x1 projects")

    site = fabric["site"]
    physical = fabric["physical"]
    column_pitch = site["width_um"] + physical["site_column_channel_um"]
    row_pitch = site["height_um"] + physical["site_row_channel_um"]
    origin_x = 20.0
    origin_y = 20.0
    instances = {}
    for project in projects:
        branch = project["anchor"]["branch"]
        site_index = project["anchor"]["site"]
        instances[f"project_site_{branch}_{site_index}_I"] = {
            "location": [origin_x + site_index * column_pitch, origin_y + branch * row_pitch],
            "orientation": "N",
        }

    array_right = origin_x + 15 * column_pitch + site["width_um"]
    array_top = origin_y + 15 * row_pitch + site["height_um"]
    die_width = array_right + physical["local_router_width_um"] + 25.0
    die_height = array_top + 25.0
    config = common_config(
        "tenon_mini_cluster_tile",
        [
            "dir::../../rtl/tenon_mini_clock_gate.sv",
            "dir::../../rtl/tenon_mini_clock_channel.sv",
            "dir::../../rtl/tenon_mini_gateway.sv",
            "dir::../../rtl/tenon_mini_branch.sv",
            "dir::../../rtl/tenon_mini_cluster.sv",
            "dir::../../generated/tenon_mini_clusters.sv",
        ],
        "dir::pnr_constraints.sdc",
        "dir::pin_order.cfg",
        "dir::pdn.tcl",
        "dir::constraints.sdc",
    )
    config.update(
        {
            "CLOCK_PORT": "ctrl_clk_i",
            "DIE_AREA": [0, 0, die_width, die_height],
            "CORE_AREA": [10, 10, die_width - 10, die_height - 10],
            "PL_TARGET_DENSITY_PCT": 45,
            "MACROS": {
                "tenon_mini_reference_project": {
                    "gds": [
                        "dir::../../projects/reference/runs/ics55-reference/final/gds/tenon_mini_reference_project.gds"
                    ],
                    "lef": [
                        "dir::../../projects/reference/runs/ics55-reference/final/lef/tenon_mini_reference_project.lef"
                    ],
                    "vh": ["dir::../reference_project.blackbox.sv"],
                    "lib": {
                        "*": [
                            "dir::../../projects/reference/runs/ics55-reference/final/lib/tenon_mini_reference_project.lib"
                        ]
                    },
                    "instances": instances,
                }
            },
        }
    )
    return config


def top_config(fabric: dict[str, Any], cluster: dict[str, Any]) -> dict[str, Any]:
    cluster_count = fabric["cluster_count"]
    cluster_width = cluster["DIE_AREA"][2]
    cluster_height = cluster["DIE_AREA"][3]
    channel = fabric["physical"]["cluster_channel_um"]
    margin = fabric["physical"]["outer_margin_um"]
    if cluster_count == 8:
        grid_columns = 3
        grid_rows = 3
        # Keep the center open for the root controller and balanced trunks.
        grid_positions = (0, 1, 2, 3, 5, 6, 7, 8)
    else:
        candidates = []
        for columns in range(1, cluster_count + 1):
            rows = math.ceil(cluster_count / columns)
            width = columns * cluster_width + (columns - 1) * channel
            height = rows * cluster_height + (rows - 1) * channel
            unused = columns * rows - cluster_count
            score = abs(math.log(width / height)) + 0.15 * unused / cluster_count
            candidates.append((score, unused, columns, rows))
        _, _, grid_columns, grid_rows = min(candidates)
        grid_positions = tuple(range(cluster_count))
    instances = {}
    for cluster_index, grid_index in enumerate(grid_positions):
        row, column = divmod(grid_index, grid_columns)
        instances[f"cluster_{cluster_index}_I"] = {
            "location": [
                margin + column * (cluster_width + channel),
                margin + row * (cluster_height + channel),
            ],
            "orientation": "N",
        }
    die_width = 2 * margin + grid_columns * cluster_width + (grid_columns - 1) * channel
    die_height = 2 * margin + grid_rows * cluster_height + (grid_rows - 1) * channel
    config = common_config(
        "tenon_mini_top",
        [
            "dir::../../rtl/tenon_mini_clock_gate.sv",
            "dir::../../rtl/tenon_mini_clock_channel.sv",
            "dir::../../rtl/tenon_mini_root_controller.sv",
            "dir::../../rtl/tenon_mini_root_fabric.sv",
            "dir::../../generated/tenon_mini_top.sv",
        ],
        "dir::pnr_constraints.sdc",
        "dir::pin_order.cfg",
        "dir::pdn.tcl",
        "dir::constraints.sdc",
    )
    config.update(
        {
            "CLOCK_PORT": "ctrl_clk_i",
            "DIE_AREA": [0, 0, die_width, die_height],
            "CORE_AREA": [10, 10, die_width - 10, die_height - 10],
            "PL_TARGET_DENSITY_PCT": 35,
            "MACROS": {
                "tenon_mini_cluster_tile": {
                    "gds": [
                        "dir::../cluster/runs/ics55-cluster/final/gds/tenon_mini_cluster_tile.gds"
                    ],
                    "lef": [
                        "dir::../cluster/runs/ics55-cluster/final/lef/tenon_mini_cluster_tile.lef"
                    ],
                    "vh": ["dir::../cluster_tile.blackbox.sv"],
                    "instances": instances,
                }
            },
        }
    )
    return config


def render(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2) + "\n"


def update(path: Path, contents: str, check: bool) -> None:
    if check:
        if not path.is_file() or path.read_text() != contents:
            raise ValueError(f"generated physical config is stale: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    fabric = read_json(FABRIC)
    layout = read_json(LAYOUT)
    cluster = cluster_config(fabric, layout)
    top = top_config(fabric, cluster)
    update(CLUSTER_CONFIG, render(cluster), args.check)
    update(TOP_CONFIG, render(top), args.check)
    print(
        f"PASS: physical hierarchy uses {cluster['DIE_AREA'][2]:g}x{cluster['DIE_AREA'][3]:g} um "
        f"clusters in a {top['DIE_AREA'][2]:g}x{top['DIE_AREA'][3]:g} um top."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f"ERROR: {error}") from error
