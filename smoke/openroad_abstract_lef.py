#!/usr/bin/env python3
"""Create a standalone abstract LEF from a completed OpenROAD database."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--odb", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--top", required=True)
    args = parser.parse_args()

    odb = args.odb.resolve()
    output = args.output.resolve()
    if not odb.is_file():
        parser.error(f"OpenDB input does not exist: {odb}")

    output.parent.mkdir(parents=True, exist_ok=True)
    tcl = f"read_db {{{odb}}}\nwrite_abstract_lef {{{output}}}\nexit\n"
    result = subprocess.run(
        ["openroad", "-no_init", "-exit"],
        input=tcl,
        text=True,
        capture_output=True,
        check=False,
    )
    log = output.with_suffix(".log")
    log.write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        print(log.read_text(), file=sys.stderr)
        return result.returncode
    if not output.is_file() or f"MACRO {args.top}" not in output.read_text():
        print(f"OpenROAD did not produce a {args.top} abstract LEF: {output}", file=sys.stderr)
        return 1

    print(f"PASS: wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
