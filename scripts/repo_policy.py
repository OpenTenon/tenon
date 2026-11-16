#!/usr/bin/env python3
"""Check deterministic repository boundaries; never claim electrical or physical signoff."""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from engineering_common import ROOT, git

SKILLS = {"tenon-task", "tenon-verify", "tenon-generated", "tenon-physical", "tenon-release"}


def check_skills(root=ROOT):
    errors = []
    found = set()
    for path in sorted((root / ".agents/skills").glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
        if not match:
            errors.append(f"{path}: missing skill frontmatter")
            continue
        metadata = dict(re.findall(r"^(name|description):\s*(.+)$", match[1], re.M))
        name = metadata.get("name", "")
        if name != path.parent.name or name in found or not metadata.get("description"):
            errors.append(f"{path}: invalid or duplicate name/description")
        found.add(name)
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if "://" not in target and not (path.parent / target.split("#")[0]).is_file():
                errors.append(f"{path}: missing reference {target}")
    if found != SKILLS:
        errors.append(f"Expected Skills {sorted(SKILLS)}, found {sorted(found)}")
    return errors


def check_policy(root=ROOT):
    errors = []
    for name in ("tier0_profiles.json", "tier0_pdks.json"):
        data = json.loads((root / "specs" / name).read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            errors.append(f"{name}: unsupported schema; a reviewed migration is required")
    inventory = json.loads(subprocess.check_output(
        [sys.executable, str(root / "tools/generate_tier0.py"), "--list-outputs"], cwd=root))
    if len(inventory) != len(set(inventory)):
        errors.append("Generated inventory contains duplicate paths")
    for name in inventory:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append(f"Invalid/missing generated output: {name}")
            continue
        if name.endswith(".yaml") and re.search(r"(?:tb[/\\]ci_|ci_\w+_stub\.sv)", path.read_text(encoding="utf-8")):
            errors.append(f"CI behavioral model entered a synthesis configuration: {name}")
    for raw in git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8")
        path = root / name
        if path.is_file() and path.suffix in {".sv", ".v"} and path.parts[len(root.parts)] in {"rtl", "tb", "smoke"}:
            text = path.read_text(encoding="utf-8")
            nettype = "wire"
            for line in text.splitlines():
                directive = re.match(r"\s*`default_nettype\s+(\w+)", line)
                if directive:
                    nettype = directive[1]
                if re.match(r"\s*module\s", line) and nettype != "none":
                    errors.append(f"Module starts outside default_nettype none: {name}")
        if path.is_file() and name.startswith("smoke/") and path.name == "config.json":
            config = json.loads(path.read_text(encoding="utf-8"))
            if any("ci_" in str(item) or "/tb/" in str(item) for item in config.get("VERILOG_FILES", [])):
                errors.append(f"CI model entered a smoke synthesis configuration: {name}")
    make = (root / "Makefile").read_text(encoding="utf-8")
    for line in make.splitlines():
        if "$(LIBRELANE)" in line and "--overwrite" in line:
            errors.append("Physical Make recipes must not overwrite existing native runs")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-only", action="store_true")
    args = parser.parse_args()
    errors = check_skills() if args.skills_only else check_policy() + check_skills()
    for error in errors:
        print(error, file=sys.stderr)
    print(f"{'FAIL' if errors else 'PASS'}: repository engineering boundaries")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
