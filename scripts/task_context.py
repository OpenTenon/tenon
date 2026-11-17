#!/usr/bin/env python3
"""Compact read-only task context from snapshots, journals, inboxes and local Git."""
from __future__ import annotations

# Controller execution ABI 1: keep before all local/business imports.
import os as _tenon_os
import sys as _tenon_sys
from pathlib import Path as _tenon_Path

def _tenon_cache_entry():
    entry = _tenon_Path(__file__).absolute()
    version = entry.parent.parent
    if (entry.parent.name != "scripts" or version.parent.name != "controllers"
            or version.parent.parent.name != "tenon" or len(version.name) != 40
            or any(char not in "0123456789abcdef" for char in version.name)):
        return
    module = _tenon_sys.modules.get("controller_leases")
    if module is not None and module.active_for(entry):
        return
    if __name__ != "__main__":
        raise RuntimeError("Owned controller imports require an admitted execution; use the installed entry")
    _tenon_sys.dont_write_bytecode = True
    _tenon_os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    launcher = version.parent.parent / "bin/tenon.py"
    if not launcher.is_file():
        raise RuntimeError("Controller admission is unavailable; run the qualified source installer explicitly")
    import json as _tenon_boot_json
    configuration = _tenon_boot_json.loads((launcher.parent / "installation.json").read_bytes())
    if configuration.get("controller_lease_abi") != 1:
        raise RuntimeError("Installed launcher has no execution admission ABI; run the qualified new source installer explicitly after dev is green")
    import runpy as _tenon_runpy
    _tenon_sys.argv = [str(launcher), "_controller", str(entry), *_tenon_sys.argv[1:]]
    _tenon_runpy.run_path(str(launcher), run_name="__main__")
    raise SystemExit(0)

try:
    _tenon_cache_entry()
except (OSError, ValueError, RuntimeError) as _tenon_error:
    import json as _tenon_json
    print(_tenon_json.dumps({"status": "BLOCKED", "reason": str(_tenon_error)}))
    raise SystemExit(2)

import copy
import json
import os
from pathlib import Path
import subprocess
from engineering_common import git, validate_evidence
from engineering import required_ids
from engineering_metrics import _json, _journal, _lease
from task_store import identifier
from task_observations import evidence_digest, source_reference, manual_summary, machine_summary


def read_task(repo: Path, task_id: str):
    """No TaskStore construction, lock creation, journal repair or remote queries."""
    identifier(task_id)
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    base = common / "tenon"
    diagnostics = []
    identity = _json(base / "repository.json", diagnostics)
    if not identity or identity.get("schema_version") != 1 or not isinstance(identity.get("repo_id"), str):
        raise ValueError("Repository runtime identity is absent/invalid; context never initializes it")
    repo_id = identity["repo_id"]
    journal, recovered = _journal(base / "tasks" / (task_id + ".jsonl"), task_id, repo_id, diagnostics)
    snapshot = _json(base / "tasks" / (task_id + ".json"), diagnostics)
    candidates = [row for row in (snapshot, recovered) if row and row.get("schema_version") == 2
                  and row.get("repo_id") == repo_id and row.get("task_id") == task_id and type(row.get("revision")) is int]
    if not candidates:
        raise ValueError("No registered task snapshot exists")
    task = copy.deepcopy(max(candidates, key=lambda row: row["revision"]))
    attempts = task.setdefault("verification_attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("Task verification history is malformed")
    for path in sorted((base / "inbox" / task_id).glob("*.json")):
        event = _json(path, diagnostics)
        if not event or event.get("repo_id") != repo_id or event.get("task_id") != task_id or not isinstance(event.get("attempt"), dict):
            diagnostics.append({"issue": "invalid-task-inbox", "path": str(path)})
            continue
        if event["attempt"] not in attempts:
            attempts.append(event["attempt"])
    queued = _json(base / "queue" / (task_id + ".json"), diagnostics)
    if queued and (queued.get("schema_version") != 1 or queued.get("repo_id") != repo_id
                   or queued.get("task_id") != task_id or queued.get("owner_id") != task.get("owner_id")):
        diagnostics.append({"issue": "queue-identity-differs"})
        queued = None
    return base, task, queued, diagnostics


def evidence_state(repo, task, source):
    if source["mode"] == "historical":
        return {"state": "HISTORICAL", "source": task.get("acceptance", {}).get("source") or task.get("source"),
                "full_verification_sha256": task.get("acceptance", {}).get("full_verification_sha256"),
                "archive": task.get("archive"), "qualification": "Verified historical archive; no live-source reuse claim"}
    path = task.get("verification_report")
    if not path:
        return {"state": "MISSING", "reason": "No full verification report is registered"}
    if source["mode"] != "live":
        return {"state": "UNAVAILABLE", "reason": "No owned live source to validate"}
    try:
        from host_profile import validate_task_inputs
        validate_task_inputs(task)
        result = validate_evidence(Path(path), Path(task["worktree"]), required_ids("all"))
        if result.get("kind") != "verification":
            raise ValueError("Full fast verification is required")
        return {"state": "SOURCE_MATCHED", "report": path, "source": result["source"],
                "qualification": "Existing local execution only; not PR/dev CI or signoff"}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return {"state": "STALE", "report": path, "reason": str(error)}


def next_argv(task, source, evidence):
    prefix = ["git", "tenon", "task"]
    task_id = task["task_id"]
    if task.get("status") == "CLEANED":
        return ["git", "tenon", "maintenance", "restore-check", "--id", task_id]
    if source["mode"] != "live":
        return [*prefix, "status", "--id", task_id]
    if task.get("role") in {"coordinator", "reviewer"}:
        return [*prefix, "verify", "--id", task_id]
    if task.get("status") == "ACTIVE":
        if task.get("execution_lease", {}).get("state") == "paused":
            return [*prefix, "lease", "--id", task_id, "--state", "active", "--role", "writer"]
        return ["git", "-C", task["worktree"], "status", "--short"]
    if evidence["state"] in {"STALE", "MISSING"} and not task.get("delivery", {}).get("merged"):
        return [*prefix, "verify", "--id", task_id]
    return [*prefix, "resume", "--id", task_id]


def context(repo: Path, task_id: str) -> dict:
    base, task, queued, diagnostics = read_task(repo, task_id)
    source = source_reference(repo, task)
    evidence = evidence_state(repo, task, source)
    dependencies = []
    for name in task.get("dependencies", []):
        try:
            _, dependency, _, issues = read_task(repo, name)
            green = dependency.get("dev_ci", {})
            ready = dependency.get("delivery", {}).get("merged") is True and green.get("conclusion") == "success" and green.get("head_sha") == dependency.get("green_dev_sha")
            dependencies.append({"task_id": name, "recorded_ready": ready, "status": dependency.get("status"), "diagnostics": issues})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            dependencies.append({"task_id": name, "recorded_ready": None, "reason": str(error)})
    actors = []
    for path in sorted((base / "tasks").glob("*.json")):
        try:
            _, row, _, _ = read_task(repo, path.stem)
            lease = row.get("execution_lease", {})
            if lease.get("state") == "active" and lease.get("role") in {"writer", "integrator"} and row.get("status") not in {"CLEANED", "CANCELLED"}:
                actors.append({"task_id": row["task_id"], "role": lease["role"], "owner_id": lease.get("owner_id")})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            diagnostics.append({"task_id": path.stem, "issue": "actor-observation-incomplete"})
    machine = machine_summary(task, queued, repo=repo)
    diagnostics.extend({"issue": issue} for issue in machine["diagnostics"])
    return {"schema_version": 1, "kind": "task-context", "status": "BLOCKED" if diagnostics else "OBSERVED",
            "task_id": task_id, "task_status": task.get("status"), "goal": task.get("goal"),
            "original_base": task.get("original_base_sha"), "current_base": task.get("base_sha"),
            "kind_policy": task.get("kind", "legacy-unspecified"), "role": task.get("role"),
            "scope": task.get("allowed_paths", []), "read_scope": task.get("read_scope"),
            "dependencies": dependencies, "acceptance": task.get("checks", []),
            "source": source, "evidence": evidence,
            "actor_lease": task.get("execution_lease"), "capacity": {"limit": 2, "observed_active": actors},
            "native_controller_lock": _lease(base / "locks" / (task_id + "-controller.lock"), task.get("host_platform")),
            "manual_observations": manual_summary(task, source), "machine_observations": machine,
            "attestation_evidence_digest": evidence_digest(task, source),
            "references": {"task": str(base / "tasks" / (task_id + ".json")),
                           "journal": str(base / "tasks" / (task_id + ".jsonl")),
                           "queue": str(base / "queue" / (task_id + ".json")) if queued else None,
                           "pr": task.get("pr_url"), "archive": task.get("archive", {}).get("path")},
            "next_argv": next_argv(task, source, evidence), "diagnostics": diagnostics,
            "qualification": "Local read-only observations; role grants no editing authority. Suggested argv requires existing authorization. No raw traces, repairs, remote queries or signoff."}
