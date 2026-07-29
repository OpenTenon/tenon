#!/usr/bin/env python3
"""Compile each standalone smoke RTL top without requiring a PDK."""

from __future__ import annotations

import subprocess
from pathlib import Path

from design_manifest import load_designs

ROOT = Path(__file__).resolve().parent


def main() -> int:
    designs = load_designs()
    for design in designs:
        source = ROOT / design["name"] / design["rtl"]
        command = [
            "iverilog",
            "-g2012",
            "-tnull",
            "-s",
            design["top"],
            str(source),
        ]
        print("+", " ".join(command))
        subprocess.run(command, check=True)

    print(f"PASS: compiled {len(designs)} ICS55 smoke RTL tops.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
