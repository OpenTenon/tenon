#!/usr/bin/env python3
"""Read-only engineering observations; source records are facts, never signoff claims."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


CURRENT_FAILURES = {"BLOCKED", "NEEDS_ATTENTION", "DEV_CI_FAILED"}
FAILURE_ACTIONS = {"verification-failed", "ci-failed", "dev-ci-failed", "operation-blocked", "pr-ci-unconfirmed"}
RESOURCE_KEYS = ("directory", "registration", "local_ref", "tracking_ref", "remote_ref")


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _as_of(value=None):
    result = value if isinstance(value, datetime) else timestamp(value) if value is not None else datetime.now(timezone.utc)
    if result is None or result.tzinfo is None:
        raise ValueError("Metrics require a timezone-aware observation time")
    return result.astimezone(timezone.utc)


def elapsed(start, end):
    first, last = timestamp(start), timestamp(end)
    if first is None or last is None or last < first:
        return None
    return (last - first).total_seconds()


def distribution(values, total=None):
    known = sorted(float(value) for value in values if type(value) in {int, float} and math.isfinite(value) and value >= 0)
    def rank(percent):
        return known[max(0, math.ceil(percent * len(known)) - 1)] if known else None
    return {"samples": len(known), "missing": max(0, (total if total is not None else len(values)) - len(known)),
            "p50_seconds": rank(0.50), "p95_seconds": rank(0.95), "percentile_method": "nearest-rank",
            "min_seconds": known[0] if known else None, "max_seconds": known[-1] if known else None}


def _counter(tasks, name):
    values = [task.get(name) for task in tasks if type(task.get(name)) is int and task[name] >= 0]
    return {"samples": len(values), "unknown": len(tasks) - len(values), "known_total": sum(values) if values else None}


def _events(rows):
    result, seen = [], set()
    for index, event in enumerate(rows):
        if not isinstance(event, dict) or timestamp(event.get("at")) is None:
            continue
        revision = event.get("revision")
        key = ("revision", revision) if type(revision) is int else ("position", index)
        if key not in seen:
            seen.add(key)
            result.append(event)
    return sorted(result, key=lambda event: timestamp(event["at"]))


def _wait_seconds(events, status):
    start, total, intervals = None, 0.0, 0
    for event in events:
        state = event.get("state")
        observed = state.get("status") if isinstance(state, dict) else None
        if not isinstance(observed, str):
            continue
        at = event["at"]
        if observed == status and start is None:
            start = at
        elif observed != status and start is not None:
            value = elapsed(start, at)
            if value is not None:
                total += value
                intervals += 1
            start = None
    return (total if intervals and start is None else None), start


def _full_attempts(task):
    rows, seen, ambiguous = [], {}, False
    blocked = not_run = unclassified = 0
    attempts = task.get("verification_attempts", [])
    if not isinstance(attempts, list):
        attempts = [None]
    for attempt in attempts:
        if not isinstance(attempt, dict):
            unclassified += 1
            continue
        if attempt.get("group") != "all":
            unclassified += attempt.get("group") is None
            continue
        blocked += attempt.get("status") == "BLOCKED"
        not_run += attempt.get("status") == "NOT_RUN"
        if attempt.get("status") not in {"PASS", "FAIL"}:
            continue
        identity = attempt.get("run_id")
        if not isinstance(identity, str) or not identity:
            ambiguous = True
            continue
        if identity in seen:
            ambiguous |= seen[identity] != attempt
            continue
        seen[identity] = attempt
        rows.append(attempt)
    if any(timestamp(row.get("started_at")) is None for row in rows):
        ambiguous = True
    rows.sort(key=lambda row: timestamp(row.get("started_at")) or datetime.max.replace(tzinfo=timezone.utc))
    first = rows[0]["status"] if rows and not ambiguous else None
    durations = [elapsed(row.get("started_at"), row.get("completed_at")) for row in rows]
    return {"first": first, "executed": len(rows), "blocked": blocked, "not_run": not_run,
            "unclassified": unclassified, "ordering_unknown": ambiguous,
            "duration_seconds": sum(durations) if durations and not ambiguous and all(value is not None for value in durations) else None}


def summarize(tasks, events=None, queues=None, observations=None, as_of=None, worker=None, diagnostics=None):
    """Pure reducer. Inputs are snapshots; missing values never become zero successes."""
    now = _as_of(as_of)
    events, queues, observations = events or {}, queues or {}, observations or {}
    tasks = list(tasks)
    result_rows, stage_values = [], {}
    firsts, historical, remaining, contradictions, resource_unknown = [], Counter(), [], [], []
    for task in tasks:
        identifier = task["task_id"]
        journal = _events(events.get(identifier, []))
        queue = queues.get(identifier, {})
        resource = observations.get(identifier, {})
        delivery = task.get("delivery", {})
        merged = delivery.get("merged") is True
        delivered = task.get("delivered_at") or delivery.get("merged_at")
        cleaned = task.get("status") == "CLEANED"
        full = _full_attempts(task)
        if full["first"] is not None:
            firsts.append(full["first"])
        failed_events = [event for event in journal if event.get("action") in FAILURE_ACTIONS]
        historical.update(event["action"] for event in failed_events)
        current_failure = not cleaned and (task.get("status") in CURRENT_FAILURES or queue.get("state") == "BLOCKED")
        green = next((event["at"] for event in journal if event.get("action") == "dev-ci-green"
                      and timestamp(delivered) is not None and timestamp(event["at"]) >= timestamp(delivered)), None)
        pr_wait, pr_open = _wait_seconds(journal, "WAITING_CI")
        stage = {"start_to_merge": elapsed(task.get("started_at"), delivered) if merged else None,
                 "full_verification_execution": full["duration_seconds"],
                 "observed_pr_ci_wait": pr_wait,
                 "post_merge_until_green": elapsed(delivered, green) if merged else None,
                 "merge_to_cleaned": elapsed(delivered, task.get("cleaned_at")) if merged and cleaned else None,
                 "green_to_cleaned": elapsed(green, task.get("cleaned_at")) if cleaned else None,
                 "actual_ci_execution": resource.get("ci_execution_seconds")}
        for name, value in stage.items():
            stage_values.setdefault(name, []).append(value)
        applicable = resource.get("applicable", task.get("owns_workspace", True))
        known_values = [resource.get(key) for key in RESOURCE_KEYS]
        has_resources = applicable and any(value is True for value in known_values)
        unknown_resources = applicable and (not resource or any(value is None for value in known_values))
        if merged and has_resources:
            remaining.append(identifier)
        if cleaned and has_resources:
            contradictions.append(identifier)
        if merged and unknown_resources:
            resource_unknown.append(identifier)
        due = timestamp(queue.get("next_attempt_at"))
        row = {"task_id": identifier, "kind": task.get("kind", "unclassified"), "status": task.get("status"),
               "delivered": merged, "cleaned_recorded": cleaned, "current_failure": current_failure,
               "failure_stage": "cleanup" if merged and current_failure else "delivery_or_verification" if current_failure else None,
               "historical_failure_events": len(failed_events), "historical_failure_revisions": [event.get("revision") for event in failed_events],
               "first_full_verification": full, "stages_seconds": stage,
               "pending_cleanup_age_seconds": elapsed(delivered, now.isoformat()) if merged and not cleaned else None,
               "open_pr_ci_wait_age_seconds": elapsed(pr_open, now.isoformat()) if pr_open else None,
               "queue_state": queue.get("state"), "queue_stage": queue.get("stage"), "queue_generation": queue.get("generation"),
               "queue_attempts": queue.get("attempts"), "queue_due": bool(due and due <= now and queue.get("state") in {"QUEUED", "WAITING"}),
               "queue_age_seconds": elapsed(queue.get("queued_at"), now.isoformat()) if queue and queue.get("state") != "DONE" else None,
               "resources": resource, "observed_resources_remaining": bool(has_resources),
               "resource_completeness_unknown": bool(unknown_resources),
               "recorded_remote_deleted": task.get("cleanup", {}).get("remote_deleted"),
               "actor_lease": task.get("execution_lease"), "started_at": task.get("started_at"), "delivered_at": delivered}
        result_rows.append(row)
    delivered_count = sum(row["delivered"] for row in result_rows)
    worker = copy.deepcopy(worker or {})
    worker["snapshot_age_seconds"] = elapsed(worker.get("completed_at"), now.isoformat())
    return {"schema_version": 1, "kind": "engineering-metrics", "as_of": now.isoformat(), "timezone": "UTC",
            "qualification": "Recorded engineering integration and observations; no hardware, release or productivity-gain inference",
            "tasks": len(tasks), "finished_tasks": sum(bool(task.get("completed_at") or task.get("delivered_at") or task.get("delivery", {}).get("merged")) for task in tasks),
            "delivered_tasks": delivered_count, "cleaned_tasks": sum(row["cleaned_recorded"] for row in result_rows),
            "cleanup_pending_tasks": sum(row["delivered"] and not row["cleaned_recorded"] for row in result_rows),
            "baseline_required_deliveries": 20, "baseline_ready": delivered_count >= 20,
            "baseline_note": "Twenty confirmed deliveries are a sample threshold, not proof of improvement",
            "counters": {name: _counter(tasks, name) for name in ("human_interventions", "rework_count")},
            "full_verification_first_pass": {"samples": len(firsts), "unknown_tasks": len(tasks) - len(firsts),
                "passes": firsts.count("PASS"), "failures": firsts.count("FAIL"),
                "fraction": firsts.count("PASS") / len(firsts) if firsts else None,
                "blocked_attempts": sum(row["first_full_verification"]["blocked"] for row in result_rows),
                "not_run_attempts": sum(row["first_full_verification"]["not_run"] for row in result_rows),
                "qualification": "First timestamp-ordered executed group=all PASS/FAIL per task; recorded classification, not independent coverage proof"},
            "stages": {name: distribution(values, len(tasks)) for name, values in stage_values.items()},
            "failures": {"current_tasks": [row["task_id"] for row in result_rows if row["current_failure"]],
                "historical_event_counts": dict(historical), "historical_tasks": [row["task_id"] for row in result_rows if row["historical_failure_events"]]},
            "resource_health": {"observed_remaining_tasks": remaining, "cleaned_but_resources_present": contradictions,
                "incomplete_observation_tasks": resource_unknown, "remote_probe": "explicit-only"},
            "worker": worker, "per_task": result_rows, "diagnostics": list(diagnostics or [])}


def _git(repo, *args):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), *args], stderr=subprocess.PIPE, timeout=20)


def _json(path, diagnostics):
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("JSON observation exceeds bounded size")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON observation is not an object")
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        diagnostics.append({"path": str(path), "issue": type(error).__name__})
        return None


def _journal(path, identifier, repo_id, diagnostics):
    result, latest = [], None
    try:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.endswith("\n"):
                    diagnostics.append({"path": str(path), "line": number, "issue": "partial-journal-tail"})
                    break
                try:
                    event = json.loads(line)
                    state = event["state"]
                    if (type(event.get("revision")) is not int or not isinstance(state, dict) or state.get("revision") != event["revision"] or state.get("task_id") != identifier
                            or state.get("repo_id") != repo_id or state.get("schema_version") != 2):
                        raise ValueError("Journal identity/schema differs")
                except (ValueError, KeyError, TypeError):
                    diagnostics.append({"path": str(path), "line": number, "issue": "invalid-journal-event"})
                    break
                if latest is None or event["revision"] > latest.get("revision", 0):
                    latest = state
                result.append({"revision": event["revision"], "at": event.get("at"), "action": event.get("action"),
                               "state": {"status": state.get("status"), "head_sha": state.get("head_sha")}})
    except FileNotFoundError:
        pass
    except OSError as error:
        diagnostics.append({"path": str(path), "issue": type(error).__name__})
    return result, latest


def _directory(path):
    try:
        os.stat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None


def _lease(path, platform):
    try:
        os.stat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    if platform != os.name:
        return None
    try:
        # The shared cleanup probe conservatively treats access denial as held.
        # A metric must distinguish an unavailable observation from a real lock.
        with path.open("r+b"):
            pass
        from task_store import lease_is_held
    except (OSError, ImportError):
        return None
    return lease_is_held(path)


def collect(repo, *, as_of=None, probe_remote=False):
    """Read snapshots only. No TaskStore initialization, repair, fetch, or default network access."""
    repo, now = Path(repo).resolve(), _as_of(as_of)
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    base, diagnostics = common / "tenon", []
    identity = _json(base / "repository.json", diagnostics)
    if identity is None:
        report = summarize([], as_of=now, diagnostics=diagnostics)
        report.update(initialized=False, repository_path=str(repo))
        return report
    repo_id = identity.get("repo_id")
    if identity.get("schema_version") != 1 or not isinstance(repo_id, str) or not repo_id:
        report = summarize([], as_of=now, diagnostics=[*diagnostics, {"issue":"invalid-repository-identity"}])
        report.update(initialized=False, repository_path=str(repo))
        return report
    tasks, events, queues, observed = [], {}, {}, {}
    names = sorted({path.stem for path in (base / "tasks").glob("*") if path.suffix in {".json", ".jsonl"}})
    for identifier in names:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier):
            diagnostics.append({"task_id": identifier, "issue": "invalid-task-id"})
            continue
        journal, recovered = _journal(base / "tasks" / (identifier + ".jsonl"), identifier, repo_id, diagnostics)
        snapshot = _json(base / "tasks" / (identifier + ".json"), diagnostics)
        candidates = [value for value in (snapshot, recovered) if value and value.get("schema_version") == 2
                      and value.get("repo_id") == repo_id and value.get("task_id") == identifier and type(value.get("revision")) is int]
        if not candidates:
            diagnostics.append({"task_id": identifier, "issue": "no-valid-task-snapshot"})
            continue
        value = copy.deepcopy(max(candidates, key=lambda item: item["revision"]))
        attempts = value.setdefault("verification_attempts", [])
        if not isinstance(attempts, list):
            diagnostics.append({"task_id":identifier,"issue":"invalid-verification-attempts"})
            attempts = value["verification_attempts"] = []
        for path in sorted((base / "inbox" / identifier).glob("*.json")):
            item = _json(path, diagnostics)
            if item and item.get("repo_id") == repo_id and item.get("task_id") == identifier and isinstance(item.get("attempt"), dict):
                attempt = item["attempt"]
                # A matching identity is not proof that the observation agrees.
                # Keep contradictory same-run records for the reducer to mark
                # ambiguous; only exact duplicate observations can be collapsed.
                if not any(row == attempt for row in attempts):
                    attempts.append(attempt)
        tasks.append(value)
        events[identifier] = journal
        queued = _json(base / "queue" / (identifier + ".json"), diagnostics)
        if queued and queued.get("schema_version") == 1 and queued.get("repo_id") == repo_id and queued.get("task_id") == identifier:
            queues[identifier] = queued
    try:
        refs = dict(line.split(" ", 1) for line in _git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes/origin").decode().splitlines())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        refs = None
        diagnostics.append({"issue": "local-ref-observation-unavailable", "detail": type(error).__name__})
    try:
        try:
            fields = _git(repo, "worktree", "list", "--porcelain", "-z").decode().split("\0")
        except subprocess.CalledProcessError:
            # Ubuntu 22.04's existing Git may lack worktree list -z. Retain
            # independent ref facts and use its stable porcelain fallback.
            fields = _git(repo, "worktree", "list", "--porcelain").decode().splitlines()
        registered = set()
        for field in fields:
            if field.startswith("worktree "):
                raw = field[9:]
                if raw.startswith('"'):
                    raise ValueError("Quoted worktree path requires a newer NUL-delimited Git interface")
                registered.add(os.path.normcase(str(Path(raw).resolve())))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        registered = None
        diagnostics.append({"issue": "worktree-observation-unavailable", "detail": type(error).__name__})
    remote = None
    if probe_remote:
        try:
            output = _git(repo, "-c", "core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout=10", "ls-remote", "--heads", "origin")
            remote = {name: sha for sha, name in (line.split(None, 1) for line in output.decode().splitlines())}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            diagnostics.append({"issue": "remote-observation-unavailable", "detail": type(error).__name__})
    leases = []
    for path in (base / "leases").glob("*.json"):
        item = _json(path, diagnostics)
        if item:
            leases.append(item)
    for task in tasks:
        identifier, branch = task["task_id"], task.get("branch")
        applicable = task.get("owns_workspace", True) is not False
        path = Path(task["worktree"]) if isinstance(task.get("worktree"), str) else None
        native_path = path is not None and path.is_absolute() and task.get("host_platform") in {None, os.name}
        observed[identifier] = {"applicable": applicable,
            "directory": _directory(path) if native_path and applicable else None,
            "registration": os.path.normcase(str(path.resolve())) in registered if native_path and applicable and registered is not None else None,
            "local_ref": "refs/heads/" + branch in refs if branch and refs is not None and applicable else None,
            "tracking_ref": "refs/remotes/origin/" + branch in refs if branch and refs is not None and applicable else None,
            "remote_ref": "refs/heads/" + branch in remote if branch and remote is not None and applicable else None,
            "controller_lease_held": _lease(base / "locks" / (identifier + "-controller.lock"), task.get("host_platform")),
            "recorded_command_lease_files": sum(item.get("task_id") == identifier for item in leases),
            "command_liveness": "unknown; lease files do not prove PID identity"}
    worker = _json(base / "operations/worker-latest.json", diagnostics) or {}
    worker = {key: worker.get(key) for key in ("completed_at", "enabled", "host_platform", "pid", "mode")}
    worker["lease_held"] = _lease(base / "locks/worker.lock", worker.get("host_platform"))
    config = _json(base / "worker.json", diagnostics)
    worker["configuration_enabled"] = config.get("enabled") if config and config.get("schema_version")==1 and config.get("repo_id")==repo_id else None
    report = summarize(tasks, events, queues, observed, now, worker, diagnostics)
    report.update(initialized=True, repository_path=str(repo), repo_id=repo_id, remote_probe_requested=probe_remote)
    return report


def improvement_items(report, month=None):
    """Actionable evidence only; never execute repairs or invent human/rework counts."""
    now = _as_of(report["as_of"])
    month = month or now.strftime("%Y-%m")
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as error:
        raise ValueError("Improvement month must be YYYY-MM") from error
    if start.strftime("%Y-%m") != month:
        raise ValueError("Improvement month must be YYYY-MM")
    end = datetime(start.year+1, 1, 1, tzinfo=timezone.utc) if start.month==12 else datetime(start.year, start.month+1, 1, tzinfo=timezone.utc)
    rows = report.get("per_task", [])
    selected = [row for row in rows if any(start <= observed < end for key in ("delivered_at","started_at")
                if (observed := timestamp(row.get(key))) is not None)]
    items = []
    def add(code, priority, identifiers, action, evidence):
        if identifiers:
            items.append({"code": code, "priority": priority, "task_ids": sorted(set(identifiers)), "action": action, "evidence": evidence})
    add("current-blockers", "P1", [row["task_id"] for row in rows if row["current_failure"]],
        "Inspect current task/queue blockers and resume only after the cause is resolved", "Current status and queue state; stale historical error fields excluded")
    add("cleanup-reality-differs", "P1", report.get("resource_health", {}).get("cleaned_but_resources_present", []),
        "Reconcile only the owned resources after fresh CI and archive gates; preserve new work", "CLEANED record contradicts observed resource presence")
    add("pending-qualified-cleanup", "P1", [row["task_id"] for row in rows if row["delivered"] and not row["cleaned_recorded"]
        and row.get("pending_cleanup_age_seconds") is not None and row["pending_cleanup_age_seconds"] >= 300],
        "Inspect CI qualification, native receipts and resource blockers; cleanup age alone is not permission to delete", "Recorded merge plus current pending state")
    add("full-verification-failures", "P2", [row["task_id"] for row in selected if row["first_full_verification"]["first"] == "FAIL"],
        "Review the recorded full-verification logs and add a regression after verifying the cause", "Timestamp-ordered executed group=all first attempt")
    stale = report.get("worker", {}).get("snapshot_age_seconds")
    due = [row["task_id"] for row in rows if row.get("queue_due")]
    if due and (stale is None or stale >= 600) and report.get("worker", {}).get("lease_held") is not True:
        add("due-queue-worker-observation", "P1", due,
            "Inspect the registered worker schedule and prerequisites; a stored PID is not liveness proof", "Due queue entries and stale/absent worker observation")
    if not report.get("baseline_ready"):
        items.append({"code": "insufficient-delivery-baseline", "priority": "P2", "task_ids": [],
            "action": "Collect more confirmed deliveries before interpreting productivity trends",
            "evidence": {"delivered": report.get("delivered_tasks", 0), "required": 20}})
    unknown = {key: value["unknown"] for key, value in report.get("counters", {}).items() if value.get("unknown")}
    if unknown:
        items.append({"code": "explicit-counter-coverage", "priority": "P2", "task_ids": [],
            "action": "Record explicit future intervention/rework observations; do not backfill estimates", "evidence": unknown})
    return {"schema_version": 1, "month": month, "timezone": "UTC", "items": items,
            "qualification": "Evidence-linked suggestions only; no repairs or delivery authorization"}
