#!/usr/bin/env python3
"""One-time registration of an existing host verifier; no installation or shell evaluation."""
from __future__ import annotations
import os
import hashlib
from pathlib import Path
import json
import copy
import sys
from contextlib import contextmanager
from engineering_common import digest, utc_now, write_json
from task_store import FileLease, TaskStore


def validate_inputs(inputs: list[dict]) -> None:
    if not isinstance(inputs,list):
        raise ValueError("Verification inputs must be a list of path/hash records")
    for item in inputs:
        if not isinstance(item,dict) or not isinstance(item.get("path"),str) or not isinstance(item.get("sha256"),str):
            raise ValueError("Invalid verification input record")
        path = Path(item["path"])
        if (not path.is_absolute() or not path.is_file() or digest(path) != item["sha256"]
                or (item.get("resolved_path") is not None and str(path.resolve()) != item["resolved_path"])):
            raise ValueError("Registered verification input changed: " + str(path))


def fingerprint_inputs(inputs: list[Path]) -> list[dict]:
    values = []
    for supplied in inputs:
        path = Path(os.path.abspath(supplied))
        if not path.is_file():
            raise ValueError("Existing verification input is required: " + str(path))
        item = {"path":str(path),"resolved_path":str(path.resolve()),"sha256":digest(path)}
        if item not in values:
            values.append(item)
    return values


def configure(repo: Path, argv: list[str], inputs: list[Path], python_paths: list[Path] | None = None) -> dict:
    if not isinstance(argv, list) or not argv or any(not isinstance(part, str) or not part for part in argv):
        raise ValueError("Host verifier must be a nonempty argv array")
    store = TaskStore(repo)
    values = fingerprint_inputs(inputs)
    directories = []
    for supplied in python_paths or []:
        path = supplied.resolve()
        if not path.is_dir():
            raise ValueError("Existing Python tools directory is required: " + str(path))
        if str(path) not in directories:
            directories.append(str(path))
    value = {"schema_version": 1, "repo_id": store.repo_id, "host_platform": os.name,
             "verify_command": argv, "inputs": values, "python_paths": directories,
             "registered_at": utc_now()}
    with FileLease(store.root / "locks/host-profile.lock"):
        write_json(store.root / "host.json", value)
    return value


def load(repo: Path) -> dict | None:
    store = TaskStore(repo)
    path = store.root / "host.json"
    if not path.exists():
        return None
    payload = path.read_bytes()
    value = json.loads(payload)
    if (value.get("schema_version") != 1 or value.get("repo_id") != store.repo_id
            or value.get("host_platform") != os.name):
        raise ValueError("Host profile belongs to another repository/platform/version")
    argv = value.get("verify_command")
    if not isinstance(argv,list) or not argv or any(not isinstance(part,str) or not part for part in argv):
        raise ValueError("Host profile verifier is not a valid argv array")
    validate_inputs(value["inputs"])
    return {**value, "profile_sha256": hashlib.sha256(payload).hexdigest()}


def select_verifier(repo: Path, command: list[str] | None) -> dict:
    """Freeze a new task's verifier; an explicit override never reads a broken profile."""
    if command is not None:
        if not isinstance(command,list) or not command or any(not isinstance(part,str) or not part for part in command):
            raise ValueError("Explicit verifier must be a nonempty argv array")
        return {"verify_command":list(command),"verification_inputs":[],"host_profile_sha256":None}
    profile = load(repo)
    return {"verify_command":profile["verify_command"] if profile else None,
            "verification_inputs":profile["inputs"] if profile else [],
            "host_profile_sha256":profile["profile_sha256"] if profile else None}


def validate_task_inputs(value: dict) -> None:
    validate_inputs(value.get("verification_inputs",[]))


def bind_verifier(repo: Path, command: list[str] | None, prior: dict | None = None) -> dict:
    """Interrupted setup keeps its actual recorded choice, including old unobserved inputs."""
    if prior is None:
        return select_verifier(repo,command)
    if command is not None and command != prior.get("verify_command"):
        raise ValueError("Explicit verifier differs from the interrupted setup contract")
    value = {"verify_command":copy.deepcopy(prior.get("verify_command"))}
    for key in ("verification_inputs","host_profile_sha256"):
        if key in prior:
            value[key] = copy.deepcopy(prior[key])
    validate_task_inputs(value)
    return value


def activate_python(repo: Path) -> None:
    profile = load(repo)
    for name in (profile or {}).get("python_paths", []):
        path = Path(name)
        if not path.is_dir():
            raise ValueError("Registered Python tools directory is missing: " + name)
        if name not in sys.path:
            sys.path.append(name)


@contextmanager
def python_context(repo: Path):
    """Activate existing tool paths for this audit/maintenance call, then restore sys.path."""
    original = sys.path[:]
    try:
        activate_python(repo)
        yield
    finally:
        sys.path[:] = original
