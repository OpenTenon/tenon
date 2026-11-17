#!/usr/bin/env python3
"""Explicit, versioned queue for verified delivery and already-merged cleanup only."""
from __future__ import annotations
import copy
import json
import re
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


def _attempt_identity(active):
    return {key: active[key] for key in ("owner_id", "generation", "input_candidate", "ordinal")}


def _event(active, operation, entry, *, status=None, category=None, reason=None, derivation=None):
    from task_observations import fingerprint
    identity = _attempt_identity(active)
    payload = {"operation": operation, **identity}
    if operation == "controller-result":
        classification = ("transient-retry" if category == "TRANSIENT" else "resource-retry" if category == "RESOURCE"
            else "policy-blocked" if category else "ci-wait" if status in {"WAITING_CI", "WAITING_DEV_CI"}
            else "resource-wait" if status == "WAITING_RESOURCE" else "phase-result")
        payload.update(status=status, classification=classification, error_category=category, reason=reason,
                       observed_candidate=entry["candidate_sha"], stage=entry["stage"])
    provenance = {"recorded_at": active["started_at"] if operation == "controller-start" else utc_now(),
                  "source": "queue-v1", "coverage": "queued-candidate logical operations"}
    if derivation:
        provenance["base_derivations"] = copy.deepcopy(derivation)
    return {"id": "q-" + fingerprint({"operation": operation, **identity}),
            "payload": payload, "provenance": provenance}


def _derivation_proves(store, proof, old, new):
    if store.repo is None or not isinstance(proof, dict) or proof.get("from_head") != old or not proof.get("token") or not proof.get("dev_sha"):
        return False
    parents = git(store.repo, "show", "-s", "--format=%P", new).decode().split()
    message = git(store.repo, "show", "-s", "--format=%B", new).decode().splitlines()
    return parents == [old, proof["dev_sha"]] and "Tenon-Base-Merge: " + proof["token"] in message


def _candidate_chain(store, task, entry, old, new, saved=None):
    """Prove every merge hop; ancestry alone is never a derivation contract."""
    if not all(re.fullmatch(r"[0-9a-f]{40}", str(value or "")) for value in (old,new)):
        raise ValueError("Observation candidate identity is missing")
    chain = copy.deepcopy([] if saved is None else saved)
    if not isinstance(chain, list):
        raise ValueError("Observation derivation chain is malformed")
    cursor = old
    for proof in chain:
        if (not isinstance(proof, dict) or proof.get("from_head") != cursor
                or not re.fullmatch(r"[0-9a-f]{40}", str(proof.get("result_head", "")))
                or not _derivation_proves(store, proof, cursor, proof["result_head"])):
            raise ValueError("Observation derivation chain has an unproven hop")
        cursor = proof["result_head"]
    if cursor != new:
        for proof in (entry.get("transition"), task.get("base_merge_intent")):
            if (isinstance(proof, dict) and proof.get("from_head") == cursor
                    and proof.get("result_head", new) == new and _derivation_proves(store, proof, cursor, new)):
                chain.append({**copy.deepcopy(proof), "result_head": new})
                cursor = new
                break
    if cursor != new:
        raise ValueError("Observation candidate changed without a complete proven base derivation chain")
    return chain


def _valid_observation(store, task, entry, event):
    from task_observations import fingerprint
    payload = event["payload"]
    if (entry.get("owner_id") != task.get("owner_id") or entry.get("repo_id") != store.repo_id
            or entry.get("task_id") != task["task_id"] or payload.get("owner_id") != entry["owner_id"]
            or type(entry.get("generation")) is not int or type(payload.get("generation")) is not int or payload["generation"] != entry.get("generation")
            or type(payload.get("ordinal")) is not int or payload["ordinal"] < 1
            or not re.fullmatch(r"[0-9a-f]{40}", str(payload.get("input_candidate", "")))
            or payload.get("operation") not in {"controller-start", "controller-result"}):
        raise ValueError("Machine outbox owner/generation/candidate binding differs")
    limit = entry.get("attempts")
    if (type(limit) is not int or limit < 0
            or payload["ordinal"] > limit + (1 if payload["operation"] == "controller-start" else 0)):
        raise ValueError("Machine outbox ordinal is outside its queue attempt range")
    identity = {key: payload[key] for key in ("owner_id", "generation", "input_candidate", "ordinal")}
    if event["id"] != "q-" + fingerprint({"operation": payload["operation"], **identity}):
        raise ValueError("Machine outbox event identity differs")
    if payload["operation"] == "controller-result" and payload.get("observed_candidate") != entry["candidate_sha"]:
        raise ValueError("Machine result observed candidate differs")
    provenance = event.get("provenance", {})
    chain = provenance.get("base_derivations")
    if chain is None and provenance.get("base_derivation"):
        chain = [{**provenance["base_derivation"], "result_head": entry["candidate_sha"]}]
    if chain is None:
        active = entry.get("active_attempt", {})
        chain = active.get("derivations", []) if active.get("input_candidate") == payload["input_candidate"] else []
    _candidate_chain(store, task, entry, payload["input_candidate"], entry["candidate_sha"], chain)


def _drain_observations(store, task_id, path, entry):
    from task_observations import mirror_machine
    task = store.load(task_id)
    changed = False
    for row in [*entry.get("history", []), entry]:
        if not row.get("observation_outbox"):
            continue
        for event in row["observation_outbox"]:
            _valid_observation(store, task, row, event)
        mirror_machine(store, task_id, row)
        row["observation_outbox"] = []
        changed = True
    if changed:
        write_json(path, entry)


def _begin_attempt(store, task, path, entry):
    """Durable queue intent before execution; an interrupted intent reuses its ID."""
    generation = entry.get("generation")
    active = entry.get("active_attempt")
    if type(generation) is not int or generation < 1 or type(entry.get("attempts")) is not int or entry["attempts"] < 0:
        if active:
            raise ValueError("Interrupted controller attempt has unknown generation/ordinal")
        return None  # Legacy provenance is unknown, never invented or backfilled.
    if active:
        if (active.get("owner_id") != entry["owner_id"] or type(active.get("generation")) is not int
                or active["generation"] != generation
                or type(active.get("ordinal")) is not int or active["ordinal"] < 1
                or ("external" in active and type(active["external"]) is not bool)
                or active["ordinal"] != entry["attempts"] + 1):
            raise ValueError("Interrupted controller attempt identity differs")
        chain = _candidate_chain(store, task, entry, active.get("input_candidate"), entry["candidate_sha"],
                                 active.get("derivations", []))
        if active.get("derivations") != chain:
            active["derivations"] = chain
            # Capture accepted hops before another operation can replace the last intent.
            write_json(path, entry)
    else:
        active = {"owner_id": entry["owner_id"], "generation": generation, "input_candidate": entry["candidate_sha"],
                  "ordinal": entry["attempts"] + 1, "started_at": utc_now(), "derivations": []}
        entry["active_attempt"] = active
        entry.setdefault("observation_coverage", {"schema_version": 1, "scope": "queued-operations",
            "from_generation": generation, "from_attempt": active["ordinal"],
            "complete_from_generation_start": entry["attempts"] == 0, "input_candidate": entry["candidate_sha"]})
        entry.setdefault("observation_outbox", []).append(_event(active, "controller-start", entry))
        write_json(path, entry)
    _drain_observations(store, task["task_id"], path, entry)
    return active


def _finish_attempt(store, task, entry, result, category=None, reason=None):
    active = entry.get("active_attempt")
    if not active:
        return
    chain = _candidate_chain(store, task, entry, active["input_candidate"], entry["candidate_sha"],
                             active.get("derivations", []))
    event = _event(active, "controller-result", entry, status=result.get("status"), category=category,
                   reason=reason, derivation=chain)
    entry.setdefault("observation_outbox", []).append(event)
    entry.pop("active_attempt", None)


def reconcile(repo: Path, apply: bool = False, factory=Lifecycle, now: datetime | None = None) -> dict:
    store = TaskStore(repo)
    now = now or datetime.now(timezone.utc)
    results = []
    for path in sorted((store.root / "queue").glob("*.json")):
        external = None
        task_id = path.stem
        entry = read_queue(store, task_id)
        if apply and any(row.get("observation_outbox") for row in [*entry.get("history", []),entry]):
            with FileLease(store.root / "locks" / (task_id + "-controller.lock")):
                entry = read_queue(store, task_id)
                _drain_observations(store, task_id, path, entry)
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
            attempt_started = False
            try:
                task = preflight(store, entry, apply=True)
                attempt_started = _begin_attempt(store, task, path, entry) is not None
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
                pending_external = bool(external and entry.get("active_attempt"))
                # The logical attempt remains open across the external invocation.
                # Repeating a fresh handoff after process exit cannot consume it again.
                entry.update(state=state, last_result=result, attempts=entry["attempts"] + (0 if pending_external else 1),
                             next_attempt_at=(now + timedelta(seconds=int(result.get("next_poll_seconds", 30)))).isoformat())
                if pending_external:
                    entry["active_attempt"]["external"] = True
                else:
                    _finish_attempt(store, observed, entry, result)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
                category = classify(error)
                # A persisted start whose mirror failed has not executed or closed.
                # Preserve its ordinal so a later publisher retry can resume it.
                unexecuted_start = not attempt_started and bool(entry.get("active_attempt"))
                entry.update(state="WAITING" if category in {"TRANSIENT", "RESOURCE"} else "BLOCKED",
                             error_category=category, reason=str(error), attempts=entry["attempts"] + (0 if unexecuted_start else 1),
                             next_attempt_at=(now + timedelta(seconds=min(300, 5 * 2 ** min(entry["attempts"], 6)))).isoformat())
                if attempt_started:
                    _finish_attempt(store, store.load(task_id), entry, {"status":entry["state"]}, category, str(error))
            entry["updated_at"] = utc_now()
            write_json(path, entry)
            _drain_observations(store, task_id, path, entry)
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
                    if entry.get("active_attempt"):
                        entry["attempts"] += 1
                    _finish_attempt(store, store.load(task_id), entry, result)
                    write_json(path, entry)
                results[-1] = {"task_id": task_id, "state": entry["state"]}
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                with FileLease(store.root / "locks" / (task_id + "-controller.lock")):
                    entry = read_queue(store, task_id)
                    entry.update(state="WAITING" if classify(error) in {"RESOURCE", "TRANSIENT"} else "BLOCKED",
                                 reason=str(error), error_category=classify(error))
                    if entry.get("active_attempt"):
                        entry["attempts"] += 1
                    _finish_attempt(store, store.load(task_id), entry, {"status":entry["state"]}, classify(error), str(error))
                    write_json(path, entry)
                results[-1] = {"task_id": task_id, "state": entry["state"], "reason": str(error)}
            # The operation result is durable. Publisher failure must preserve its
            # closed state/ordinal/outbox, exactly like the ordinary result path.
            # A later reconcile only drains; it never reclassifies the operation.
            with FileLease(store.root / "locks" / (task_id + "-controller.lock")):
                entry = read_queue(store, task_id)
                _drain_observations(store, task_id, path, entry)
    states = {item["state"] for item in results}
    status = "BLOCKED" if "BLOCKED" in states else "WAITING" if states & {"WAITING","BACKOFF","QUEUED","WAITING_APP"} else "OBSERVED"
    return {"schema_version": 1, "status":status, "qualification":"Queue observation only; WAITING is never CLEANED",
            "mode": "apply" if apply else "read-only", "at": now.isoformat(), "tasks": results}
