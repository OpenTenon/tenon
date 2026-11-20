#!/usr/bin/env python3
"""Generate the Tenon Mini project placement and SystemVerilog assembly."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FABRIC = ROOT / "specs" / "fabric.json"
DEFAULT_PROJECTS = ROOT / "specs" / "projects.json"
DEFAULT_OUTPUT = ROOT / "generated"
SCHEMA_VERSION = 1


class GenerationError(ValueError):
    """Raised when a Mini specification cannot be generated safely."""


@dataclass(frozen=True)
class Location:
    cluster: int
    branch: int
    site: int


@dataclass
class Project:
    name: str
    module: str
    columns: int
    rows: int
    rtl: str | None
    views: dict[str, str]
    location: Location | None = None
    reference: bool = False

    @property
    def units(self) -> int:
        return self.columns * self.rows


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise GenerationError(f"missing specification: {path}") from error
    except json.JSONDecodeError as error:
        raise GenerationError(f"invalid JSON in {path}: {error.msg}") from error
    if not isinstance(value, dict):
        raise GenerationError(f"{path} must contain a JSON object")
    return value


def positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GenerationError(f"{label} must be a positive integer")
    return value


def nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise GenerationError(f"{label} must be a non-empty string")
    return value


def validate_fabric(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "cluster_count",
        "branches_per_cluster",
        "sites_per_branch",
        "site",
        "clocks",
        "physical",
    }
    if set(value) != required:
        raise GenerationError("fabric.json has missing or unsupported keys")
    if value["schema_version"] != SCHEMA_VERSION:
        raise GenerationError("fabric.json has an unsupported schema_version")
    clusters = positive_int(value["cluster_count"], "cluster_count")
    branches = positive_int(value["branches_per_cluster"], "branches_per_cluster")
    sites = positive_int(value["sites_per_branch"], "sites_per_branch")
    if branches != 16 or sites != 16:
        raise GenerationError("Mini v1 requires exactly 16 branches and 16 sites per cluster")
    if clusters < 2:
        raise GenerationError("cluster_count must be at least two")
    site = value["site"]
    if not isinstance(site, dict):
        raise GenerationError("site must be an object")
    if site.get("width_um") != 25.0 or site.get("height_um") != 40.0:
        raise GenerationError("Mini v1 site dimensions must be 25x40 um")
    if site.get("pin_edge") != "east":
        raise GenerationError("the native project pin edge must be east")
    return value


def parse_project(value: Any, index: int) -> Project:
    if not isinstance(value, dict):
        raise GenerationError(f"projects[{index}] must be an object")
    required = {"name", "module", "columns", "rows"}
    optional = {"rtl", "views", "location"}
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing or unknown:
        raise GenerationError(
            f"projects[{index}] has invalid keys; missing={sorted(missing)}, "
            f"unsupported={sorted(unknown)}"
        )
    name = nonempty_string(value["name"], f"projects[{index}].name")
    module = nonempty_string(value["module"], f"projects[{index}].module")
    columns = positive_int(value["columns"], f"projects[{index}].columns")
    rows = positive_int(value["rows"], f"projects[{index}].rows")
    if columns > 16 or rows > 16:
        raise GenerationError(f"project {name} exceeds the 16x16 cluster boundary")
    rtl = value.get("rtl")
    if rtl is not None:
        rtl = nonempty_string(rtl, f"projects[{index}].rtl")
    views = value.get("views", {})
    if not isinstance(views, dict):
        raise GenerationError(f"projects[{index}].views must be an object")
    for key, path in views.items():
        if key not in {"lef", "gds", "lib"}:
            raise GenerationError(f"project {name} has unsupported view {key}")
        nonempty_string(path, f"project {name} view {key}")
    location_value = value.get("location")
    location = None
    if location_value is not None:
        if not isinstance(location_value, dict) or set(location_value) != {
            "cluster",
            "branch",
            "site",
        }:
            raise GenerationError(f"project {name} location is invalid")
        location = Location(
            cluster=location_value["cluster"],
            branch=location_value["branch"],
            site=location_value["site"],
        )
        if any(not isinstance(item, int) or isinstance(item, bool) for item in asdict(location).values()):
            raise GenerationError(f"project {name} location values must be integers")
    return Project(name, module, columns, rows, rtl, dict(views), location)


def load_projects(value: dict[str, Any]) -> tuple[list[Project], dict[str, Any] | None]:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise GenerationError("projects.json has an unsupported schema_version")
    if set(value) - {"schema_version", "projects", "reference_fill"}:
        raise GenerationError("projects.json has unsupported keys")
    projects_value = value.get("projects")
    if not isinstance(projects_value, list):
        raise GenerationError("projects.json projects must be a list")
    projects = [parse_project(project, index) for index, project in enumerate(projects_value)]
    names = [project.name for project in projects]
    if len(names) != len(set(names)):
        raise GenerationError("projects.json contains duplicate project names")
    reference = value.get("reference_fill")
    if reference is not None:
        if not isinstance(reference, dict) or set(reference) != {
            "name_prefix",
            "module",
            "rtl",
            "views",
        }:
            raise GenerationError("reference_fill has invalid keys")
        for key in ("name_prefix", "module", "rtl"):
            nonempty_string(reference[key], f"reference_fill.{key}")
        if not isinstance(reference["views"], dict) or set(reference["views"]) != {
            "lef",
            "gds",
            "lib",
        }:
            raise GenerationError("reference_fill must provide lef, gds and lib views")
    return projects, reference


def footprint(project: Project, location: Location) -> list[tuple[int, int, int]]:
    return [
        (location.cluster, location.branch + row, location.site + column)
        for row in range(project.rows)
        for column in range(project.columns)
    ]


def can_place(
    project: Project,
    location: Location,
    cluster_count: int,
    occupied: set[tuple[int, int, int]],
) -> bool:
    if not 0 <= location.cluster < cluster_count:
        return False
    if location.branch < 0 or location.site < 0:
        return False
    if location.branch + project.rows > 16 or location.site + project.columns > 16:
        return False
    return not any(site in occupied for site in footprint(project, location))


def place_projects(projects: list[Project], cluster_count: int) -> set[tuple[int, int, int]]:
    occupied: set[tuple[int, int, int]] = set()
    fixed = [project for project in projects if project.location is not None]
    automatic = [project for project in projects if project.location is None]
    for project in fixed:
        assert project.location is not None
        if not can_place(project, project.location, cluster_count, occupied):
            raise GenerationError(f"fixed project {project.name} overlaps or exceeds its cluster")
        occupied.update(footprint(project, project.location))
    automatic.sort(key=lambda project: (-project.units, -project.rows, -project.columns, project.name))
    for project in automatic:
        placed = False
        for cluster in range(cluster_count):
            for branch in range(16):
                for site in range(16):
                    location = Location(cluster, branch, site)
                    if can_place(project, location, cluster_count, occupied):
                        project.location = location
                        occupied.update(footprint(project, location))
                        placed = True
                        break
                if placed:
                    break
            if placed:
                break
        if not placed:
            raise GenerationError(f"project {project.name} could not be placed")
    return occupied


def fill_reference(
    projects: list[Project],
    reference: dict[str, Any] | None,
    cluster_count: int,
    occupied: set[tuple[int, int, int]],
) -> None:
    if reference is None:
        return
    for cluster in range(cluster_count):
        for branch in range(16):
            for site in range(16):
                coordinate = (cluster, branch, site)
                if coordinate in occupied:
                    continue
                name = f"{reference['name_prefix']}_{cluster}_{branch}_{site}"
                projects.append(
                    Project(
                        name=name,
                        module=reference["module"],
                        columns=1,
                        rows=1,
                        rtl=reference["rtl"],
                        views=dict(reference["views"]),
                        location=Location(cluster, branch, site),
                        reference=True,
                    )
                )
                occupied.add(coordinate)


def address(project: Project) -> int:
    assert project.location is not None
    return (project.location.cluster << 8) | (project.location.branch << 4) | project.location.site


def layout_json(fabric: dict[str, Any], projects: list[Project]) -> str:
    records = []
    for project in sorted(projects, key=address):
        assert project.location is not None
        records.append(
            {
                "name": project.name,
                "module": project.module,
                "address": address(project),
                "columns": project.columns,
                "rows": project.rows,
                "units": project.units,
                "anchor": asdict(project.location),
                "reference": project.reference,
            }
        )
    value = {
        "schema_version": SCHEMA_VERSION,
        "cluster_count": fabric["cluster_count"],
        "address_width": 8 + (fabric["cluster_count"] - 1).bit_length(),
        "total_sites": fabric["cluster_count"] * 256,
        "projects": records,
    }
    return json.dumps(value, indent=2) + "\n"


def address_csv(projects: list[Project]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        ("address", "cluster", "branch", "site", "name", "module", "columns", "rows", "units")
    )
    for project in sorted(projects, key=address):
        assert project.location is not None
        writer.writerow(
            (
                address(project),
                project.location.cluster,
                project.location.branch,
                project.location.site,
                project.name,
                project.module,
                project.columns,
                project.rows,
                project.units,
            )
        )
    return output.getvalue()


def catalog_sv(fabric: dict[str, Any], projects: list[Project]) -> str:
    cluster_count = fabric["cluster_count"]
    total_sites = cluster_count * 256
    address_width = 8 + (cluster_count - 1).bit_length()
    valid = ["0"] * total_sites
    for project in projects:
        valid[address(project)] = "1"
    valid_literal = "".join(reversed(valid))
    lines = [
        "// Generated by app/mini/tools/generate.py. Do not edit.",
        "`default_nettype none",
        "",
    ]

    occupied_anchors = {address(project): project for project in projects}
    cluster_projects_by_index = []
    cluster_signatures = []
    for cluster_index in range(cluster_count):
        cluster_projects = {
            local_address: project
            for local_address in range(256)
            if (project := occupied_anchors.get(cluster_index * 256 + local_address)) is not None
        }
        cluster_projects_by_index.append(cluster_projects)
        cluster_signatures.append(
            tuple(
                (local_address, project.module, project.columns, project.rows)
                for local_address, project in sorted(cluster_projects.items())
            )
        )
    unique_signatures = list(dict.fromkeys(cluster_signatures))
    module_by_signature = {
        signature: (
            "tenon_mini_cluster_tile"
            if len(unique_signatures) == 1
            else f"tenon_mini_cluster_tile_{cluster_signatures.index(signature)}"
        )
        for signature in unique_signatures
    }

    emitted_signatures = set()
    for cluster_index, cluster_projects in enumerate(cluster_projects_by_index):
        signature = cluster_signatures[cluster_index]
        if signature in emitted_signatures:
            continue
        emitted_signatures.add(signature)
        tile_module = module_by_signature[signature]
        cluster_valid = ["0"] * 256
        for local_address in cluster_projects:
            cluster_valid[local_address] = "1"
        cluster_literal = "".join(reversed(cluster_valid))
        lines.extend(
            [
                f"module {tile_module} (",
                "    input  logic        ctrl_clk_i,",
                "    input  logic        ctrl_rst_n_i,",
                "    input  logic [ 1:0] cluster_clock_i,",
                "    input  logic [ 1:0] rst_n_i,",
                "    input  logic [ 3:0] branch_addr_i,",
                "    input  logic [ 3:0] site_addr_i,",
                "    input  logic        branch_clock_request_i,",
                "    input  logic        route_enable_i,",
                "    input  logic [11:0] gpio_i,",
                "    output logic [ 1:0] branch_clock_active_o,",
                "    output logic [11:0] gpio_o,",
                "    output logic [11:0] gpio_oe,",
                "    inout  wire         VDD,",
                "    inout  wire         VSS",
                ");",
                "",
                f"  localparam logic [255:0] ANCHOR_VALID = 256'b{cluster_literal};",
                "",
                "  logic [ 511:0] site_clock;",
                "  logic [3071:0] site_gpio_o;",
                "  logic [3071:0] site_gpio_oe;",
                "  logic [ 255:0] site_enable;",
                "",
                "  tenon_mini_cluster #(",
                "      .ANCHOR_VALID(ANCHOR_VALID)",
                "  ) cluster_I (",
                "      .ctrl_clk_i,",
                "      .ctrl_rst_n_i,",
                "      .cluster_clock_i,",
                "      .branch_addr_i,",
                "      .site_addr_i,",
                "      .branch_clock_request_i,",
                "      .route_enable_i,",
                "      .site_gpio_o_i (site_gpio_o),",
                "      .site_gpio_oe_i(site_gpio_oe),",
                "      .site_enable_o (site_enable),",
                "      .site_clock_o  (site_clock),",
                "      .branch_clock_active_o,",
                "      .gpio_o,",
                "      .gpio_oe",
                "  );",
                "",
            ]
        )
        for local_address in range(256):
            project = cluster_projects.get(local_address)
            if project is None:
                lines.extend(
                    [
                        f"  assign site_gpio_o[{local_address * 12}+:12] = '0;",
                        f"  assign site_gpio_oe[{local_address * 12}+:12] = '0;",
                    ]
                )
                continue
            branch = local_address // 16
            site = local_address % 16
            instance = f"site_{branch}_{site}"
            lines.extend(
                [
                    f"  logic [11:0] {instance}_gpio_i;",
                    f"  logic [11:0] {instance}_gpio_o;",
                    f"  logic [11:0] {instance}_gpio_oe;",
                    f"  logic        {instance}_enable;",
                    "",
                    f"  tenon_mini_gateway gateway_{instance}_I (",
                    f"      .selected_i      (site_enable[{local_address}]),",
                    "      .fabric_gpio_i   (gpio_i),",
                    f"      .project_gpio_i  ({instance}_gpio_i),",
                    f"      .project_enable_o({instance}_enable),",
                    f"      .project_gpio_o  ({instance}_gpio_o),",
                    f"      .project_gpio_oe ({instance}_gpio_oe),",
                    f"      .site_gpio_o     (site_gpio_o[{local_address * 12}+:12]),",
                    f"      .site_gpio_oe    (site_gpio_oe[{local_address * 12}+:12])",
                    "  );",
                    "",
                    f"  {project.module} project_{instance}_I (",
                    f"      .clk_i   (site_clock[{local_address * 2}+:2]),",
                    "      .rst_n_i,",
                    f"      .enable_i({instance}_enable),",
                    f"      .gpio_i  ({instance}_gpio_i),",
                    f"      .gpio_o  ({instance}_gpio_o),",
                    f"      .gpio_oe ({instance}_gpio_oe),",
                    "      .VDD,",
                    "      .VSS",
                    "  );",
                    "",
                ]
            )
        lines.extend(["endmodule", ""])

    lines.extend(
        [
            "module tenon_mini_top (",
            "    input  logic        ctrl_clk_i,",
            "    input  logic        ctrl_rst_n_i,",
            f"    input  logic [{address_width - 1:2}:0] project_addr_i,",
            "    input  logic        select_commit_i,",
            "    output logic        select_ready_o,",
            "    output logic        select_valid_o,",
            "    input  logic        fabric_enable_i,",
            "    input  logic [ 1:0] clk_i,",
            "    input  logic [ 1:0] rst_n_i,",
            "    input  logic [11:0] gpio_i,",
            "    output logic [11:0] gpio_o,",
            "    output logic [11:0] gpio_oe,",
            "    inout  wire         VDD,",
            "    inout  wire         VSS",
            ");",
            "",
            f"  localparam int CLUSTER_COUNT = {cluster_count};",
            f"  localparam int TOTAL_SITES = {total_sites};",
            f"  localparam logic [TOTAL_SITES-1:0] ANCHOR_VALID = {total_sites}'b{valid_literal};",
            "",
            "  logic [ CLUSTER_COUNT*2-1:0] cluster_clock;",
            "  logic [                 3:0] branch_addr;",
            "  logic [                 3:0] site_addr;",
            "  logic [   CLUSTER_COUNT-1:0] branch_clock_request;",
            "  logic [   CLUSTER_COUNT-1:0] route_enable;",
            "  logic [ CLUSTER_COUNT*2-1:0] branch_clock_active;",
            "  logic [CLUSTER_COUNT*12-1:0] cluster_gpio_o;",
            "  logic [CLUSTER_COUNT*12-1:0] cluster_gpio_oe;",
            "`ifdef TENON_MINI_VERIFY",
            "  logic [TOTAL_SITES-1:0] site_enable;",
            "`endif",
            "",
            "  tenon_mini_root_fabric #(",
            "      .CLUSTER_COUNT(CLUSTER_COUNT),",
            "      .ANCHOR_VALID (ANCHOR_VALID)",
            "  ) root_fabric_I (",
            "      .ctrl_clk_i,",
            "      .ctrl_rst_n_i,",
            "      .project_addr_i,",
            "      .select_commit_i,",
            "      .select_ready_o,",
            "      .select_valid_o,",
            "      .fabric_enable_i,",
            "      .clk_i,",
            "      .cluster_clock_o       (cluster_clock),",
            "      .branch_addr_o         (branch_addr),",
            "      .site_addr_o           (site_addr),",
            "      .branch_clock_request_o(branch_clock_request),",
            "      .route_enable_o        (route_enable),",
            "      .branch_clock_active_i (branch_clock_active),",
            "      .cluster_gpio_o_i      (cluster_gpio_o),",
            "      .cluster_gpio_oe_i     (cluster_gpio_oe),",
            "      .gpio_o,",
            "      .gpio_oe",
            "  );",
            "",
        ]
    )
    for cluster_index in range(cluster_count):
        tile_module = module_by_signature[cluster_signatures[cluster_index]]
        lines.extend(
            [
                f"  {tile_module} cluster_{cluster_index}_I (",
                "      .ctrl_clk_i,",
                "      .ctrl_rst_n_i,",
                f"      .cluster_clock_i       (cluster_clock[{cluster_index * 2}+:2]),",
                "      .rst_n_i,",
                "      .branch_addr_i         (branch_addr),",
                "      .site_addr_i           (site_addr),",
                f"      .branch_clock_request_i(branch_clock_request[{cluster_index}]),",
                f"      .route_enable_i        (route_enable[{cluster_index}]),",
                "      .gpio_i,",
                f"      .branch_clock_active_o (branch_clock_active[{cluster_index * 2}+:2]),",
                f"      .gpio_o                (cluster_gpio_o[{cluster_index * 12}+:12]),",
                f"      .gpio_oe               (cluster_gpio_oe[{cluster_index * 12}+:12]),",
                "      .VDD,",
                "      .VSS",
                "  );",
                "`ifdef TENON_MINI_VERIFY",
                f"  assign site_enable[{cluster_index * 256}+:256] = cluster_{cluster_index}_I.site_enable;",
                "`endif",
                "",
            ]
        )
    lines.extend(["endmodule", "", "`default_nettype wire", ""])
    return "\n".join(lines)


def generated_files(fabric_path: Path, projects_path: Path) -> dict[str, str]:
    fabric = validate_fabric(read_json(fabric_path))
    projects, reference = load_projects(read_json(projects_path))
    occupied = place_projects(projects, fabric["cluster_count"])
    fill_reference(projects, reference, fabric["cluster_count"], occupied)
    catalog = catalog_sv(fabric, projects)
    marker = "module tenon_mini_top ("
    cluster_rtl, separator, top_rtl = catalog.partition(marker)
    if not separator:
        raise GenerationError("generated catalog is missing tenon_mini_top")
    return {
        "layout.json": layout_json(fabric, projects),
        "address_map.csv": address_csv(projects),
        "tenon_mini_clusters.sv": cluster_rtl + "`default_nettype wire\n",
        "tenon_mini_top.sv": (
            "// Generated by app/mini/tools/generate.py. Do not edit.\n"
            "`default_nettype none\n\n"
            + separator
            + top_rtl
        ),
    }


def write_files(output: Path, files: dict[str, str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, contents in files.items():
        (output / name).write_text(contents)


def check_files(output: Path, files: dict[str, str]) -> None:
    mismatches = []
    for name, contents in files.items():
        path = output / name
        if not path.is_file() or path.read_text() != contents:
            mismatches.append(name)
    if mismatches:
        raise GenerationError(f"generated files are stale: {', '.join(mismatches)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fabric", type=Path, default=DEFAULT_FABRIC)
    parser.add_argument("--projects", type=Path, default=DEFAULT_PROJECTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = generated_files(args.fabric, args.projects)
    if args.check:
        check_files(args.output, files)
    else:
        write_files(args.output, files)
    layout = json.loads(files["layout.json"])
    print(
        f"PASS: {len(layout['projects'])} projects occupy "
        f"{sum(project['units'] for project in layout['projects'])}/"
        f"{layout['total_sites']} sites."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GenerationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
