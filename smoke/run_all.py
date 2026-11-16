#!/usr/bin/env python3
"""Run all standalone ICS55 smoke hardenings serially and render final metrics."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from design_manifest import load_designs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from physical import PhysicalRun

ROOT = Path(__file__).resolve().parent
RUN_TAG = "ics55-smoke"
ABSTRACT_LEF = ROOT / "openroad_abstract_lef.py"
# No automatic DRC/extraction/LVS exceptions: AGENTS.md is authoritative.
SKIPPED_STEPS: tuple[str, ...] = ()


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


def generate_abstract_lef(design_dir: Path, top: str, session: PhysicalRun) -> Path:
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
    if not session.command(command, cwd=design_dir, check_id="abstract-lef"):
        raise RuntimeError("Abstract LEF generation failed; see execution evidence")
    return lef


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdk-root",
        default=os.environ.get("ICS55_PDK_ROOT", str(Path.home() / ".ciel" / "manual")),
    )
    parser.add_argument("--design", action="append")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=21600)
    args = parser.parse_args()
    os.environ["ICS55_PDK_ROOT"] = args.pdk_root

    designs = load_designs(args.design)
    failures: list[str] = []

    for design in designs:
        design_dir = ROOT / design["name"]
        command = fresh_command(args.pdk_root)
        print("+", " ".join(command), f"(cwd={design_dir})")
        if args.dry_run:
            continue
        try:
            with PhysicalRun(ROOT.parent, "ics55", design_dir / "config.json", RUN_TAG, args.timeout) as session:
                if not session.command(command, cwd=design_dir, check_id="initial-flow"):
                    failures.append(design["name"])
                    continue
                lef = generate_abstract_lef(design_dir, design["top"], session)
                state = render_state(design_dir)
                command = resume_command(args.pdk_root, state, lef)
                if not session.command(command, cwd=design_dir, check_id="resume-flow"):
                    failures.append(design["name"])
            if session.summary["status"] != "PASS":
                failures.append(design["name"])
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            print(error, file=sys.stderr)
            failures.append(design["name"])

    if not args.dry_run:
        report_command = [sys.executable, str(ROOT / "report_metrics.py"), "--output", str(ROOT.parent / "build" / "smoke" / "REPORTS.md")]
        for design in args.design or []:
            report_command.extend(("--design", design))
        report = subprocess.run(report_command)
        if report.returncode != 0:
            failures.append("metrics-report")

    if failures:
        print("FAILED:", ", ".join(failures), file=sys.stderr)
        return 1
    print("NOT_RUN: commands planned; no checks executed." if args.dry_run else "PASS: all selected ICS55 smoke flows completed (non-signoff).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
