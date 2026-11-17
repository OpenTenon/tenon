#!/usr/bin/env python3
"""Explicit attributed observations; never infer human work from automatic activity."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
import re
import subprocess
from engineering_common import digest, git, source_state, utc_now
from task_store import TaskStore

FIELDS = {"intervention": "human_interventions", "rework": "rework_count"}
SHA = re.compile(r"[0-9a-f]{64}")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("Invalid observation event ID")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("A nonempty, bounded observation reason is required")
    return value


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Explicit totals must be nonnegative integers, never booleans")
    return value


def phase(task):
    status = task.get("status")
    if status == "CLEANED":
        return "cleaned"
    if status == "CANCELLED":
        return "cancelled"
    if task.get("delivery", {}).get("merged"):
        return "cleanup" if task.get("dev_ci", {}).get("conclusion") == "success" else "dev-ci"
    if status in {"VERIFYING", "VERIFIED"}:
        return "verification"
    if status in {"WAITING_CI", "PUSHING", "OPENING_PR", "MERGING"} or task.get("pr_number"):
        return "pr-ci"
    if task.get("handoff_at"):
        return "handoff"
    return "editing" if status == "ACTIVE" else "recorded-" + str(status or "unknown")


def namespace(task, create=False):
    value = task.get("observations_v1")
    if value is None:
        if not create:
            return None
        value = task["observations_v1"] = {"schema_version": 1}
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported task observation namespace")
    return value


def _events(task, stream):
    value = namespace(task)
    section = (value or {}).get(stream, {})
    if not isinstance(section, dict) or not isinstance(section.get("events", {}), dict):
        raise ValueError("Malformed observation stream")
    for identity, row in section.get("events", {}).items():
        if not isinstance(identity, str) or not isinstance(row, dict) or not isinstance(row.get("payload"), dict) or not isinstance(row.get("provenance"), dict):
            raise ValueError("Malformed observation event")
        operations = {"note", "attest"} if stream == "manual" else {"controller-start", "controller-result"}
        if row["payload"].get("operation") not in operations:
            raise ValueError("Unknown observation operation")
    return section.get("events", {})


def manual_digest(task):
    return fingerprint({key: row["payload"] for key, row in sorted(_events(task, "manual").items())
                        if row.get("payload", {}).get("operation") == "note"})


def binding(task):
    """Exclude revisions, polls, and attestation events; keep scope/source evidence."""
    return {"task_id": task.get("task_id"), "repo_id": task.get("repo_id"), "owner_id": task.get("owner_id"),
            "phase": phase(task), "started_at": task.get("started_at"),
            "candidate": task.get("head_sha") or task.get("verified_head") or task.get("source", {}).get("sha") or task.get("base_sha"),
            "source": task.get("source") or task.get("acceptance", {}).get("source") or task.get("analysis_source"),
            "full_verification": task.get("acceptance", {}).get("full_verification_sha256"),
            "evidence": task.get("evidence", []), "archive_sha256": task.get("archive", {}).get("sha256"),
            "legacy_totals": {name: task.get(name) for name in FIELDS.values()}, "manual_digest": manual_digest(task)}


def source_reference(repo: Path, task):
    candidate = task.get("head_sha") or task.get("verified_head") or task.get("delivery", {}).get("head_sha") or task.get("base_sha")
    if task.get("host_platform") != os.name:
        return {"mode": "unavailable", "reason": "Inspect source on its registered host", "candidate": candidate}
    raw = task.get("worktree")
    if not isinstance(raw, str) or (os.name != "nt" and PureWindowsPath(raw).drive):
        return {"mode": "unavailable", "reason": "No native worktree path", "candidate": candidate}
    root = Path(raw)
    try:
        if root.is_dir():
            common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
            observed = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
            if Path(common).resolve() != Path(observed).resolve():
                return {"mode": "stale", "reason": "Worktree Git identity differs", "candidate": candidate}
            state = source_state(root)
            expected = task.get("source_branch") if task.get("role") in {"coordinator", "reviewer"} else task.get("branch")
            if expected and state["branch"] != expected:
                return {"mode": "stale", "reason": "Worktree branch differs", "fingerprint": state, "candidate": candidate}
            if task.get("analysis_source") and state != task["analysis_source"]:
                return {"mode": "stale", "reason": "Frozen analysis source changed", "fingerprint": state, "candidate": candidate}
            return {"mode": "live", "fingerprint": state, "candidate": state["sha"]}
        archive = task.get("archive", {})
        if archive and re.fullmatch(r"[0-9a-f]{40}", str(candidate or "")):
            path = Path(archive["path"])
            if not path.is_file():
                return {"mode": "missing", "reason": "Historical archive is unavailable", "candidate": candidate}
            if not SHA.fullmatch(str(archive.get("sha256", ""))):
                return {"mode": "stale", "reason": "Historical archive fingerprint is missing", "candidate": candidate}
            from task_archive import verify_archive
            manifest = verify_archive(path, archive["sha256"])
            if manifest.get("repo_id") != task.get("repo_id") or manifest.get("task_id") != task.get("task_id") or manifest.get("head_sha") != candidate:
                return {"mode": "stale", "reason": "Historical archive identity differs", "candidate": candidate}
            return {"mode": "historical", "candidate": candidate, "archive_sha256": archive["sha256"],
                    "fingerprint": task.get("acceptance", {}).get("source") or task.get("source"),
                    "qualification": "Verified archive fingerprint; historical source, not live checkout"}
        return {"mode": "missing", "candidate": candidate, "reason": "Worktree and qualified archive are absent"}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return {"mode": "unavailable", "candidate": candidate, "reason": type(error).__name__}


def evidence_digest(task, source):
    return fingerprint({"binding": binding(task), "source": source})


def _identity(store, task_id, task):
    if task.get("task_id") != task_id or task.get("repo_id") != store.repo_id or task.get("host_platform") != os.name:
        raise ValueError("Observation task/host/repository identity differs")


def _append(task, stream, event_id, payload, provenance):
    existing = _events(task, stream).get(event_id)
    if existing is not None:
        if canonical(existing.get("payload")) != canonical(payload):
            raise ValueError("Observation event ID already has a different canonical payload")
        return False
    section = namespace(task, True).setdefault(stream, {"events": {}})
    section.setdefault("events", {})[event_id] = {"payload": copy.deepcopy(payload), "provenance": copy.deepcopy(provenance)}
    return True


def note(repo, task_id, event_id, kind, reason):
    _id(event_id); _text(reason)
    if kind not in FIELDS:
        raise ValueError("Observation kind must be intervention or rework")
    store = TaskStore(repo, initialize=False)
    payload = {"operation": "note", "kind": kind, "reason": reason}
    changed = []
    def update(task):
        _identity(store, task_id, task)
        existing = _events(task, "manual").get(event_id)
        if existing is not None:
            _append(task, "manual", event_id, payload, {})
            return task
        changed.append(_append(task, "manual", event_id, payload,
            {"recorded_at": utc_now(), "phase": phase(task), "source": source_reference(repo, task),
             "attribution": "explicit human-intervention declaration" if kind == "intervention" else "explicit corrective-iteration declaration"}))
        return task
    task = store.mutate(task_id, update, "observation-note", skip_unchanged=True)
    return {"status": "RECORDED" if changed else "UNCHANGED", "task_id": task_id, "event_id": event_id,
            "observations": manual_summary(task), "qualification": "Metadata only; no editing authority or resource creation"}


def attest(repo, task_id, event_id, interventions, rework, expected_digest, reason, coverage_start):
    _id(event_id); _text(reason); _count(interventions); _count(rework)
    if not isinstance(expected_digest, str) or not SHA.fullmatch(expected_digest):
        raise ValueError("The current context evidence digest is required")
    if coverage_start not in {"task-start", "tracking-start"}:
        raise ValueError("Explicit coverage start is required")
    store = TaskStore(repo, initialize=False)
    totals = {"human_interventions": interventions, "rework_count": rework}
    payload = {"operation": "attest", "totals": totals, "evidence_digest": expected_digest,
               "reason": reason, "coverage_start": coverage_start}
    changed = []
    def update(task):
        _identity(store, task_id, task)
        existing = _events(task, "manual").get(event_id)
        if existing is not None:
            _append(task, "manual", event_id, payload, {})
            return task
        source = source_reference(repo, task)
        if source["mode"] not in {"live", "historical"} or evidence_digest(task, source) != expected_digest:
            raise ValueError("Attestation source/evidence digest is unavailable or stale; refresh task context")
        observed = _note_counts(task)
        if any(totals[name] < observed[name] for name in totals):
            raise ValueError("Explicit total is below the recorded manual-event lower bound")
        if coverage_start == "task-start":
            if not task.get("started_at"):
                raise ValueError("The task coverage start is unknown")
            if any(type(task.get(name)) is int and totals[name] < task[name] for name in totals):
                raise ValueError("Explicit whole-task total cannot silently reduce an older declaration")
        events = _events(task, "manual")
        start = task.get("started_at") if coverage_start == "task-start" else min(
            (row["provenance"]["recorded_at"] for row in events.values()), default=utc_now())
        provenance = {"recorded_at": utc_now(), "source": source, "phase": phase(task),
                      "coverage": {"start": coverage_start, "at": start, "through_phase": phase(task)},
                      "binding_digest": fingerprint(binding(task)), "manual_digest": manual_digest(task),
                      "attribution": "explicit operator completeness attestation", "completeness_attested": True}
        changed.append(_append(task, "manual", event_id, payload, provenance))
        namespace(task)["manual"]["current"] = event_id
        return task
    task = store.mutate(task_id, update, "observation-attestation", skip_unchanged=True)
    return {"status": "RECORDED" if changed else "UNCHANGED", "task_id": task_id, "event_id": event_id,
            "observations": manual_summary(task), "qualification": "Explicit totals, never an inferred zero"}


def _note_counts(task):
    result = {name: 0 for name in FIELDS.values()}
    for row in _events(task, "manual").values():
        payload = row.get("payload", {})
        if payload.get("operation") == "note" and payload.get("kind") in FIELDS:
            result[FIELDS[payload["kind"]]] += 1
    return result


def manual_summary(task, source=None):
    legacy = {name: task.get(name) if type(task.get(name)) is int and task[name] >= 0 else None for name in FIELDS.values()}
    value = namespace(task)
    manual = (value or {}).get("manual")
    if manual is None:
        return {"tracking": "legacy-unobserved", "totals": legacy, "complete": None,
                "observed_events": {name: 0 for name in FIELDS.values()}, "qualification": "Legacy explicit integers retained; missing values unknown"}
    events = _events(task, "manual")
    current = events.get(manual["current"]) if manual.get("current") is not None else manual.get("handoff_declaration")
    valid = bool(current and current.get("provenance", {}).get("binding_digest") == fingerprint(binding(task))
                 and current.get("provenance", {}).get("manual_digest") == manual_digest(task))
    if valid and source is not None:
        valid = current["provenance"].get("source") == source
    coverage = current.get("provenance", {}).get("coverage") if current else None
    covered = current.get("payload", {}).get("totals", {}) if valid and coverage else {}
    covered = {name: covered.get(name) if type(covered.get(name)) is int and covered[name] >= 0 else None for name in FIELDS.values()}
    totals = covered if coverage and coverage.get("start") == "task-start" else {}
    attested = bool(current and current.get("provenance", {}).get("completeness_attested"))
    return {"tracking": "explicit-v1", "totals": {name: totals.get(name) for name in FIELDS.values()},
            "covered_totals": covered, "scope_complete": bool(valid and attested and all(value is not None for value in covered.values())),
            "complete": bool(valid and attested and all(type(totals.get(name)) is int for name in FIELDS.values())),
            "count_source": "explicit-attestation" if attested else "legacy-explicit-handoff" if current else "unattested",
            "observed_events": _note_counts(task), "legacy_declared_totals": legacy,
            "coverage": coverage, "current_event_id": manual.get("current"),
            "qualification": "Recorded explicit totals through the bound source/phase; not future unobserved work"}


def qualified_counter(task, name):
    try:
        value = manual_summary(task)["totals"].get(name)
        return value if type(value) is int and value >= 0 else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def handoff_declaration(current, merged, source, *, complete=False, expected_digest=None):
    """Pure callback helper: preserve concurrent events and the legacy int/None API."""
    if complete:
        if expected_digest != evidence_digest(current, source):
            raise ValueError("Handoff observation evidence changed; refresh context")
        if not merged.get("started_at"):
            raise ValueError("Complete handoff requires a known task coverage start")
        if any(type(merged.get(name)) is not int or merged[name] < 0 for name in FIELDS.values()):
            raise ValueError("Complete handoff requires both explicit nonnegative totals")
    if "manual" not in (namespace(current) or {}) and not complete:
        return merged
    observed = _note_counts(merged)
    for name in FIELDS.values():
        value = merged.get(name)
        if value is not None and (type(value) is not int or value < observed[name]):
            raise ValueError("Handoff total contradicts explicit observations")
    manual = namespace(merged, True).setdefault("manual", {"events": {}})
    manual.pop("current", None)
    manual["handoff_declaration"] = {
        "payload": {"operation": "legacy-handoff", "totals": {name: merged.get(name) for name in FIELDS.values()}},
        "provenance": {"recorded_at": utc_now(), "source": source, "phase": phase(merged),
            "coverage": {"start": "task-start", "at": merged.get("started_at"), "through_phase": phase(merged)},
            "binding_digest": fingerprint(binding(merged)), "manual_digest": manual_digest(merged),
            "attribution": "legacy explicit handoff integers; None remains unknown", "completeness_attested": complete}}
    return merged


def machine_events(task, queue=None, *, repo=None):
    """Merge authoritative task events and bound queue outboxes without draining."""
    from task_queue import _valid_observation
    events = copy.deepcopy(_events(task, "machine"))
    diagnostics = []
    # This proxy is only an identity/read-query context, never a TaskStore.
    reader = SimpleNamespace(repo=repo, repo_id=task.get("repo_id"))
    for entry in ([queue] if queue else []) + (queue or {}).get("history", []):
        if (entry.get("owner_id") != task.get("owner_id") or entry.get("repo_id") != task.get("repo_id")
                or entry.get("task_id") != task.get("task_id")):
            diagnostics.append("Unbound queue observation source")
            continue
        for event in entry.get("observation_outbox", []):
            try:
                _valid_observation(reader, task, entry, event)
                identity = event["id"]
                if identity in events and canonical(events[identity]["payload"]) != canonical(event["payload"]):
                    raise ValueError("Conflicting machine event " + identity)
                events.setdefault(identity, {"payload": event["payload"], "provenance": event["provenance"]})
            except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
                diagnostics.append("Unqualified pending machine observation: " + str(error))
    return events, diagnostics


def machine_summary(task, queue=None, *, repo=None):
    events, diagnostics = machine_events(task, queue, repo=repo)
    payloads = [row["payload"] for row in events.values()]
    starts = [row for row in payloads if row.get("operation") == "controller-start"]
    results = [row for row in payloads if row.get("operation") == "controller-result"]
    coverage = (namespace(task) or {}).get("machine", {}).get("coverage")
    def identity(row):
        return tuple(row.get(key) for key in ("owner_id","generation","input_candidate","ordinal"))
    open_attempts = {identity(row) for row in starts} - {identity(row) for row in results}
    if {identity(row) for row in results} - {identity(row) for row in starts}:
        diagnostics.append("Controller result has no observed start")
    return {"coverage": coverage, "unfinished_observed_attempts": len(open_attempts), "observed_controller_starts": len(starts), "observed_controller_results": len(results),
            "observed_ci_waits": sum(row.get("classification") == "ci-wait" for row in results),
            "observed_transient_retries": sum(row.get("classification") == "transient-retry" for row in results),
            "observed_resource_retries": sum(row.get("classification") == "resource-retry" for row in results),
            "covered_scope_fault_retries": sum(row.get("classification") in {"transient-retry", "resource-retry"} for row in results) if coverage and not diagnostics and not open_attempts else None,
            "whole_task_fault_retry_total": None,
            "legacy_queue_iterations": (queue or {}).get("attempts") if type((queue or {}).get("attempts")) is int else None, "diagnostics": diagnostics,
            "qualification": "Observed queued operations only; no foreground or legacy-total inference"}


def mirror_machine(store, task_id, entry):
    _identity(store, task_id, store.load(task_id))
    task = store.load(task_id)
    if entry.get("repo_id") != store.repo_id or entry.get("owner_id") != task.get("owner_id") or entry.get("task_id") != task_id:
        raise ValueError("Machine outbox ownership differs")
    def update(value):
        _identity(store, task_id, value)
        for event in entry.get("observation_outbox", []):
            _append(value, "machine", event["id"], event["payload"], event["provenance"])
        if entry.get("observation_coverage"):
            namespace(value, True).setdefault("machine", {"events": {}}).setdefault("coverage", copy.deepcopy(entry["observation_coverage"]))
        return value
    return store.mutate(task_id, update, "machine-observations", skip_unchanged=True)
