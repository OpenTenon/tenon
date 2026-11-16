#!/usr/bin/env python3
"""Compile existing fixed package tops with CI models; this is not PDK validation."""
from __future__ import annotations
import json
import os
import sys
from engineering import make_values
from engineering_common import ROOT, execute, finish_summary, new_summary


def configurations(root=ROOT):
    values = make_values(root)
    profiles = json.loads((root / "specs/tier0_profiles.json").read_text(encoding="utf-8"))["profiles"]
    for prefix, model, stem, variants in (
        ("IHP", "CI_IHP_IO_MODEL", "tenon_tier0_", ("",)),
        ("SKY130", "CI_SKY130_IO_MODEL", "tenon_tier0_sky130_", ("",)),
        ("GF180", "CI_GF180_IO_MODEL", "tenon_tier0_gf180_", ("",)),
        ("ICS55", "CI_ICS55_IO_MODEL", "tenon_tier0_ics55_", ("_no_pll", "_pll")),
    ):
        for profile in profiles:
            for variant in variants:
                top = stem + profile["id"] + variant
                yield top, [os.environ.get("IVERILOG", "iverilog"), "-g2012", "-tnull", "-s", top,
                            values[model], *values[prefix + "_RTL"].split()]


def main():
    summary = new_summary(ROOT, "ci-adapter-compilation")
    directory = ROOT / "build/verification" / summary["run_id"]
    directory.mkdir(parents=True, exist_ok=False)
    for top, command in configurations():
        item = execute(command, ROOT, directory / (top + ".log"), check_id=top,
                       coverage={"kind": "elaboration-with-ci-model", "top": top})
        summary["checks"].append(item)
    finish_summary(summary, ROOT, directory / "summary.json")
    print(f"{summary['status']}: {len(summary['checks']) - 1} fixed tops compiled with CI models")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
