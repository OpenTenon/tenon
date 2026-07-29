#!/usr/bin/env python3
"""Run all standalone ICS55 smoke hardenings serially and render final metrics."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN_TAG = "ics55-smoke"
ABSTRACT_LEF = ROOT / "openroad_abstract_lef.py"
SKIPPED_STEPS = (
    "Magic.DRC",
    "KLayout.DRC",
    "Magic.SpiceExtraction",
    "Netgen.LVS",
)


def base_command(pdk_root: str) -> list[str]:
    command = [
        "librelane",
        "--manual-pdk",
        "--pdk",
        "ics55",
        "--pdk-root",
        pdk_root,
        "--scl",
        "ics55_LLSC_H7CR",
    ]
    for step in SKIPPED_STEPS:
        command.extend(("--skip", step))
    command.extend(("--override-config", "DRT_OPT_ITERS=8"))
    return command


def fresh_command(pdk_root: str) -> list[str]:
    return base_command(pdk_root) + [
        "--to",
        "KLayout.Render",
        "--run-tag",
        RUN_TAG,
        "--overwrite",
        "config.json",
    ]


def resume_command(pdk_root: str, state: Path, lef: Path) -> list[str]:
    return base_command(pdk_root) + [
        "--from",
        "Odb.CheckDesignAntennaProperties",
        "--last-run",
        "--with-initial-state",
        str(state),
        "--initial-state-element-override",
        f"LEF={lef}",
        "config.json",
    ]


def render_state(design_dir: Path) -> Path:
    states = sorted((design_dir / "runs" / RUN_TAG).glob("*-klayout-render/state_out.json"))
    if not states:
        raise FileNotFoundError("KLayout render state was not produced")
    return states[-1]


def merge_metrics(state: Path, design_dir: Path) -> None:
    state_metrics = json.loads(state.read_text())["metrics"]
    metrics_path = design_dir / "runs" / RUN_TAG / "final" / "metrics.json"
    final_metrics = json.loads(metrics_path.read_text())
    metrics_path.write_text(json.dumps({**state_metrics, **final_metrics}, indent=2) + "\n")


def generate_abstract_lef(design_dir: Path, top: str) -> Path:
    run_dir = design_dir / "runs" / RUN_TAG
    odb = run_dir / "final" / "odb" / f"{top}.odb"
    lef = run_dir / "openroad-abstract-lef" / f"{top}.lef"
    command = [
        sys.executable,
        str(ABSTRACT_LEF),
        "--odb",
        str(odb),
        "--output",
        str(lef),
        "--top",
        top,
    ]
    print("+", " ".join(command))
    subprocess.run(command, check=True, cwd=design_dir)
    return lef


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdk-root",
        default=os.environ.get("ICS55_PDK_ROOT", str(Path.home() / ".ciel" / "manual")),
    )
    parser.add_argument("--design", action="append")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    designs = json.loads((ROOT / "manifest.json").read_text())["designs"]
    selected = set(args.design) if args.design else None
    failures: list[str] = []

    for design in designs:
        if selected is not None and design["name"] not in selected:
            continue
        design_dir = ROOT / design["name"]
        command = fresh_command(args.pdk_root)
        print("+", " ".join(command), f"(cwd={design_dir})")
        if args.dry_run:
            continue
        result = subprocess.run(command, cwd=design_dir)
        if result.returncode != 0:
            failures.append(design["name"])
            continue
        try:
            lef = generate_abstract_lef(design_dir, design["top"])
            state = render_state(design_dir)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            print(error, file=sys.stderr)
            failures.append(design["name"])
            continue
        command = resume_command(args.pdk_root, state, lef)
        print("+", " ".join(command), f"(cwd={design_dir})")
        result = subprocess.run(command, cwd=design_dir)
        if result.returncode != 0:
            failures.append(design["name"])
        else:
            merge_metrics(state, design_dir)

    if not args.dry_run:
        report_command = [sys.executable, str(ROOT / "report_metrics.py"), "--write"]
        for design in args.design or []:
            report_command.extend(("--design", design))
        report = subprocess.run(report_command)
        if report.returncode != 0:
            failures.append("metrics-report")

    if failures:
        print("FAILED:", ", ".join(failures), file=sys.stderr)
        return 1
    print("PASS: all selected ICS55 smoke flows completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
