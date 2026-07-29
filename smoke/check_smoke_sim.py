#!/usr/bin/env python3
"""Run behavioral simulations for smoke designs that provide a testbench."""

from __future__ import annotations

import subprocess
from pathlib import Path

from design_manifest import load_designs

ROOT = Path(__file__).resolve().parent
BUILD = ROOT.parent / "build" / "smoke"


def main() -> int:
    designs = load_designs()
    simulated = 0
    BUILD.mkdir(parents=True, exist_ok=True)
    for design in designs:
        testbench = design.get("testbench")
        if testbench is None:
            continue
        design_dir = ROOT / design["name"]
        output = BUILD / f"{design['name']}.vvp"
        compile_command = [
            "iverilog",
            "-g2012",
            "-s",
            f"{design['top']}_tb",
            "-o",
            str(output),
            str(design_dir / design["rtl"]),
            str(design_dir / testbench),
        ]
        print("+", " ".join(compile_command))
        subprocess.run(compile_command, check=True)
        run_command = ["vvp", str(output)]
        print("+", " ".join(run_command))
        subprocess.run(run_command, check=True)
        simulated += 1

    print(f"PASS: simulated {simulated} ICS55 smoke demonstration tops.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
