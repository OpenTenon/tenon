#!/usr/bin/env python3
"""Render a compact, tracked summary from standalone ICS55 final metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from design_manifest import load_designs

ROOT = Path(__file__).resolve().parent
RUN_TAG = "ics55-smoke"
DIE_AREA_UNIT_UM2 = 1_000
DIE_AREA_TOLERANCE_UM2 = 0.001


def metric_text(value: object | None, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def stage_metrics(run_root: Path, pattern: str) -> dict[str, object] | None:
    states = sorted(run_root.glob(pattern))
    if not states:
        return None
    metrics = json.loads(states[-1].read_text()).get("metrics")
    return metrics if isinstance(metrics, dict) else None


def layout_units(design: dict[str, object]) -> int | None:
    physical = design.get("physical")
    if not isinstance(physical, dict):
        return None
    units = physical.get("required_layout_units")
    if isinstance(units, int) and units >= 1:
        return units
    return None


def report_row(design: dict[str, object]) -> tuple[str, bool]:
    name = str(design["name"])
    units = layout_units(design)
    run_root = ROOT / name / "runs" / RUN_TAG
    metrics_path = run_root / "final" / "metrics.json"
    post_route = stage_metrics(run_root, "*-openroad-detailedrouting/state_out.json")
    disconnected_metrics = stage_metrics(run_root, "*-checker-disconnectedpins/state_out.json")
    if units is None or not metrics_path.exists() or post_route is None or disconnected_metrics is None:
        return f"| {name} | NOT RUN | " + " | ".join(["-"] * 15) + " |", False

    die_area = post_route.get("design__die__area")
    route_drc = post_route.get("route__drc_errors")
    antenna = post_route.get("antenna__violating__nets")
    psm = post_route.get("design__power_grid_violation__count")
    disconnected = disconnected_metrics.get("design__critical_disconnected_pin__count")
    flow_errors = post_route.get("flow__errors__count")
    expected_die_area = units * DIE_AREA_UNIT_UM2
    checks_passed = (
        flow_errors == 0
        and route_drc == 0
        and antenna == 0
        and psm == 0
        and disconnected == 0
    )
    die_matches_target = (
        isinstance(die_area, (int, float))
        and abs(die_area - expected_die_area) <= DIE_AREA_TOLERANCE_UM2
    )
    passed = checks_passed and die_matches_target
    status = "PASS" if passed else "FAIL"
    die_target = "FIXED" if die_matches_target else "MISMATCH"
    metric_link = metrics_path.relative_to(ROOT).as_posix()
    route_logs = sorted(run_root.glob("*-openroad-detailedrouting/openroad-detailedrouting.log"))
    route_link = route_logs[-1].relative_to(ROOT).as_posix() if route_logs else "-"
    utilization = post_route.get("design__instance__utilization")
    values = (
        name,
        status,
        metric_text(units),
        die_target,
        metric_text(die_area),
        metric_text(die_area / 1_000_000 if isinstance(die_area, (int, float)) else None, 6),
        metric_text(post_route.get("design__core__area")),
        metric_text(post_route.get("design__instance__area")),
        metric_text(utilization * 100 if isinstance(utilization, (int, float)) else None),
        metric_text(post_route.get("design__instance__count__stdcell")),
        metric_text(route_drc),
        metric_text(antenna),
        metric_text(psm),
        metric_text(disconnected),
        metric_text(post_route.get("timing__setup__ws")),
        f"[metrics]({metric_link})",
        f"[route]({route_link})" if route_link != "-" else "-",
    )
    return "| " + " | ".join(values) + " |", passed


def render(selected: set[str] | None = None) -> tuple[str, bool]:
    designs = load_designs(selected)
    rows = [report_row(design) for design in designs]
    content = [
        "# ICS55 Smoke Reports",
        "",
        "All results are non-signoff evaluations. Magic DRC, KLayout DRC, Magic",
        "Spice extraction, and Netgen LVS are skipped. Route DRC, OpenROAD",
        "antenna, PSM, and critical disconnected-pin results are read from the",
        "corresponding completed flow stages. Every die uses an integer number of",
        "25 um x 40 um layout units; actual utilization is sampled after detailed",
        "routing and before filler insertion.",
        "",
        "| Design | Status | Units | Die target | Die (um2) | Die (mm2) | Core (um2) | Cell (um2) | Actual util (%) | Std cells | Route DRC | OpenROAD antenna | PSM | Critical disconnected | Setup WNS | Metrics | Route report |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        *(row for row, _ in rows),
        "",
        f"Acceptance requires die area = Units * {DIE_AREA_UNIT_UM2} um2 within {DIE_AREA_TOLERANCE_UM2} um2,",
        "zero flow errors, zero route DRC, zero OpenROAD antenna violations, zero PSM",
        "violations, and zero critical disconnected pins.",
    ]
    return "\n".join(content) + "\n", all(passed for _, passed in rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--design", action="append")
    args = parser.parse_args()
    content, passed = render(set(args.design) if args.design else None)
    if args.write:
        (ROOT / "REPORTS.md").write_text(content)
    else:
        print(content, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
