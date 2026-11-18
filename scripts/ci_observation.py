"""Pure, allowlisted accepted-CI observations; never delivery authorization."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone


TIMINGS = ("hosted_span_seconds", "all_job_wall_seconds", "windows_job_wall_seconds", "linux_job_wall_seconds",
           "ci_complete_to_confirmation_seconds", "confirmation_to_cleaned_seconds")


def _integer(value):
    return value if type(value) is int and value > 0 else None


def _text(value):
    return value if isinstance(value, str) and len(value) <= 512 else None


def _sha(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _elapsed(first, last):
    return (last - first).total_seconds() if first is not None and last is not None and last >= first else None


def accepted_record(snapshot, *, task_id, repository, candidate_head, accepted_head, event, observed_at, previous=None):
    """Called only after the existing live gate succeeds; invalid clocks stay raw/unknown."""
    run = snapshot["workflow_run"]
    result = {"run_id": run["id"], "head_sha": accepted_head, "conclusion": "success"}
    previous = previous if isinstance(previous, dict) else {}
    # Existing accepted compact records have no attempt/first-confirmation
    # clock. A resume must not manufacture that historical observation now.
    if (previous.get("run_id") == run["id"] and previous.get("head_sha") == accepted_head
            and previous.get("conclusion") == "success" and previous.get("observation") is None):
        return result
    jobs = snapshot.get("jobs")
    retained = None
    if isinstance(jobs, list) and len(jobs) <= 128 and all(isinstance(job, dict) for job in jobs):
        retained = []
        for job in jobs:
            labels = job.get("labels")
            labels = list(labels) if (isinstance(labels, list) and len(labels) <= 64
                                      and all(isinstance(label, str) and len(label) <= 256 for label in labels)) else None
            retained.append({"name": _text(job.get("name")), "run_id": _integer(job.get("run_id")),
                "run_attempt": _integer(job.get("run_attempt")), "head_sha": _sha(job.get("head_sha")),
                "status": _text(job.get("status")), "conclusion": _text(job.get("conclusion")),
                "started_at": _text(job.get("started_at")), "completed_at": _text(job.get("completed_at")), "labels": labels})
    pull = snapshot.get("pull_request") or {}
    observation = {"schema_version": 1, "task_id": _text(task_id), "repository": _text(repository),
        "event": _text(run.get("event")), "run_id": _integer(run.get("id")), "run_attempt": _integer(run.get("run_attempt")),
        "candidate_head_sha": _sha(candidate_head), "accepted_head_sha": _sha(accepted_head),
        "provider_head_sha": _sha(run.get("head_sha")),
        "pr_merge_head_sha": _sha(pull.get("merge_commit_sha")) if event == "pull_request" else None,
        "status": _text(run.get("status")), "conclusion": _text(run.get("conclusion")),
        "observed_at": _text(observed_at), "jobs": retained}
    prior = previous.get("observation")
    identity = ("task_id", "repository", "event", "run_id", "run_attempt", "candidate_head_sha", "accepted_head_sha", "provider_head_sha")
    if (isinstance(prior, dict) and type(prior.get("schema_version")) is int and prior["schema_version"] == 1
            and observation["candidate_head_sha"] and observation["accepted_head_sha"] and observation["provider_head_sha"]
            and all(prior.get(key) == observation[key] for key in identity)):
        observation = copy.deepcopy(prior)
    result["observation"] = observation
    return result


def recovered_pr_record(record, head):
    """Carry a previous merge intent through ambiguous merge recovery; never backfill it."""
    if (not isinstance(record, dict) or _integer(record.get("run_id")) is None
            or record.get("head_sha") != head or record.get("conclusion") != "success"):
        return None
    return copy.deepcopy(record)


def phase_metrics(records, *, task_id, repository, candidate_head, accepted_head, event, required_jobs, cleaned_at=None):
    """Reduce only a matching accepted attempt; no clocks inferred from journals/mtime."""
    result = {name: None for name in TIMINGS}
    result.update(run_id=None, run_attempt=None, candidate_head_sha=None, provider_head_sha=None,
                  runner_labels_by_job=None, runner_layout_sha256=None,
                  ci_completed_at=None, controller_observed_at=None, qualification="unknown: legacy or missing accepted CI observation")
    records = [record for record in records if isinstance(record, dict) and record.get("observation") is not None]
    if not records:
        return result
    observations = [record.get("observation") for record in records]
    value = observations[0]
    if not isinstance(value, dict) or any(item != value for item in observations):
        result["qualification"] = "unknown: invalid or conflicting accepted CI observations"
        return result
    valid = (type(value.get("schema_version")) is int and value["schema_version"] == 1
        and _integer(value.get("run_id")) is not None and _integer(value.get("run_attempt")) is not None
        and isinstance(task_id, str) and value.get("task_id") == task_id
        and isinstance(repository, str) and repository and value.get("repository") == repository
        and _sha(candidate_head) is not None and value.get("candidate_head_sha") == candidate_head
        and _sha(accepted_head) is not None and value.get("accepted_head_sha") == accepted_head
        and value.get("event") == event and value.get("status") == "completed" and value.get("conclusion") == "success")
    candidate_sha, accepted_sha = _sha(candidate_head), _sha(accepted_head)
    merge_sha = _sha(value.get("pr_merge_head_sha"))
    valid = valid and (value.get("pr_merge_head_sha") is None or merge_sha is not None)
    allowed_heads = {head for head in (candidate_sha, merge_sha) if head is not None} if event == "pull_request" else {head for head in (accepted_sha,) if head is not None}
    valid = valid and _sha(value.get("provider_head_sha")) is not None and value["provider_head_sha"] in allowed_heads
    valid = valid and all(_integer(record.get("run_id")) == value["run_id"] and record.get("head_sha") == accepted_head
                          and record.get("conclusion") == "success" for record in records)
    jobs = value.get("jobs")
    if not valid or not isinstance(jobs, list) or not jobs or len(jobs) > 128 or any(not isinstance(job, dict) for job in jobs):
        result["qualification"] = "unknown: CI observation ownership or inventory differs"
        return result
    names = [job.get("name") for job in jobs]
    if (any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names))
            or not set(required_jobs).issubset(names)
            or any(job.get("run_id") != value["run_id"] or type(job.get("run_id")) is not int
                   or job.get("run_attempt") != value["run_attempt"] or type(job.get("run_attempt")) is not int
                   or job.get("status") != "completed" or job.get("conclusion") != "success"
                   or _sha(job.get("head_sha")) is None or job["head_sha"] not in allowed_heads for job in jobs)):
        result["qualification"] = "unknown: CI jobs do not qualify for the accepted run/attempt"
        return result
    result.update(run_id=value["run_id"], run_attempt=value["run_attempt"], candidate_head_sha=candidate_head,
                  provider_head_sha=value["provider_head_sha"], qualification="accepted attempt observation; timing clocks are qualified separately")
    # These are verified job selectors, not evidence of the hosted image or
    # installed runtime. Missing selectors must never come from local tooling.
    if all(isinstance(job.get("labels"), list) and 0 < len(job["labels"]) <= 64
           and all(isinstance(label, str) and label and len(label) <= 256 for label in job["labels"]) for job in jobs):
        layout = {job["name"]: sorted(set(job["labels"])) for job in sorted(jobs, key=lambda job: job["name"])}
        result["runner_labels_by_job"] = layout
        result["runner_layout_sha256"] = hashlib.sha256(json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    observed = _time(value.get("observed_at"))
    result["controller_observed_at"] = observed.isoformat() if observed is not None else None
    result["confirmation_to_cleaned_seconds"] = _elapsed(observed, _time(cleaned_at))
    starts, ends = [_time(job.get("started_at")) for job in jobs], [_time(job.get("completed_at")) for job in jobs]
    durations = [_elapsed(start, end) for start, end in zip(starts, ends)]
    if any(duration is None for duration in durations):
        return result
    completed = max(ends)
    result.update(hosted_span_seconds=(completed-min(starts)).total_seconds(), all_job_wall_seconds=sum(durations),
                  ci_completed_at=completed.isoformat(), ci_complete_to_confirmation_seconds=_elapsed(completed, observed))
    platforms = []
    for job in jobs:
        labels = job.get("labels")
        if not isinstance(labels, list) or not labels or any(not isinstance(label, str) for label in labels):
            platforms.append(None)
            continue
        labels = [label.lower() for label in labels]
        windows = any(label == "windows" or label.startswith("windows-") for label in labels)
        linux = any(label in {"linux", "ubuntu"} or label.startswith("ubuntu-") for label in labels)
        platforms.append("windows" if windows and not linux else "linux" if linux and not windows else None)
    if all(platform is not None for platform in platforms):
        for platform in ("windows", "linux"):
            result[platform+"_job_wall_seconds"] = sum(duration for name, duration in zip(platforms, durations) if name == platform)
    return result
