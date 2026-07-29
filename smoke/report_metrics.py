#!/usr/bin/env python3
"""Render a compact, tracked summary from standalone ICS55 final metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN_TAG = "ics55-smoke"
DIE_AREA_LIMIT_UM2 = 1_000


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


def report_row(design: dict[str, str]) -> tuple[str, bool]:
    name = design["name"]
    run_root = ROOT / name / "runs" / RUN_TAG
    metrics_path = run_root / "final" / "metrics.json"
    post_route = stage_metrics(run_root, "*-openroad-detailedrouting/state_out.json")
    disconnected_metrics = stage_metrics(run_root, "*-checker-disconnectedpins/state_out.json")
    if not metrics_path.exists() or post_route is None or disconnected_metrics is None:
        return f"| {name} | NOT RUN | " + " | ".join(["-"] * 15) + " |", False

    config = json.loads((ROOT / name / "config.json").read_text())
    die_area = post_route.get("design__die__area")
    route_drc = post_route.get("route__drc_errors")
    antenna = post_route.get("antenna__violating__nets")
    psm = post_route.get("design__power_grid_violation__count")
    disconnected = disconnected_metrics.get("design__critical_disconnected_pin__count")
    flow_errors = post_route.get("flow__errors__count")
    checks_passed = (
        flow_errors == 0
        and route_drc == 0
        and antenna == 0
        and psm == 0
        and disconnected == 0
    )
    under_budget = isinstance(die_area, (int, float)) and die_area < DIE_AREA_LIMIT_UM2
    passed = checks_passed and under_budget
    status = "PASS" if passed else "FAIL"
    budget = "UNDER" if under_budget else "EXCEEDS"
    metric_link = metrics_path.relative_to(ROOT).as_posix()
    route_logs = sorted(run_root.glob("*-openroad-detailedrouting/openroad-detailedrouting.log"))
    route_link = route_logs[-1].relative_to(ROOT).as_posix() if route_logs else "-"
    utilization = post_route.get("design__instance__utilization")
    values = (
        name,
        status,
        budget,
        metric_text(die_area),
        metric_text(die_area / 1_000_000 if isinstance(die_area, (int, float)) else None, 6),
        metric_text(post_route.get("design__core__area")),
        metric_text(post_route.get("design__instance__area")),
        metric_text(utilization * 100 if isinstance(utilization, (int, float)) else None),
        metric_text(float(config["FP_CORE_UTIL"]) if "FP_CORE_UTIL" in config else None),
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
    designs = json.loads((ROOT / "manifest.json").read_text())["designs"]
    if selected is not None:
        designs = [design for design in designs if design["name"] in selected]
    rows = [report_row(design) for design in designs]
    content = [
        "# ICS55 Smoke Reports",
        "",
        "All results are non-signoff evaluations. Magic DRC, KLayout DRC, Magic",
        "Spice extraction, and Netgen LVS are skipped. Route DRC, OpenROAD",
        "antenna, PSM, and critical disconnected-pin results are read from the",
        "corresponding completed flow stages. Actual utilization is sampled after",
        "detailed routing and before filler insertion.",
        "",
        "| Design | Status | Budget | Die (um2) | Die (mm2) | Core (um2) | Cell (um2) | Actual util (%) | Target util (%) | Std cells | Route DRC | OpenROAD antenna | PSM | Critical disconnected | Setup WNS | Metrics | Route report |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        *(row for row, _ in rows),
        "",
        f"Acceptance requires die area < {DIE_AREA_LIMIT_UM2} um2, zero flow errors,",
        "zero route DRC, zero OpenROAD antenna violations, zero PSM violations, and",
        "zero critical disconnected pins.",
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
