#!/usr/bin/env python3
"""Local task contracts, evidence-backed handoffs, metrics and non-destructive cleanup checks."""
from __future__ import annotations
import argparse
import fnmatch
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from engineering import required_ids
from engineering_common import ROOT, digest, git, source_state, utc_now, validate_evidence, write_json
from task_store import TaskStore, activity_blockers
from task_contract import (Allocation, READERS, allocation_lock, changed_path_errors,
                           contract_errors, handoff_contract, register_task, set_lease_state,
                           acceptance_errors, extra_acceptance_commands, acceptance_commands)


def task_path(identifier: str, root: Path = ROOT) -> Path:
    return TaskStore(root).path(identifier)


def changed_paths(root: Path, base: str) -> list[str]:
    changed = set(git(root, "diff", "--name-only", "-z", base, "--").split(b"\0"))
    changed.update(git(root, "ls-files", "-z", "--others", "--exclude-standard").split(b"\0"))
    return sorted(item.decode("utf-8") for item in changed if item)


def scope_errors(task: dict, root: Path) -> list[str]:
    if task.get("kind") == "engineering":
        if task.get("role") in READERS:
            return contract_errors(task) + ([] if source_state(root) == task.get("analysis_source")
                                            else ["Frozen analysis source changed"])
        return contract_errors(task) + changed_path_errors(task, root, changed_paths(root, task["base_sha"]))
    return [name for name in changed_paths(root, task["base_sha"])
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in task["allowed_paths"])]


def start(identifier: str, goal: str, allowed: list[str], checks: list[str], dependencies: list[str], root: Path = ROOT, managed_identity: str | None = None, verify_command: list[str] | None = None, delivery_mode: str = "auto", *,
          kind: str | None = None, role: str = "writer", read_scope: list[str] | None = None,
          inputs: list[str] | None = None, outputs: list[str] | None = None,
          allocation: Allocation | None = None) -> dict:
    if kind is not None and (not isinstance(kind, str) or kind not in {"engineering", "general", "legacy"}):
        raise ValueError("Unknown task kind")
    if kind != "engineering" and (role != "writer" or read_scope is not None or inputs is not None or outputs is not None or allocation is not None):
        raise ValueError("Explicit engineering kind is required for task contract fields")
    root = root.resolve()
    state = source_state(root)
    analysis = kind == "engineering" and role in READERS
    if not analysis and not state["branch"].startswith("dev-"):
        raise ValueError("Start from a dev-<task-id>-<topic> branch created from dev")
    base = state["sha"] if analysis else git(root, "rev-parse", "origin/dev").decode().strip()
    if not analysis:
        subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", base, "HEAD"], check=True)
    if kind == "engineering" and (not isinstance(allowed, list) or not isinstance(checks, list)):
        raise ValueError("Engineering scopes and acceptance commands must be lists")
    if kind != "engineering" and (not allowed or not checks or any(Path(item).is_absolute() or ".." in item.replace("\\", "/").split("/") for item in allowed)):
        raise ValueError("Provide repository-relative allowed paths and acceptance commands")
    path = task_path(identifier, root)
    if path.exists():
        raise FileExistsError("Task already exists; resume with status/handoff")
    value = {"schema_version": 2, "task_id": identifier, "goal": goal, "base_ref": "origin/dev",
             "base_sha": base, "original_base_sha":base, "branch": state["branch"], "worktree": str(root), "started_at": utc_now(),
             "allowed_paths": allowed, "checks": checks, "dependencies": dependencies,
             "integration_target": "dev", "main_merge_operator": "human", "status": "ACTIVE",
             "host_platform":os.name,"owner_id":__import__("uuid").uuid4().hex,
             "managed_identity":managed_identity,"verify_command":verify_command,"delivery_mode":delivery_mode}
    if kind == "engineering":
        value.update(kind=kind, role=role, read_scope=read_scope, inputs=inputs, outputs=outputs)
        if analysis:
            value.update(branch=None, source_branch=state["branch"], owns_workspace=False, analysis_source=state)
    elif kind in {"general", "legacy"}:
        value["kind"] = "general"
    return register_task(TaskStore(root), value, allocation)


def handoff(identifier: str, evidence: list[Path], remaining: list[str], interventions: int | None,
            rework: int | None, root: Path = ROOT, *, observations_complete: bool = False,
            observation_digest: str | None = None) -> dict:
    store = TaskStore(root)
    if store.load(identifier).get("kind") == "engineering":
        with allocation_lock(store) as ticket:
            return _handoff(identifier, evidence, remaining, interventions, rework, root, store, ticket,
                            observations_complete=observations_complete, observation_digest=observation_digest)
    return _handoff(identifier, evidence, remaining, interventions, rework, root, store,
                    observations_complete=observations_complete, observation_digest=observation_digest)


def _handoff(identifier: str, evidence: list[Path], remaining: list[str], interventions: int | None,
             rework: int | None, root: Path, store: TaskStore, allocation: Allocation | None = None, *,
             observations_complete: bool = False, observation_digest: str | None = None) -> dict:
    value = store.load(identifier)
    observed = source_state(root) if value.get("kind") == "engineering" else None
    errors = scope_errors(value, root)
    checks = set()
    executed = set()
    reports = []
    for report in evidence:
        try:
            result = validate_evidence(report, root)
            if result.get("kind") == "verification":
                checks.update(item["id"] for item in result["checks"] if item.get("status") == "PASS")
            executed.update(tuple(item["command"]) for item in result["checks"]
                            if item.get("status") == "PASS" and item.get("command"))
            reports.append({"path": str(report.resolve()), "sha256": digest(report), "status": result["status"]})
        except (ValueError, KeyError, OSError) as error:
            errors.append(str(error))
    if not (value.get("kind") == "engineering" and value.get("role") in READERS) and not required_ids("all").issubset(checks):
        errors.append("Full fast verification evidence is missing")
    value.update(status="READY" if not errors and not remaining else "BLOCKED", handoff_at=utc_now(),
                 changed_files=[] if value.get("role") in READERS else changed_paths(root, value["base_sha"]), source=source_state(root),
                 evidence=reports, remaining=remaining, blockers=errors,
                 human_interventions=interventions, rework_count=rework)
    if value.get("kind") == "engineering":
        allocation.assert_owner(store)
        if value.get("role") in READERS:
            from tenon import verification_argv
            for argv in acceptance_commands(value["checks"]):
                covered = required_ids("all").issubset(checks) if argv == ["make", "verify"] else tuple(verification_argv({"verify_command": argv}, root)) in executed
                if not covered:
                    errors.append("Declared analysis acceptance command has no executed evidence: " + repr(argv))
        if value.get("role") not in READERS and extra_acceptance_commands(value):
            errors.extend(acceptance_errors(value, root))
        snapshot, contract_errors = handoff_contract(value, root, reports, remaining)
        errors.extend(contract_errors)
        if observed != value["source"] or snapshot["source"] != observed:
            errors.append("Task source changed during handoff")
        errors.extend(name for name in scope_errors(value, root) if name not in errors)
        snapshot["blockers"] = errors
        value.update(output_contract=snapshot, blockers=errors,
                     status="READY" if not errors and not remaining else "BLOCKED")
        if value["status"] == "READY":
            value["execution_lease"] = {**value["execution_lease"], "state": "completed", "updated_at": utc_now()}
    if value.get("kind") == "engineering":
        fields = {name: value[name] for name in ("status", "handoff_at", "changed_files", "source", "evidence", "remaining",
            "blockers", "human_interventions", "rework_count", "output_contract", "execution_lease")}
        def merge(current):
            from task_observations import handoff_declaration, manual_summary, source_reference
            merged = {**current, **fields}
            merged = handoff_declaration(current, merged, source_reference(root, current),
                complete=observations_complete, expected_digest=observation_digest)
            merged["output_contract"]["observation_completeness"] = manual_summary(merged)
            return merged
        return store.mutate(identifier,merge,"handoff")
    def merge_legacy(current):
        from task_observations import handoff_declaration, source_reference
        # Do not overwrite concurrently recorded history/observations with the old snapshot.
        fields = {name:value[name] for name in ("status","handoff_at","changed_files","source","evidence",
            "remaining","blockers","human_interventions","rework_count")}
        return handoff_declaration(current,{**current,**fields},source_reference(root,current),
            complete=observations_complete,expected_digest=observation_digest)
    return store.mutate(identifier,merge_legacy,"handoff")


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
    store = TaskStore(root)
    for value in store.all():
        if Path(value["worktree"]).resolve()==root.resolve() and value.get("status")=="ACTIVE":
            errors.append("Active task: " + value["task_id"])
        if Path(value["worktree"]).resolve()==root.resolve():
            errors.extend(activity_blockers(store,value["task_id"]))
    for parent in [*(root / "flow").glob("*/runs"), *(root / "smoke").glob("*/runs")]:
        if parent.is_dir() and any(parent.iterdir()):
            errors.append("Native run artifacts require verified archival before removal: " + str(parent))
    return errors


def _parse_time(value: str):
    return datetime.fromisoformat(value.replace("Z","+00:00"))


def metrics(directory: Path) -> dict:
    """Compatible legacy keys plus the qualified read-only metrics v1 report."""
    from engineering_metrics import collect, summarize, elapsed
    from math import isfinite
    if directory.name=="tasks" and directory.parent.name=="build" and (directory.parent.parent/".git").exists():
        repo = directory.parent.parent
        detailed = collect(repo)
        tasks = TaskStore(repo).all() if detailed.get("initialized") else []
    else:
        tasks = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]
        detailed = summarize(tasks)
    finished = [row for row in tasks if row.get("completed_at") or row.get("delivered_at") or row.get("delivery",{}).get("merged")]
    cycles = [elapsed(row.get("started_at"), row.get("delivered_at") or row.get("delivery",{}).get("merged_at") or row.get("completed_at")) for row in finished]
    cycles = [value for value in cycles if value is not None]
    def attempts(row):
        values = row.get("verification_attempts",[])
        return [value for value in values if isinstance(value,dict)] if isinstance(values,list) else []
    firsts = [next((entry for entry in attempts(row) if entry.get("status") in {"PASS","FAIL"}),None) for row in tasks]
    firsts = [value for value in firsts if value]
    preparation = [item["duration_seconds"] for row in tasks for entry in attempts(row) for item in entry.get("preparation",[])
                   if isinstance(item,dict) and type(item.get("duration_seconds")) in {int,float} and isfinite(item["duration_seconds"]) and item["duration_seconds"]>=0]
    counters = detailed["counters"]
    cleanup = [elapsed(row.get("delivered_at") or row.get("delivery",{}).get("merged_at"),row.get("cleaned_at")) for row in tasks if row.get("cleaned_at")]
    return {"schema_version":2,"status":"BLOCKED" if detailed.get("diagnostics") else "OBSERVED",
        "tasks":len(tasks),"finished_tasks":len(finished),"baseline_ready":detailed["baseline_ready"],
        "delivered_tasks":detailed["delivered_tasks"],"cleaned_tasks":detailed["cleaned_tasks"],
        "verification_ready_tasks":sum(row.get("status")=="READY" for row in tasks),
        "blocked_tasks":sum(row.get("status") in {"BLOCKED","NEEDS_ATTENTION","DEV_CI_FAILED","WAITING_RESOURCE"} for row in tasks),
        "cleanup_delays_seconds":[value for value in cleanup if value is not None],
        "cleanup_pending_tasks":detailed["cleanup_pending_tasks"],
        "first_executed_verification_samples":len(firsts),
        "first_executed_verification_pass_fraction":sum(row["status"]=="PASS" for row in firsts)/len(firsts) if firsts else None,
        "median_linter_preparation_seconds":statistics.median(preparation) if preparation else None,
        "median_cycle_seconds":statistics.median(cycles) if cycles else None,
        "ready_fraction":sum(row.get("status") in {"READY","INTEGRATED","CLEANUP_PENDING","MERGED","DEV_GREEN","ARCHIVED","CLEANING","CLEANED"} or row.get("delivery",{}).get("merged",False) for row in finished)/len(finished) if finished else None,
        "intervention_samples":counters["human_interventions"]["samples"],"human_interventions":counters["human_interventions"]["known_total"],
        "rework_samples":counters["rework_count"]["samples"],"rework_count":counters["rework_count"]["known_total"],
        "engineering_metrics_v1":detailed,
        "note":"Legacy first_executed keys include partial groups; engineering_metrics_v1 classifies full runs. Unknown counters stay unknown; no productivity or signoff inference."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("start")
    create.add_argument("--id", required=True)
    create.add_argument("--goal", required=True)
    create.add_argument("--allow", action="append", default=[])
    create.add_argument("--check", action="append", required=True)
    create.add_argument("--dependency", action="append", default=[])
    create.add_argument("--managed-identity")
    create.add_argument("--verify-command-json")
    create.add_argument("--local-only",action="store_true")
    create.add_argument("--kind", choices=("engineering", "general", "legacy"))
    create.add_argument("--role", choices=("writer", "integrator", "coordinator", "reviewer"), default="writer")
    create.add_argument("--read-allow", action="append")
    create.add_argument("--input", action="append")
    create.add_argument("--output", action="append")
    lease = commands.add_parser("lease")
    lease.add_argument("--id", required=True)
    lease.add_argument("--state", choices=("active", "paused", "completed"), required=True)
    lease.add_argument("--owner-id", required=True)
    lease.add_argument("--role", choices=("writer", "integrator", "coordinator", "reviewer"))
    migrate = commands.add_parser("migrate")
    migrate.add_argument("--worktree",type=Path,required=True)
    migrate.add_argument("--managed-identity")
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
            value = start(args.id, args.goal, args.allow, args.check, args.dependency,managed_identity=args.managed_identity,verify_command=json.loads(args.verify_command_json) if args.verify_command_json else None,delivery_mode="local-only" if args.local_only else "auto",
                kind=args.kind, role=args.role, read_scope=args.read_allow, inputs=args.input, outputs=args.output)
        elif args.command == "lease":
            value = set_lease_state(TaskStore(ROOT), args.id, args.state, owner_id=args.owner_id, role=args.role)
        elif args.command == "migrate":
            value = {"migrated":TaskStore(ROOT).migrate(args.worktree,args.managed_identity)}
        elif args.command == "status":
            value = TaskStore(ROOT).load(args.id)
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
