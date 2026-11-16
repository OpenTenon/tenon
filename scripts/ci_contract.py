#!/usr/bin/env python3
"""Read-only consistency checks for workflow, controller gates, branch rules and tool pins."""
from __future__ import annotations
import argparse
import ast
import json
import re
import subprocess
from pathlib import Path
from ci_gate import REQUIRED_JOBS
from engineering_common import ROOT, git

MINIMUM_TARGETS = {
    "format": {"format-check"},
    "check": {"check", "tools-test", "policy-check", "workflow-check"},
    "sim": {"sim", "adapter-compile"},
    "tools": {"tools-test", "skill-check"},
}


def literal(text: str, name: str):
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise ValueError("Invalid controller source: " + str(error)) from error
    values = [node.value for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
              and any(isinstance(target, ast.Name) and target.id == name for target in
                      (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    if len(values) != 1:
        raise ValueError("Expected one literal controller declaration: " + name)
    try:
        return ast.literal_eval(values[0])
    except (ValueError, TypeError) as error:
        raise ValueError("Controller declaration must remain literal: " + name) from error


def check_data(workflow: dict, templates: dict, pins: dict, required_jobs=None, verification_groups=None) -> list[str]:
    errors = []
    if not isinstance(workflow, dict):
        return ["Workflow must be a mapping"]
    required_jobs = set(REQUIRED_JOBS if required_jobs is None else required_jobs)
    targets = verification_groups if verification_groups is not None else MINIMUM_TARGETS
    if not isinstance(targets, dict):
        return ["Verifier groups must declare their actual target coverage"]
    for group, required in MINIMUM_TARGETS.items():
        declared = targets.get(group, [])
        if not isinstance(declared, (list, set, tuple)) or not required.issubset(set(declared)):
            errors.append("Verifier group omits mandatory checks: " + group)
    verification_groups = set(targets)
    jobs = workflow.get("jobs", {})
    if set(jobs) != required_jobs | {"required"}:
        errors.append("Workflow jobs differ from the controller's required inventory")
    gate = jobs.get("required", {})
    needs = gate.get("needs", [])
    if gate.get("name") != "ci-required" or not isinstance(needs, list) or set(needs) != required_jobs:
        errors.append("ci-required does not aggregate every required job")
    condition = str(gate.get("if", "")).replace("${{", "").replace("}}", "").strip()
    if condition != "always()":
        errors.append("ci-required must run even when a prerequisite fails or is skipped")
    if workflow.get("permissions") != {"contents": "read"}:
        errors.append("Fast CI must keep its read-only repository token")
    events = workflow.get("on", workflow.get(True, {}))
    if not isinstance(events, dict) or set(events) != {"push", "pull_request", "workflow_dispatch"}:
        errors.append("CI triggers differ from the approved PR/push/manual contract")
    else:
        for event in ("push", "pull_request"):
            value = events[event] or {}
            if set(value.get("branches", [])) != {"dev", "main"} or any(key.startswith("paths") for key in value):
                errors.append("Fast CI must cover all dev/main changes without path filtering")
    dispatched = set()
    for name, job in jobs.items():
        if "permissions" in job and job["permissions"] not in ({"contents": "read"}, {}):
            errors.append("Job token overrides the read-only CI invariant: " + name)
        if job.get("continue-on-error") or (name != "required" and "if" in job):
            errors.append("Required job may be conditionally bypassed: " + name)
        limit = job.get("timeout-minutes")
        if not isinstance(limit, int) or not 0 < limit <= 30:
            errors.append("Missing or unbounded job timeout: " + name)
        if name != "tools-windows" and job.get("runs-on") != pins.get("linux_runner"):
            errors.append("Linux runner differs from the toolchain contract: " + name)
        for step in job.get("steps", []):
            action = step.get("uses")
            if action and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}", action):
                errors.append("Action is not pinned to an immutable commit: " + action)
        unconditional = [str(step.get("run", "")).strip() for step in job.get("steps", [])
                         if "if" not in step and not step.get("continue-on-error")]
        if name == "check" and "python3 scripts/ci_contract.py --root ." not in unconditional:
            errors.append("The source contract needs an independent unconditional CI audit step")
        if name == "required":
            if not any(command in {"python3 scripts/ci_gate.py", "python scripts/ci_gate.py"} for command in unconditional):
                errors.append("ci-required does not dispatch its fail-closed gate")
        else:
            groups = [match[1] for command in unconditional
                      if (match := re.fullmatch(r"python(?:3)? scripts/engineering\.py verify --group ([a-z_-]+)", command))]
            if not groups or any(group not in verification_groups for group in groups):
                errors.append("Required job does not dispatch a known verifier group: " + name)
            dispatched.update(groups)
    if dispatched != verification_groups:
        errors.append("CI dispatch does not cover every verifier group")
    for branch in ("dev", "main"):
        rule = templates.get(branch, {})
        checks = rule.get("required_status_checks", {})
        if checks.get("strict") is not True or checks.get("contexts") != ["ci-required"]:
            errors.append("Branch checks differ from ci-required/current-base policy: " + branch)
        if (rule.get("enforce_admins") is not True or rule.get("allow_force_pushes") is not False
                or rule.get("allow_deletions") is not False or rule.get("required_conversation_resolution") is not True):
            errors.append("Branch protection invariants differ: " + branch)
    main = templates.get("main", {}).get("restrictions") or {}
    if not main.get("users") or main.get("apps") or main.get("teams"):
        errors.append("Main must remain restricted to its named human maintainer")
    for name in ("verible", "actionlint_archive"):
        pin = pins.get(name, {})
        if not re.fullmatch(r"[0-9a-f]{64}", pin.get("sha256", "")) or not pin.get("url", "").startswith("https://"):
            errors.append("Missing HTTPS/hash-pinned tool archive: " + name)
    for name, version in pins.get("python_packages", {}).items():
        if not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.+_-]*", version):
            errors.append("Python tool must have an exact version: " + name)
    return errors


def audit_source(root: Path = ROOT, ref: str | None = None) -> dict:
    try:
        import yaml
    except ImportError as error:
        from host_profile import activate_python
        activate_python(root)
        try:
            import yaml
        except ImportError:
            raise RuntimeError("The existing workflow parser PyYAML is required for source auditing") from error
    commit = git(root, "rev-parse", "--verify", (ref or "HEAD") + "^{commit}").decode().strip()
    def read(name):
        return git(root, "show", commit + ":" + name).decode("utf-8") if ref else (root / name).read_text(encoding="utf-8")
    try:
        workflow = yaml.safe_load(read(".github/workflows/ci.yml"))
    except yaml.YAMLError as error:
        raise ValueError("Invalid workflow YAML: " + str(error)) from error
    templates = {name: json.loads(read(".github/branch-protection/" + name + ".json")) for name in ("dev", "main")}
    pins = json.loads(read("tools/toolchain.json"))
    required_jobs = literal(read("scripts/ci_gate.py"), "REQUIRED_JOBS")
    groups = literal(read("scripts/engineering.py"), "GROUP_TARGETS")
    if (not isinstance(required_jobs, set) or not required_jobs or not all(isinstance(item, str) for item in required_jobs)
            or not isinstance(groups, dict)):
        raise ValueError("Controller/verifier inventory types differ")
    errors = check_data(workflow, templates, pins, required_jobs, groups)
    return {"schema_version": 1, "kind": "ci-contract", "commit": commit,
            "source": "committed-ref" if ref else "working-tree", "status": "FAIL" if errors else "PASS",
            "required_jobs": sorted(required_jobs | {"ci-required"}), "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ref")
    args = parser.parse_args()
    try:
        result = audit_source(args.root.resolve(), args.ref)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result = {"status": "BLOCKED", "reason": str(error)}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
