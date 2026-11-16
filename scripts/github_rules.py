#!/usr/bin/env python3
"""Review or audit branch protections; writes require an explicit separate --apply action."""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from pathlib import Path
from engineering_common import ROOT, git


def api(repository: str, endpoint: str, body: Path | None = None):
    command = ["gh", "api", f"repos/{repository}/{endpoint}", "-H", "Accept: application/vnd.github+json"]
    if body:
        command.extend(["--method", "PUT", "--input", str(body)])
    return json.loads(subprocess.check_output(command, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60))


def normalize(observed: dict) -> dict:
    value = dict(observed)
    for name in ("enforce_admins", "allow_force_pushes", "allow_deletions", "required_conversation_resolution"):
        if isinstance(value.get(name), dict):
            value[name] = value[name].get("enabled")
    if isinstance(value.get("restrictions"), dict):
        value["restrictions"] = {key: [item.get("login", item.get("slug")) if isinstance(item, dict) else item
                                      for item in items]
                                 for key, items in value["restrictions"].items() if key in {"users", "teams", "apps"}}
    return value


def differences(expected, actual, path: str) -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [path + " is missing or has a different type"]
        return [error for key, value in expected.items() for error in differences(value, actual.get(key), path + "." + key)]
    if isinstance(expected, list):
        return [] if isinstance(actual, list) and sorted(expected) == sorted(actual) else [path + " differs"]
    return [] if expected == actual else [path + " differs"]


def audit(repository: str, root: Path = ROOT, ref: str | None = None, query=None) -> dict:
    query = query or api
    commit = git(root, "rev-parse", "--verify", ref + "^{commit}").decode().strip() if ref else None
    errors, observed = [], {}
    for branch in ("dev", "main"):
        name = ".github/branch-protection/" + branch + ".json"
        text = git(root, "show", commit + ":" + name).decode() if commit else (root / name).read_text(encoding="utf-8")
        expected = json.loads(text)
        observed[branch] = query(repository, "branches/" + branch + "/protection")
        errors.extend(differences(expected, normalize(observed[branch]), branch))
    metadata = query(repository, "")
    if metadata.get("delete_branch_on_merge") is not False:
        errors.append("Repository branch deletion must wait for the post-merge CI/archive controller")
    if metadata.get("allow_merge_commit") is not True:
        errors.append("Repository must support the controller's normal merge commits")
    return {"schema_version": 1, "kind": "branch-rule-audit", "repository": repository,
            "source_commit": commit, "status": "FAIL" if errors else "PASS", "errors": errors, "observed": observed,
            "repository_settings": {name: metadata.get(name) for name in ("delete_branch_on_merge", "allow_merge_commit")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="OpenTenon/tenon")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ref", help="Read expected rules from an immutable Git ref during audit")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audit", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-repository")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("Invalid repository identifier")
    templates = {branch: args.root / ".github/branch-protection" / (branch + ".json") for branch in ("dev", "main")}
    if args.audit:
        try:
            result = audit(args.repository, args.root.resolve(), args.ref)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result = {"status": "BLOCKED", "reason": str(error)}
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "PASS" else 2
    if not args.audit and not args.apply:
        print(json.dumps({branch:json.loads(path.read_text(encoding="utf-8")) for branch,path in templates.items()}, indent=2))
        return 0
    if args.apply:
        if args.ref:
            parser.error("Apply uses reviewed working-tree payloads; --ref is read-only")
        if args.confirm_repository != args.repository:
            parser.error("--apply requires an explicit matching --confirm-repository")
        # Applying before ci-required exists would block merges. Check the published dev workflow first.
        branch = api(args.repository, "branches/dev")
        runs = api(args.repository, f"commits/{branch['commit']['sha']}/check-runs")
        if not any(run["name"] == "ci-required" and run.get("conclusion") == "success" for run in runs.get("check_runs", [])):
            raise SystemExit("BLOCKED: publish and pass ci-required on dev before applying branch protections")
    results = {}
    for branch,path in templates.items():
        results[branch] = api(args.repository, f"branches/{branch}/protection", path if args.apply else None)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
