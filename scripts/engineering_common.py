#!/usr/bin/env python3
"""Shared source fingerprints, bounded execution and local verification evidence."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(root), *args], stderr=subprocess.PIPE)


def source_state(root: Path = ROOT) -> dict:
    """Hash tracked AND untracked, non-ignored files, including staged/deleted changes."""
    names = set(git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0"))
    content = hashlib.sha256()
    for raw in sorted(name for name in names if name):
        name = raw.decode("utf-8")
        path = root / name
        content.update(raw + b"\0")
        if path.is_symlink():
            target = path.resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError("Source symlink escapes the repository: " + name)
            content.update(b"SYMLINK\0" + os.readlink(path).encode("utf-8"))
            if target.is_file():
                content.update(hashlib.sha256(target.read_bytes()).digest())
        elif path.is_file():
            content.update(b"FILE\0" + hashlib.sha256(path.read_bytes()).digest())
        else:
            content.update(b"MISSING\0")
    diff = git(root, "diff", "--binary", "HEAD", "--")
    # The complete worktree hash also covers untracked files, absent from git diff.
    return {
        "sha": git(root, "rev-parse", "HEAD").decode().strip(),
        "branch": git(root, "branch", "--show-current").decode().strip(),
        "worktree_sha256": content.hexdigest(),
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "dirty": bool(git(root, "status", "--porcelain=v1", "--untracked-files=all")),
    }


def aggregate(checks: list[dict]) -> str:
    if not checks:
        return "NOT_RUN"
    required = [item for item in checks if item.get("required", True)]
    if not required:
        return "NOT_RUN"
    for state in ("FAIL", "BLOCKED", "NOT_RUN"):
        if any(item.get("status") == state for item in required):
            return state
    return "PASS" if all(item.get("status") == "PASS" for item in required) else "FAIL"


def status_exit(status: str) -> int:
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "NOT_RUN": 2}[status]


def stop_process(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def execute(command: list[str], cwd: Path, log: Path, timeout: float = 900,
            dry_run: bool = False, check_id: str = "command", coverage: object = None,
            env: dict | None = None) -> dict:
    result = {"id": check_id, "required": True, "command": command, "cwd": str(cwd),
              "status": "NOT_RUN", "exit_code": None, "duration_seconds": 0,
              "coverage": coverage, "log": log.name, "log_sha256": None}
    if dry_run:
        return result
    started = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as output:
        try:
            options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                       else {"start_new_session": True})
            with subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
                                  stderr=subprocess.STDOUT, **options) as process:
                try:
                    code = process.wait(timeout=timeout)
                    result.update(status="PASS" if code == 0 else "FAIL", exit_code=code)
                except subprocess.TimeoutExpired:
                    stop_process(process)
                    output.write(b"\nTIMEOUT: process tree terminated.\n")
                    result.update(status="FAIL", reason="timeout", exit_code=124)
                except BaseException:
                    stop_process(process)
                    raise
        except OSError as error:
            output.write((str(error) + "\n").encode("utf-8"))
            result.update(status="BLOCKED", reason=str(error))
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    result["log_sha256"] = digest(log)
    return result


def new_summary(root: Path, kind: str, identifier: str | None = None) -> dict:
    return {"schema_version": SCHEMA_VERSION, "run_id": identifier or run_id(), "kind": kind,
            "started_at": utc_now(), "completed_at": None, "source": source_state(root),
            "host": socket.gethostname(), "platform": os.name, "tools": {}, "checks": [],
            "status": "NOT_RUN", "qualification": "local-evidence-only", "signoff": False,
            "exceptions": [], "skipped_steps": []}


def finish_summary(summary: dict, root: Path, path: Path, readonly: bool = True) -> dict:
    if not any("command" in item and item.get("exit_code") is not None for item in summary["checks"]):
        summary["checks"].append({"id": "execution-not-performed", "required": True, "status": "NOT_RUN",
                                  "reason": "No check process actually completed"})
    if readonly:
        after = source_state(root)
        unchanged = after == summary["source"]
        summary["checks"].append({"id": "repository-unchanged", "required": True,
                                  "status": "PASS" if unchanged else "FAIL",
                                  "before": summary["source"], "after": after})
    summary["status"] = aggregate(summary["checks"])
    summary["completed_at"] = utc_now()
    write_json(path, summary)
    return summary


def validate_evidence(path: Path, root: Path, required: set[str] | None = None) -> dict:
    """Validate local evidence, never infer CI execution or signoff from its contents."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != SCHEMA_VERSION or value.get("status") != "PASS":
        raise ValueError("Evidence schema/status is not acceptable")
    if not value.get("completed_at") or not value.get("run_id"):
        raise ValueError("Evidence is incomplete")
    actual = source_state(root)
    if value.get("source") != actual:
        raise ValueError("Evidence belongs to a different SHA or worktree fingerprint")
    if value.get("kind") == "pdk-verification":
        interfaces = value.get("pdk", {}).get("required_files", [])
        if not interfaces:
            raise ValueError("External PDK interface evidence is missing")
        for interface in interfaces:
            if not interface.get("exists") or not interface.get("sha256") or digest(Path(interface["path"])) != interface["sha256"]:
                raise ValueError("External PDK verification inputs changed")
    checks = value.get("checks")
    if not isinstance(checks, list) or aggregate(checks) != "PASS":
        raise ValueError("Evidence has missing, failed, blocked or unexecuted checks")
    if not any("command" in check and check.get("exit_code") is not None for check in checks):
        raise ValueError("Evidence contains no executed checks")
    ids = [check.get("id") for check in checks]
    if len(ids) != len(set(ids)) or not (required or set()).issubset(set(ids)):
        raise ValueError("Evidence check inventory is incomplete or duplicated")
    for check in checks:
        if check.get("status") not in STATUSES:
            raise ValueError("Unknown evidence check status")
        if check.get("id") in (required or set()) and (check.get("status") != "PASS" or not check.get("required", True)):
            raise ValueError("A required check was made optional or did not pass")
        if "command" not in check:
            continue
        if not isinstance(check["command"], list) or not check["command"] or check.get("exit_code") != 0:
            raise ValueError("Evidence contains an unexecuted command")
        log = (path.parent / str(check.get("log", ""))).resolve()
        if not log.is_relative_to(path.parent.resolve()) or not log.is_file():
            raise ValueError("Evidence log is missing or outside the run directory")
        if check.get("log_sha256") != digest(log):
            raise ValueError("Evidence log hash does not match")
    return value


class PhysicalLock:
    """Workspace-wide exclusive lease. An interrupted lease requires human inspection."""
    def __init__(self, root: Path, identifier: str):
        self.path = root / "build" / "locks" / "physical.lock"
        self.identifier = identifier

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"run_id": self.identifier, "pid": os.getpid(), "host": socket.gethostname(),
                       "created_at": utc_now()}, handle)
        return self

    def __exit__(self, *_):
        self.path.unlink()


def record_attempt(root: Path, report: Path, summary: dict) -> None:
    """Persist task attempts outside disposable worktrees under a task transaction."""
    from task_store import TaskStore
    store = TaskStore(root)
    task = store.for_worktree(root)
    if not task or task.get("status") in {"CLEANED"}:
        return
    attempt = {"run_id":summary["run_id"],"report_relative":report.relative_to(root).as_posix(),
               "report":str(report),"sha256":digest(report),"source":summary["source"],
               "status":summary["status"],"group":summary.get("group"),
               "started_at":summary["started_at"],"completed_at":summary["completed_at"],
               "preparation":summary.get("preparation",[])}
    if task.get("host_platform")!=os.name:
        store.enqueue_attempt(task["task_id"],attempt)
        return
    def append(value):
        attempts = value.setdefault("verification_attempts", [])
        if not any(item.get("run_id")==summary["run_id"] for item in attempts):
            attempts.append(attempt)
        return value
    store.mutate(task["task_id"],append,"verification-attempt")
