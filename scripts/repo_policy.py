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
    import ast
    import shlex
    from skill_eval import load_cases
    errors = []
    found = set()
    make = (root / "Makefile").read_text(encoding="utf-8")
    targets = set(re.findall(r"^([A-Za-z][\w-]*)\s*:", make, re.M))
    contracts = {}

    def command_error(command):
        try:
            argv = shlex.split(command)
        except ValueError:
            return "invalid command quoting"
        if len(argv) >= 2 and argv[0] == "make":
            return None if argv[1] in targets else "unknown Make target " + argv[1]
        if len(argv) >= 2 and argv[0] in {"python", "python3"} and argv[1].endswith(".py"):
            source = (root / argv[1]).resolve()
            if not source.is_relative_to(root.resolve()) or not source.is_file():
                return "missing/outside Python entry " + argv[1]
            if source not in contracts:
                tree = ast.parse(source.read_text(encoding="utf-8"))
                names = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        if node.func.attr in {"add_parser", "add_argument"}:
                            names.update(arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
                            for option in node.keywords:
                                if option.arg == "choices" and isinstance(option.value, (ast.List, ast.Tuple, ast.Set)):
                                    names.update(item.value for item in option.value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str))
                    if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                            and isinstance(node.iter, (ast.List, ast.Tuple, ast.Set))
                            and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.iter.elts)):
                        for call in (child for statement in node.body for child in ast.walk(statement)):
                            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                                    and call.func.attr in {"add_parser", "add_argument"} and call.args
                                    and isinstance(call.args[0], ast.Name) and call.args[0].id == node.target.id):
                                names.update(item.value for item in node.iter.elts)
                contracts[source] = names
            if len(argv) >= 3 and argv[2] not in contracts[source]:
                return "unknown Python command/option " + argv[2]
        return None

    for path in sorted((root / ".agents/skills").glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
        if not match:
            errors.append(f"{path}: missing skill frontmatter")
            continue
        fields = re.findall(r"^(name|description):\s*(.+)$", match[1], re.M)
        metadata = dict(fields)
        name = metadata.get("name", "")
        if name != path.parent.name or name in found or not metadata.get("description") or len(fields) != 2:
            errors.append(f"{path}: invalid or duplicate name/description")
        found.add(name)
        for document in [path, *sorted((path.parent / "references").rglob("*.md"))]:
            body = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", body):
                if "://" in target:
                    continue
                link, _, anchor = target.partition("#")
                destination = (document.parent / link).resolve()
                if not destination.is_relative_to(root.resolve()) or not destination.is_file():
                    errors.append(f"{document}: missing/outside reference {target}")
                elif anchor:
                    headings = re.findall(r"^#{1,6}\s+(.+)$", destination.read_text(encoding="utf-8"), re.M)
                    slugs = {re.sub(r"[^\w\s-]", "", heading.lower()).replace(" ", "-") for heading in headings}
                    if anchor not in slugs:
                        errors.append(f"{document}: missing reference anchor {target}")
            for command in re.findall(r"`([^`\n]+)`", body):
                error = command_error(command)
                if error:
                    errors.append(f"{document}: {error}")
    if found != SKILLS:
        errors.append(f"Expected Skills {sorted(SKILLS)}, found {sorted(found)}")
    try:
        load_cases(root)
    except (ValueError, KeyError, OSError) as error:
        errors.append("Invalid Skill evaluation cases: " + str(error))
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
