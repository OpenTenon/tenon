#!/usr/bin/env python3
"""Local task contracts, evidence-backed handoffs, metrics and non-destructive cleanup checks."""
from __future__ import annotations
import argparse
import fnmatch
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from engineering import required_ids
from engineering_common import ROOT, digest, git, source_state, utc_now, validate_evidence, write_json


def task_path(identifier: str, root: Path = ROOT) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", identifier):
        raise ValueError("Task ID must be a short path-safe identifier")
    return root / "build/tasks" / (identifier + ".json")


def changed_paths(root: Path, base: str) -> list[str]:
    changed = set(git(root, "diff", "--name-only", "-z", base, "--").split(b"\0"))
    changed.update(git(root, "ls-files", "-z", "--others", "--exclude-standard").split(b"\0"))
    return sorted(item.decode("utf-8") for item in changed if item)


def scope_errors(task: dict, root: Path) -> list[str]:
    return [name for name in changed_paths(root, task["base_sha"])
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in task["allowed_paths"])]


def start(identifier: str, goal: str, allowed: list[str], checks: list[str], dependencies: list[str], root: Path = ROOT) -> dict:
    state = source_state(root)
    if not state["branch"].startswith("dev-"):
        raise ValueError("Start from a dev-<task-id>-<topic> branch created from dev")
    base = git(root, "rev-parse", "origin/dev").decode().strip()
    subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", base, "HEAD"], check=True)
    if not allowed or not checks or any(Path(item).is_absolute() or ".." in item.replace("\\", "/").split("/") for item in allowed):
        raise ValueError("Provide repository-relative allowed paths and acceptance commands")
    path = task_path(identifier, root)
    if path.exists():
        raise FileExistsError("Task already exists; resume with status/handoff")
    value = {"schema_version": 1, "task_id": identifier, "goal": goal, "base_ref": "origin/dev",
             "base_sha": base, "branch": state["branch"], "worktree": str(root), "started_at": utc_now(),
             "allowed_paths": allowed, "checks": checks, "dependencies": dependencies,
             "integration_target": "dev", "main_merge_operator": "human", "status": "ACTIVE"}
    write_json(path, value)
    return value


def handoff(identifier: str, evidence: list[Path], remaining: list[str], interventions: int | None,
            rework: int | None, root: Path = ROOT) -> dict:
    path = task_path(identifier, root)
    value = json.loads(path.read_text(encoding="utf-8"))
    errors = scope_errors(value, root)
    checks = set()
    reports = []
    for report in evidence:
        try:
            result = validate_evidence(report, root)
            if result.get("kind") == "verification":
                checks.update(item["id"] for item in result["checks"] if item.get("status") == "PASS")
            reports.append({"path": str(report.resolve()), "sha256": digest(report), "status": result["status"]})
        except (ValueError, KeyError, OSError) as error:
            errors.append(str(error))
    if not required_ids("all").issubset(checks):
        errors.append("Full fast verification evidence is missing")
    value.update(status="READY" if not errors and not remaining else "BLOCKED", completed_at=utc_now(),
                 changed_files=changed_paths(root, value["base_sha"]), source=source_state(root),
                 evidence=reports, remaining=remaining, blockers=errors,
                 human_interventions=interventions, rework_count=rework)
    write_json(path, value)
    return value


def cleanup_check(root: Path = ROOT) -> list[str]:
    errors = []
    if source_state(root)["dirty"]:
        errors.append("Uncommitted/untracked source changes remain")
    result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", "HEAD", "origin/dev"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        errors.append("HEAD contains work not integrated into dev")
    if (root / "build/locks/physical.lock").exists():
        errors.append("Physical execution lease remains; inspect its owner before cleanup")
    for path in (root / "build/tasks").glob("*.json"):
        if json.loads(path.read_text(encoding="utf-8")).get("status") == "ACTIVE":
            errors.append("Active task: " + path.name)
    for parent in [*(root / "flow").glob("*/runs"), *(root / "smoke").glob("*/runs")]:
        if parent.is_dir() and any(parent.iterdir()):
            errors.append("Native run artifacts require manual archival before removal: " + str(parent))
    return errors


def metrics(directory: Path) -> dict:
    tasks = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]
    finished = [task for task in tasks if task.get("completed_at")]
    cycles = [(datetime.fromisoformat(task["completed_at"]) - datetime.fromisoformat(task["started_at"])).total_seconds()
              for task in finished]
    interventions = [task["human_interventions"] for task in finished if isinstance(task.get("human_interventions"), int)]
    rework = [task["rework_count"] for task in finished if isinstance(task.get("rework_count"), int)]
    attempts = [task.get("verification_attempts", []) for task in tasks]
    executed_first = [next((item for item in values if item.get("status") in {"PASS", "FAIL"}), None)
                      for values in attempts]
    executed_first = [item for item in executed_first if item]
    preparation = [entry["duration_seconds"] for values in attempts for item in values
                   for entry in item.get("preparation", []) if isinstance(entry.get("duration_seconds"), (int, float))]
    return {"schema_version": 1, "tasks": len(tasks), "finished_tasks": len(finished),
            "baseline_ready": len(finished) >= 20,
            "first_executed_verification_samples": len(executed_first),
            "first_executed_verification_pass_fraction": sum(item["status"] == "PASS" for item in executed_first) / len(executed_first) if executed_first else None,
            "median_linter_preparation_seconds": statistics.median(preparation) if preparation else None, "median_cycle_seconds": statistics.median(cycles) if cycles else None,
            "ready_fraction": sum(task["status"] == "READY" for task in finished) / len(finished) if finished else None,
            "intervention_samples": len(interventions), "human_interventions": sum(interventions) if interventions else None,
            "rework_samples": len(rework), "rework_count": sum(rework) if rework else None,
            "note": "Unknown intervention/rework counts remain unknown; no productivity gain is inferred."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("start")
    create.add_argument("--id", required=True)
    create.add_argument("--goal", required=True)
    create.add_argument("--allow", action="append", required=True)
    create.add_argument("--check", action="append", required=True)
    create.add_argument("--dependency", action="append", default=[])
    status = commands.add_parser("status")
    status.add_argument("--id", required=True)
    complete = commands.add_parser("handoff")
    complete.add_argument("--id", required=True)
    complete.add_argument("--evidence", type=Path, action="append", default=[])
    complete.add_argument("--remaining", action="append", default=[])
    complete.add_argument("--human-interventions", type=int)
    complete.add_argument("--rework", type=int)
    commands.add_parser("cleanup-check")
    measure = commands.add_parser("metrics")
    measure.add_argument("--directory", type=Path, default=ROOT / "build/tasks")
    args = parser.parse_args()
    try:
        if args.command == "start":
            value = start(args.id, args.goal, args.allow, args.check, args.dependency)
        elif args.command == "status":
            value = json.loads(task_path(args.id).read_text(encoding="utf-8"))
            value["scope_errors"] = scope_errors(value, ROOT)
        elif args.command == "handoff":
            if any(item is not None and item < 0 for item in (args.human_interventions, args.rework)):
                raise ValueError("Counters cannot be negative")
            value = handoff(args.id, args.evidence, args.remaining, args.human_interventions, args.rework)
        elif args.command == "cleanup-check":
            errors = cleanup_check()
            value = {"status": "BLOCKED" if errors else "READY", "blockers": errors,
                     "note": "Read-only check; no files, worktrees or branches are deleted"}
        else:
            value = metrics(args.directory)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 2 if value.get("status") == "BLOCKED" else 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
