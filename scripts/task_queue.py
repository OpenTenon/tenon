#!/usr/bin/env python3
"""Explicit, versioned queue for verified delivery and already-merged cleanup only."""
from __future__ import annotations
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from engineering import required_ids
from engineering_common import digest, git, source_state, utc_now, validate_evidence, write_json
from task_archive import expected_head, ownership_errors, verify_task_archive, worktrees
from task_lifecycle import Lifecycle
from task_store import FileLease, TaskStore, identifier


def queue_path(store: TaskStore, task_id: str) -> Path:
    return store.root / "queue" / (identifier(task_id) + ".json")


def read_queue(store: TaskStore, task_id: str) -> dict:
    value = json.loads(queue_path(store, task_id).read_text(encoding="utf-8"))
    if (value.get("schema_version") != 1 or value.get("repo_id") != store.repo_id
            or value.get("task_id") != task_id):
        raise ValueError("Queue identity/version differs")
    return value


def submit(repo: Path, task_id: str) -> dict:
    store = TaskStore(repo)
    with FileLease(store.root / "locks" / (identifier(task_id) + "-controller.lock")):
        task = store.load(task_id)
        if task.get("host_platform") != os.name or task.get("delivery_mode", "auto") != "auto":
            raise ValueError("Only explicitly authorized tasks on their registered host can be queued")
        merged = task.get("delivery", {}).get("merged", False)
        head = expected_head(task)
        report_hash = None
        if not merged:
            root = Path(task["worktree"])
            state = source_state(root)
            if state["dirty"] or state["sha"] != head or task.get("verified_head") != head:
                raise ValueError("Queue requires a clean, verified committed candidate")
            report = Path(task["verification_report"])
            validate_evidence(report, root, required_ids("all"))
            report_hash = digest(report)
        path = queue_path(store, task_id)
        if path.exists():
            previous = read_queue(store, task_id)
            if previous["owner_id"] != task["owner_id"]:
                raise ValueError("Existing queue belongs to another owner")
            if previous.get("state") == "DONE":
                if previous["candidate_sha"] != head:
                    raise ValueError("A completed task needs a new task identity for new work")
                return previous
        else:
            previous = None
        value = {"schema_version": 1, "repo_id": store.repo_id, "task_id": task_id,
                 "owner_id": task["owner_id"], "candidate_sha": head,
                 "stage": "CLEANUP" if merged else "DELIVERY", "state": "QUEUED",
                 "queued_at": utc_now(), "next_attempt_at": utc_now(), "attempts": 0,
                 "verification_sha256": report_hash,
                 "generation": (previous or {}).get("generation", 0) + 1,
                 "history": (previous or {}).get("history", []) +
                     ([{key: item for key, item in previous.items() if key != "history"}] if previous else [])}
        write_json(path, value)
        return value


def classify(error: BaseException) -> str:
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) or getattr(error, "winerror", None) == 32:
        return "RESOURCE" if getattr(error, "winerror", None) == 32 else "TRANSIENT"
    if isinstance(error, (ValueError, KeyError)):
        return "POLICY"
    text = str(error).lower()
    if isinstance(error, subprocess.CalledProcessError):
        # CalledProcessError.__str__ contains argv, including SSH ConnectTimeout.
        # Only process diagnostics can establish a transport failure.
        diagnostic = error.stderr or error.stdout or b""
        text = diagnostic.decode("utf-8", errors="replace").lower() if isinstance(diagnostic, bytes) else diagnostic.lower()
    if any(fragment in text for fragment in ("bad credentials", "authentication failed", "permission denied", "http 401", "resource not accessible by integration")):
        return "POLICY"
    if any(fragment in text for fragment in ("timed out", "timeout", "connection reset", "connection refused",
            "could not resolve", "unable to resolve", "network is unreachable", "http 502", "http 503", "http 504",
            "a connection attempt failed because the connected party did not properly respond",
            "established connection failed because connected host has failed to respond",
            "http 429", "api rate limit exceeded", "secondary rate limit")):
        return "TRANSIENT"
    return "POLICY"


def derived_candidate(store: TaskStore, task: dict, old: str, new: str) -> bool:
    intent = task.get("base_merge_intent", {})
    if intent.get("from_head") != old or not intent.get("token") or not intent.get("dev_sha"):
        return False
    parents = git(store.repo, "show", "-s", "--format=%P", new).decode().split()
    message = git(store.repo, "show", "-s", "--format=%B", new).decode()
    return parents == [old, intent["dev_sha"]] and ("Tenon-Base-Merge: " + intent["token"]) in message.splitlines()


def preflight(store: TaskStore, entry: dict, apply: bool = False) -> dict:
    task = store.load(entry["task_id"])
    if (task.get("owner_id") != entry["owner_id"] or task.get("host_platform") != os.name
            or task.get("delivery_mode", "auto") != "auto"):
        raise ValueError("Queued task owner/host/authorization changed")
    if task.get("status") == "CLEANED":
        return task
    root = Path(task["worktree"])
    actual_head = expected_head(task)
    if root.exists() and (root / ".git").exists():
        state = source_state(root)
        actual_head = state["sha"]
        if state["dirty"]:
            raise ValueError("Queued worktree contains new source or commits")
    elif not task.get("delivery", {}).get("merged"):
        raise ValueError("Unmerged queued worktree is missing")
    if actual_head != entry["candidate_sha"]:
        if not derived_candidate(store, task, entry["candidate_sha"], actual_head):
            raise ValueError("Queued candidate changed without its durable base-merge intent")
        entry.update(candidate_sha=actual_head, needs_verification=True,
                     transition={**task["base_merge_intent"], "result_head": actual_head})
        task = {**task, "head_sha": actual_head, "base_sha": task["base_merge_intent"]["dev_sha"]}
        if apply:
            store.mutate(task["task_id"], lambda value: {**value, "head_sha": actual_head,
                "base_sha": task["base_merge_intent"]["dev_sha"]}, "queued-base-merge-recovered")
            write_json(queue_path(store, task["task_id"]), entry)
    elif expected_head(task) != actual_head:
        raise ValueError("Task candidate differs from queued source")
    if task.get("delivery", {}).get("merged"):
        errors = ownership_errors(store, task, absent_ok=bool(task.get("cleanup", {}).get("remote_deleted")))
        if errors:
            raise ValueError("; ".join(errors))
    if entry["stage"] == "DELIVERY" and not task.get("delivery", {}).get("merged"):
        if entry.get("needs_verification"):
            transition = entry["transition"]
            if not derived_candidate(store, {**task, "base_merge_intent": transition}, transition["from_head"], actual_head):
                raise ValueError("Queued derived candidate lacks its original transition proof")
        else:
            report = Path(task["verification_report"])
            if digest(report) != entry["verification_sha256"]:
                raise ValueError("Queued verification evidence changed")
            validate_evidence(report, root, required_ids("all"))
    return task


def reconcile(repo: Path, apply: bool = False, factory=Lifecycle, now: datetime | None = None) -> dict:
    store = TaskStore(repo)
    now = now or datetime.now(timezone.utc)
    results = []
    for path in sorted((store.root / "queue").glob("*.json")):
        external = None
        task_id = path.stem
        entry = read_queue(store, task_id)
        if entry.get("state") in {"DONE", "BLOCKED", "WAITING_APP"}:
            results.append({"task_id": task_id, "state": entry["state"]})
            continue
        if datetime.fromisoformat(entry["next_attempt_at"]) > now:
            results.append({"task_id": task_id, "state": "BACKOFF"})
            continue
        if not apply:
            try:
                task = preflight(store, entry)
                results.append({"task_id": task_id, "state": "READY", "task_status": task["status"]})
            except (OSError, ValueError, KeyError) as error:
                results.append({"task_id": task_id, "state": "BLOCKED", "reason": str(error)})
            continue
        # One integration owner and the same per-task lease used by foreground delivery.
        with FileLease(store.root / "locks/integration.lock"), FileLease(store.root / "locks" / (task_id + "-controller.lock")):
            entry = read_queue(store, task_id)
            try:
                task = preflight(store, entry, apply=True)
                lifecycle = factory(repo, task_id)
                if task.get("status") == "CLEANED":
                    verify_task_archive(store, task)
                    result = {"status": "CLEANED"}
                elif task.get("managed_identity") and task.get("app_archive_receipt"):
                    result = lifecycle.complete_app_cleanup(Path(task["app_archive_receipt"]))
                else:
                    result = lifecycle.deliver(allow_commit=False, expected_candidate=entry["candidate_sha"])
                if result.get("status") == "READY_EXTERNAL_CLEANUP":
                    external = result
                observed = store.load(task_id)
                entry.update(candidate_sha=expected_head(observed), stage="CLEANUP" if observed.get("delivery", {}).get("merged") else "DELIVERY")
                if not observed.get("delivery", {}).get("merged"):
                    report = Path(observed["verification_report"])
                    validate_evidence(report, Path(observed["worktree"]), required_ids("all"))
                    entry.update(verification_sha256=digest(report), needs_verification=False)
                status = result.get("status")
                state = "DONE" if status == "CLEANED" else ("WAITING_APP" if status == "NEEDS_APP_ARCHIVE" else
                        "BLOCKED" if status in {"BLOCKED", "NEEDS_ATTENTION", "DEV_CI_FAILED"} else "WAITING")
                entry.update(state=state, last_result=result, attempts=entry["attempts"] + 1,
                             next_attempt_at=(now + timedelta(seconds=int(result.get("next_poll_seconds", 30)))).isoformat())
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
                category = classify(error)
                entry.update(state="WAITING" if category in {"TRANSIENT", "RESOURCE"} else "BLOCKED",
                             error_category=category, reason=str(error), attempts=entry["attempts"] + 1,
                             next_attempt_at=(now + timedelta(seconds=min(300, 5 * 2 ** min(entry["attempts"], 6)))).isoformat())
            entry["updated_at"] = utc_now()
            write_json(path, entry)
            results.append({"task_id": task_id, "state": entry["state"], "reason": entry.get("reason")})
        # The external controller acquires its own task lock; do not hold ours across this handoff.
        if external:
            try:
                primary = Path(external["repo"])
                output = subprocess.check_output([__import__("sys").executable, external["controller"], "cleanup",
                    "--repo", str(primary), "--id", task_id], cwd=primary, stderr=subprocess.PIPE)
                result = json.loads(output)
                with FileLease(store.root / "locks" / (task_id + "-controller.lock")):
                    entry = read_queue(store, task_id)
                    entry.update(last_result=result, state="DONE" if result.get("status") == "CLEANED" else
                                 "WAITING_APP" if result.get("status") == "NEEDS_APP_ARCHIVE" else "WAITING")
                    write_json(path, entry)
                results[-1] = {"task_id": task_id, "state": entry["state"]}
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                with FileLease(store.root / "locks" / (task_id + "-controller.lock")):
                    entry = read_queue(store, task_id)
                    entry.update(state="WAITING" if classify(error) in {"RESOURCE", "TRANSIENT"} else "BLOCKED",
                                 reason=str(error), error_category=classify(error))
                    write_json(path, entry)
                results[-1] = {"task_id": task_id, "state": entry["state"], "reason": str(error)}
    states = {item["state"] for item in results}
    status = "BLOCKED" if "BLOCKED" in states else "WAITING" if states & {"WAITING","BACKOFF","QUEUED","WAITING_APP"} else "OBSERVED"
    return {"schema_version": 1, "status":status, "qualification":"Queue observation only; WAITING is never CLEANED",
            "mode": "apply" if apply else "read-only", "at": now.isoformat(), "tasks": results}
