#!/usr/bin/env python3
"""Compile each standalone smoke RTL top without requiring a PDK."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    designs = json.loads((ROOT / "manifest.json").read_text())["designs"]
    for design in designs:
        source = ROOT / design["name"] / design["source"]
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
