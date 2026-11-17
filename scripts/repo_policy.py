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

    def parser_contract(source):
        """Interpret only argparse declarations, never import/execute the entry."""
        import argparse
        import contextlib
        import io
        tree = ast.parse(source.read_text(encoding="utf-8"))
        constants = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    if isinstance(node.value, ast.Dict) and all(isinstance(key, ast.Constant) for key in node.value.keys):
                        value = [key.value for key in node.value.keys]
                    else:
                        continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = value
        class InvalidCommand(Exception):
            pass
        class Contract(argparse.ArgumentParser):
            def error(self, message):
                raise InvalidCommand(message)
            def exit(self, status=0, message=None):
                raise InvalidCommand(message or "help-only command")
        def build(function):
            env = dict(constants)
            roots = []
            def literal(node):
                if isinstance(node, ast.Name):
                    return env[node.id]
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sorted"
                        and len(node.args) == 1 and not node.keywords):
                    values = literal(node.args[0])
                    if isinstance(values, (list, tuple, set, dict)):
                        return sorted(values)
                    raise ValueError("sorted choices must contain static values")
                if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                    result = []
                    for item in node.elts:
                        result.extend(literal(item.value) if isinstance(item, ast.Starred) else [literal(item)])
                    return result
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "argparse":
                    return getattr(argparse, node.attr)
                return ast.literal_eval(node)
            def expression(node):
                if isinstance(node, ast.Name):
                    return env.get(node.id)
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    return None
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "argparse" and node.func.attr == "ArgumentParser":
                    value = Contract(add_help=True, allow_abbrev=False)
                    roots.append(value)
                    return value
                owner = expression(node.func.value)
                method = node.func.attr
                if method not in {"add_parser", "add_subparsers", "add_argument"} or owner is None:
                    return None
                args = [literal(arg) for arg in node.args]
                kwargs = {}
                for item in node.keywords:
                    if item.arg in {"action", "nargs", "choices", "dest", "type"}:
                        try:
                            kwargs[item.arg] = {"int": int, "float": float, "str": str}.get(item.value.id, str) if item.arg == "type" and isinstance(item.value, ast.Name) else literal(item.value)
                        except (KeyError, ValueError, TypeError):
                            # Unknown dynamic declarations fail closed when used; no imports/eval.
                            if item.arg == "choices":
                                # An unresolved choices expression must reject any provided value,
                                # while documentation that does not use this option can still parse.
                                kwargs["choices"] = ()
                            else:
                                raise ValueError("Unsupported argparse declaration in " + str(source))
                if method == "add_parser":
                    kwargs.update(allow_abbrev=False)
                # Commands are documented as templates; required values may be omitted.
                # Provided names/options/choices/arity must still parse in their exact scope.
                return getattr(owner, method)(*args, **kwargs)
            def statements(nodes):
                for node in nodes:
                    if isinstance(node, ast.Assign):
                        try:
                            value = expression(node.value)
                            if value is None:
                                value = literal(node.value)
                        except (KeyError, ValueError, TypeError):
                            continue
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                env[target.id] = value
                    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                        expression(node.value)
                    elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                        try:
                            values = literal(node.iter)
                        except (KeyError, ValueError, TypeError):
                            continue
                        if isinstance(values, (list, tuple, set)) and len(values) < 100:
                            for value in values:
                                env[node.target.id] = value
                                statements(node.body)
                    elif isinstance(node, ast.If):
                        test = node.test
                        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Eq, ast.NotEq)):
                            try:
                                equal = literal(test.left) == literal(test.comparators[0])
                            except (KeyError, ValueError, TypeError):
                                continue
                            statements(node.body if equal == isinstance(test.ops[0], ast.Eq) else node.orelse)
            statements(function.body)
            return roots
        roots = [root for function in tree.body if isinstance(function, ast.FunctionDef)
                 for root in build(function)]
        if len(roots) != 1:
            raise ValueError("Expected one statically declared argparse interface in " + str(source))
        parser = roots[0]
        if source.name == "tenon.py":
            subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
            # Stable maintenance forwards argv to this exact local entry.
            subparsers.choices["maintenance"] = parser_contract(source.with_name("maintenance.py"))
        def validate(argv):
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                try:
                    parser.parse_args(argv)
                except InvalidCommand as error:
                    return str(error)
            return None
        # The delegating branch above needs the parser itself, not its validator.
        parser.tenon_validate = validate
        return parser

    def command_error(command):
        try:
            argv = shlex.split(command)
        except ValueError:
            return "invalid command quoting"
        if len(argv) >= 2 and argv[0] == "make":
            return None if argv[1] in targets else "unknown Make target " + argv[1]
        stable = argv[:2] == ["git", "tenon"]
        if stable or (len(argv) >= 2 and argv[0] in {"python", "python3"} and argv[1].endswith(".py")):
            entry = "scripts/tenon.py" if stable else argv[1]
            source = (root / entry).resolve()
            if not source.is_relative_to(root.resolve()) or not source.is_file():
                return "missing/outside Python entry " + entry
            try:
                if source not in contracts:
                    contracts[source] = parser_contract(source)
                problem = contracts[source].tenon_validate(argv[2:])
                if problem:
                    return ("invalid stable command " if stable else "unknown Python command/option ") + problem
            except (ValueError, TypeError, SyntaxError, AttributeError) as error:
                return "unverifiable command interface " + str(error)
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
    from ci_contract import audit_source
    try:
        errors.extend(audit_source(root)["errors"])
    except RuntimeError as error:
        errors.append(str(error))
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
