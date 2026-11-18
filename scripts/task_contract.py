#!/usr/bin/env python3
"""Engineering task allocation, scope/dependency gates and durable handoff contracts.

Only explicitly engineering-owned records adopt this policy. A held allocation
ticket can span preflight, Git workspace creation and registration without taking
the same lock twice. Lease states reserve writing authority, not process liveness.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import threading
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from engineering_common import digest, git, source_state, utc_now, validate_evidence
from task_store import FileLease, TaskStore, identifier


WRITERS = {"writer", "integrator"}
READERS = {"coordinator", "reviewer"}
ROLES = WRITERS | READERS
STATES = {"active", "paused", "completed"}
ENGINEERING_ROOTS = (".agents/skills/", ".github/", "tests/engineering/", "tests/skill-evals/", "docs/engineering/")
ENGINEERING_FILES = {"AGENTS.md", "Makefile", ".editorconfig", ".gitattributes", ".gitignore",
                     ".bake.toml", ".verible-format", ".yamllint.yml", "tools/toolchain.json"}
SCRIPT_SUFFIXES = {".py", ".ps1", ".sh", ".cmd"}
EXCLUDED_SCRIPTS = {"scripts/build_ics55_manual_pdk.py"}
_PROCESS_GUARD = threading.Lock()
_PROCESS_LOCKS = {}


class Allocation:
    def __init__(self, store: TaskStore):
        self.common = store.common.resolve()
        self.repo_id = store.repo_id
        self.lease = FileLease(store.root / "locks/task-allocation.lock", "task-allocation")
        key = os.path.normcase(str(self.common))
        with _PROCESS_GUARD:
            self.process_lock = _PROCESS_LOCKS.setdefault(key, threading.Lock())
        self.active = False

    def __enter__(self):
        if self.active:
            raise ValueError("An allocation ticket cannot be entered recursively")
        if not self.process_lock.acquire(timeout=10):
            raise TimeoutError("Task allocation is busy in this process; pass the existing ticket when nesting")
        try:
            self.lease.__enter__()
        except BaseException:
            if hasattr(self.lease, "handle"):
                self.lease.handle.close()
            self.process_lock.release()
            raise
        self.active, self.pid, self.thread = True, os.getpid(), threading.get_ident()
        return self

    def __exit__(self, *args):
        self.active = False
        try:
            return self.lease.__exit__(*args)
        finally:
            if hasattr(self.lease, "handle"):
                self.lease.handle.close()
            self.process_lock.release()

    def assert_owner(self, store: TaskStore):
        if (not self.active or self.common != store.common.resolve() or self.repo_id != store.repo_id
                or self.pid != os.getpid() or self.thread != threading.get_ident()):
            raise ValueError("An active allocation ticket for this repository/thread is required")


def allocation_lock(store: TaskStore) -> Allocation:
    return Allocation(store)


class AllocationBusy(TimeoutError):
    """A live actor/interface owner must release capacity; background callers retry."""


def normalize_patterns(patterns: list[str], *, literal: bool = False) -> list[str]:
    if not isinstance(patterns, list):
        raise ValueError("Scopes must be lists of repository-relative paths/patterns")
    result = []
    for value in patterns:
        if (not isinstance(value, str) or not value or value != value.strip() or PureWindowsPath(value).drive
                or value.startswith(("/", "\\")) or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or ":" in value or ".." in value.replace("\\", "/").split("/")):
            raise ValueError("Scope escaped the repository or is not a canonical relative path")
        path = PurePosixPath(value.replace("\\", "/")).as_posix()
        if path == "." or any(part.casefold() == ".git" for part in path.split("/")):
            raise ValueError("Repository metadata/root cannot be a task scope")
        if literal and any(char in path for char in "*?["):
            raise ValueError("Inputs and outputs require literal repository-relative paths")
        if path not in result:
            result.append(path)
    return result


def engineering_path(path: str) -> bool:
    return (path in ENGINEERING_FILES or path.startswith(ENGINEERING_ROOTS)
            or (path.startswith("scripts/") and Path(path).suffix in SCRIPT_SUFFIXES and path not in EXCLUDED_SCRIPTS))


def engineering_pattern(pattern: str) -> bool:
    if not any(char in pattern for char in "*?["):
        return engineering_path(pattern)
    # Broad scopes are intersected with engineering_path again for every changed
    # file. In particular scripts/* never grants edits to PDK installation/assets.
    if pattern.startswith(ENGINEERING_ROOTS):
        return True
    if pattern.startswith("scripts/"):
        suffix = Path(pattern).suffix
        return not suffix or suffix in SCRIPT_SUFFIXES or any(char in suffix for char in "*?[")
    return False


def acceptance_commands(checks: list) -> list[list[str]]:
    """Legacy simple commands and explicit JSON argv; never invoke an implicit shell."""
    if not isinstance(checks, list) or not checks:
        raise ValueError("Nonempty acceptance commands are required")
    result = []
    for command in checks:
        if isinstance(command, str):
            if command.lstrip().startswith("["):
                try:
                    argv = json.loads(command)
                except ValueError as error:
                    raise ValueError("Acceptance JSON must contain an argv array") from error
            else:
                if any(char in command for char in "\r\n;&|<>`$\\"):
                    raise ValueError("Use a JSON argv array for opaque acceptance arguments; shell recipes are unsupported")
                argv = shlex.split(command)
        else:
            argv = command
        if (not isinstance(argv, list) or not argv or any(not isinstance(part, str) or not part or "\0" in part for part in argv)):
            raise ValueError("Acceptance commands must be nonempty argv arrays")
        executable = shutil.which(argv[0]) or argv[0]
        if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
            raise ValueError("Use a native executable for acceptance; Windows batch shims evaluate arguments")
        if argv not in result:
            result.append(list(argv))
    return result


def extra_acceptance_commands(value: dict) -> list[list[str]]:
    return [argv for argv in acceptance_commands(value["checks"]) if argv != ["make", "verify"]]


def contract_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value["input_contract"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def output_observations(value: dict, root: Path) -> tuple[list[dict], list[str]]:
    outputs, errors = [], []
    for name in value.get("outputs", []):
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append("Expected output is missing/outside the task: " + name)
        else:
            outputs.append({"path": name, "sha256": digest(path)})
    return outputs, errors


def acceptance_errors(value: dict, root: Path) -> list[str]:
    """Source/head/contract bound observations, checked again at the publishing gates."""
    errors = []
    observed, missing = output_observations(value, root)
    errors.extend(missing)
    record = value.get("acceptance", {})
    state = source_state(root)
    if (record.get("schema_version") != 1 or record.get("source") != state
            or record.get("contract_sha256") != contract_digest(value)
            or record.get("commands") != acceptance_commands(value["checks"])):
        return errors + ["Declared acceptance evidence is missing or belongs to another candidate/contract"]
    if record.get("outputs") != observed:
        errors.append("Declared output changed after source-qualified acceptance")
    try:
        from engineering import required_ids
        full = Path(value["verification_report"])
        if value.get("verified_head") != state["sha"] or digest(full) != record.get("full_verification_sha256"):
            raise ValueError("Full verification identity changed")
        validate_evidence(full, root, required_ids("all"))
        extras = extra_acceptance_commands(value)
        if extras:
            info = record.get("report") or {}
            report = Path(info["path"])
            if digest(report) != info.get("sha256"):
                raise ValueError("Declared acceptance report changed")
            summary = validate_evidence(report, root, {"acceptance-" + str(index) for index in range(len(extras))})
            if summary.get("contract_sha256") != contract_digest(value):
                raise ValueError("Acceptance report contract differs")
            from tenon import verification_argv
            executed = {item["id"]: item.get("command") for item in summary["checks"]}
            for index, argv in enumerate(extras):
                if executed.get("acceptance-" + str(index)) != verification_argv({"verify_command": argv}, root):
                    raise ValueError("Executed acceptance argv differs from the declared command")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append("Declared acceptance evidence is invalid: " + str(error))
    return errors


def scopes_overlap(first: list[str], second: list[str]) -> bool:
    """Different literal prefixes prove disjointness; ambiguous globs conflict.

    This deliberately does not try to prove that matching suffixes/character
    classes are disjoint. Exact file scopes give the most useful parallelism.
    Comparisons are case-insensitive across hosts to protect Windows workspaces.
    """
    for left in first:
        for right in second:
            a, b = left.casefold(), right.casefold()
            a_glob, b_glob = any(char in a for char in "*?["), any(char in b for char in "*?[")
            if not a_glob and not b_glob:
                if a == b:
                    return True
                continue
            prefix_a, prefix_b = re.split(r"[*?\[]", a, maxsplit=1)[0], re.split(r"[*?\[]", b, maxsplit=1)[0]
            if prefix_a.startswith(prefix_b) or prefix_b.startswith(prefix_a):
                return True
    return False


def _ancestor(repo: Path, older: str, newer: str) -> bool:
    import subprocess
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False).returncode == 0


def dependency_errors(records: list[dict], task_id: str, dependencies: list[str], repo: Path, dev_sha: str) -> list[str]:
    indexed = {value["task_id"]: value for value in records}
    graph = {name: value.get("dependencies", []) for name, value in indexed.items()}
    graph[task_id] = dependencies
    errors, visited, visiting = [], set(), set()
    stack = [(task_id, False)]
    while stack:
        name, leaving = stack.pop()
        if leaving:
            visiting.remove(name)
            visited.add(name)
            continue
        if name in visiting:
            errors.append("Dependency cycle includes " + name)
            continue
        if name in visited:
            continue
        visiting.add(name)
        stack.append((name, True))
        children = graph.get(name)
        if not isinstance(children, list):
            errors.append("Dependency contract is invalid: " + name)
            children = []
        for child in children:
            if not isinstance(child, str):
                errors.append("Dependency contract contains a non-string task ID: " + name)
                continue
            if child not in graph:
                errors.append("Missing dependency: " + str(child))
            else:
                stack.append((child, False))
    for name in dependencies:
        value = indexed.get(name)
        if not value:
            continue
        delivery = value.get("delivery", {})
        ci = value.get("dev_ci") or delivery.get("dev_ci") or {}
        head = delivery.get("merge_commit_sha") or delivery.get("head_sha")
        green = ci.get("head_sha") or value.get("green_dev_sha")
        if (not delivery.get("merged") or ci.get("conclusion") != "success" or not head or not green
                or not _ancestor(repo, head, green) or not _ancestor(repo, green, dev_sha)):
            errors.append("Dependency is not integrated with recorded green dev evidence: " + name)
    return errors


def _scope_owner(value: dict) -> bool:
    if value.get("kind") != "engineering" or value.get("status") in {"CLEANED", "CANCELLED", "ABANDONED"} or value.get("delivery", {}).get("merged"):
        return False
    lease = value.get("execution_lease", {})
    if contract_errors(value):
        raise ValueError("Existing engineering input contract changed: " + value["task_id"])
    state = lease.get("state", "active")
    role = lease.get("role", value.get("role", "writer"))
    if state not in STATES or role not in ROLES:
        raise ValueError("Existing engineering execution lease is invalid: " + value["task_id"])
    return role in WRITERS


def _live_writer(value: dict) -> bool:
    return _scope_owner(value) and value.get("execution_lease", {}).get("state", "active") == "active"


def preflight(store: TaskStore, task_id: str, allowed: list[str], checks: list[str], dependencies: list[str], *,
              role: str = "writer", read_scope: list[str] | None = None, inputs: list[str] | None = None,
              outputs: list[str] | None = None, base_sha: str | None = None, allocation: Allocation | None = None,
              analysis_root: Path | None = None) -> dict:
    """Call before creating Git resources; registration repeats it under allocation."""
    if allocation is None:
        with allocation_lock(store) as ticket:
            return preflight(store, task_id, allowed, checks, dependencies, role=role, read_scope=read_scope,
                             inputs=inputs, outputs=outputs, base_sha=base_sha, allocation=ticket, analysis_root=analysis_root)
    allocation.assert_owner(store)
    prepared, _ = inspect_preflight(store, task_id, allowed, checks, dependencies, role=role,
                                    read_scope=read_scope, inputs=inputs, outputs=outputs,
                                    base_sha=base_sha, analysis_root=analysis_root)
    return prepared


def inspect_preflight(store: TaskStore, task_id: str, allowed: list[str], checks: list[str], dependencies: list[str], *,
                      role: str = "writer", read_scope: list[str] | None = None,
                      inputs: list[str] | None = None, outputs: list[str] | None = None,
                      base_sha: str | None = None, analysis_root: Path | None = None) -> tuple[dict, dict]:
    """Evaluate the contract against local observations; only preflight owns allocation."""
    task_id = identifier(task_id)
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError("Unknown engineering role")
    write = normalize_patterns(allowed)
    reads = normalize_patterns(read_scope if read_scope is not None else ["**"])
    inputs = normalize_patterns(inputs if inputs is not None else [], literal=True)
    outputs = normalize_patterns(outputs if outputs is not None else [], literal=True)
    commands = acceptance_commands(checks)
    if (role in WRITERS and not write) or (role in READERS and write):
        raise ValueError("Writers require a scope; coordinator/reviewer roles have no write scope")
    if any(not engineering_pattern(value) for value in write):
        raise ValueError("Engineering scope includes product or unbounded files")
    for output in outputs:
        if not output.startswith("build/") and (role in READERS or not engineering_path(output)
                or not any(fnmatch.fnmatchcase(output, pattern) for pattern in write)):
            raise ValueError("Expected output is outside the engineering write/artifact scope")
    if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies) or len(dependencies) != len(set(dependencies)):
        raise ValueError("Dependencies must be a list of unique task IDs")
    dependencies = [identifier(value) for value in dependencies]
    dev = git(store.repo, "rev-parse", "origin/dev").decode().strip()
    if role in WRITERS and base_sha is not None and base_sha != dev:
        raise ValueError("Preflight base no longer equals current origin/dev")
    records = store.all()
    errors = dependency_errors(records, task_id, dependencies, store.repo, dev)
    if errors:
        raise ValueError("; ".join(errors))
    owners = [value for value in records if value["task_id"] != task_id and _scope_owner(value)]
    others = [value for value in owners if _live_writer(value)]
    busy = []
    if role in WRITERS:
        if len(others) >= 2:
            busy.append("Both engineering writer slots are occupied")
        for value in owners:
            if scopes_overlap(write, normalize_patterns(value.get("allowed_paths", []))):
                busy.append("Write scope is reserved by unmerged task " + value["task_id"])
        for value in others:
            if role == "integrator" and value.get("execution_lease", {}).get("role", value.get("role")) == "integrator":
                busy.append("An integrator already owns the integration role")
    if busy:
        raise AllocationBusy("; ".join(busy))
    input_records = []
    for path in inputs:
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in reads):
            errors.append("Input is outside the declared read scope: " + path)
            continue
        try:
            if role in READERS and analysis_root is not None:
                source = (analysis_root / path).resolve()
                if not source.is_relative_to(analysis_root.resolve()) or not source.is_file():
                    raise ValueError("Input is not a file inside the frozen analysis snapshot")
                payload = source.read_bytes()
            else:
                payload = git(store.repo, "show", dev + ":" + path)
            input_records.append({"path": path, "sha256": hashlib.sha256(payload).hexdigest()})
        except Exception as error:
            errors.append("Input is absent from the candidate base: " + path + " (" + type(error).__name__ + ")")
    if errors:
        raise ValueError("; ".join(errors))
    prepared = {"kind": "engineering", "role": role, "allowed_paths": write, "read_scope": reads,
            "dependencies": dependencies, "checks": checks, "inputs": inputs, "outputs": outputs,
            "input_contract": {"schema_version": 1, "base_sha": base_sha if role in READERS and base_sha else dev, "role": role,
                "write_scope": write, "read_scope": reads, "dependencies": dependencies,
                "acceptance_commands": checks, "acceptance_argv": commands, "inputs": input_records, "expected_outputs": outputs}}
    capacity = {"limit": 2, "active_writers": [value["task_id"] for value in others], "free": max(0, 2 - len(others))}
    return prepared, capacity


def changed_path_errors(value: dict, root: Path, paths: list[str]) -> list[str]:
    errors = []
    for name in paths:
        if (not engineering_path(name) or not any(fnmatch.fnmatchcase(name, pattern) for pattern in value["allowed_paths"])
                or not (root / name).resolve().is_relative_to(root.resolve())):
            errors.append(name)
        elif name == "AGENTS.md":
            try:
                before = git(root, "show", value["base_sha"] + ":AGENTS.md").decode("utf-8")
                after = (root / name).read_text(encoding="utf-8")
                marker = "## Agent Engineering Workflow"
                if before.replace("\r\n", "\n").split(marker, 1)[0] != after.split(marker, 1)[0]:
                    errors.append(name)
            except Exception:
                errors.append(name)
    return errors


def contract_errors(value: dict) -> list[str]:
    contract = value.get("input_contract", {})
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        return ["Engineering input contract is missing/unsupported"]
    mirrors = {"allowed_paths":"write_scope", "read_scope":"read_scope", "dependencies":"dependencies",
               "checks":"acceptance_commands", "outputs":"expected_outputs", "role":"role", "goal":"goal"}
    errors = ["Engineering input contract changed: " + name for name, key in mirrors.items()
              if value.get(name) != contract.get(key)]
    input_records = contract.get("inputs")
    if (not isinstance(input_records, list) or any(not isinstance(item, dict) for item in input_records)
            or value.get("inputs") != [item.get("path") for item in input_records if isinstance(item, dict)]):
        errors.append("Engineering input contract changed: inputs")
    try:
        if "acceptance_argv" in contract and contract["acceptance_argv"] != acceptance_commands(value["checks"]):
            errors.append("Engineering input contract changed: acceptance argv")
    except (ValueError, KeyError):
        errors.append("Engineering acceptance command contract is invalid")
    return errors


def register_task(store: TaskStore, value: dict, allocation: Allocation | None = None) -> dict:
    if value.get("kind") != "engineering":
        return store.create(value)
    if allocation is None:
        with allocation_lock(store) as ticket:
            return register_task(store, value, ticket)
    allocation.assert_owner(store)
    if not isinstance(value.get("goal"), str) or not value["goal"].strip():
        raise ValueError("An engineering task goal is required")
    root = Path(value["worktree"]).resolve()
    prepared = preflight(store, value["task_id"], value["allowed_paths"], value["checks"], value["dependencies"],
        role=value.get("role", "writer"), read_scope=value.get("read_scope"), inputs=value.get("inputs"),
        outputs=value.get("outputs"), base_sha=value["base_sha"], allocation=allocation,
        analysis_root=root if value.get("role") in READERS else None)
    if prepared["role"] in WRITERS:
        from task_archive import worktrees
        if root == worktrees(store.repo)[0]["path"]:
            raise ValueError("Engineering writers require an isolated non-primary worktree")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    actual_branch = git(root, "branch", "--show-current").decode().strip()
    if common != store.common or actual_branch != value.get("source_branch", value["branch"]):
        raise ValueError("Engineering worktree/branch ownership differs")
    names = set(git(root, "diff", "--name-only", "-z", value["base_sha"], "--").split(b"\0"))
    names.update(git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
    if prepared["role"] in WRITERS and changed_path_errors({**value, **prepared}, root, [name.decode() for name in names if name]):
        raise ValueError("Existing source changes are outside the engineering task scope")
    value = {**value, **prepared}
    value["input_contract"]["goal"] = value["goal"]
    if value["role"] in READERS:
        value["delivery_mode"] = "local-only"
        if value.get("analysis_source") != source_state(root):
            raise ValueError("Source changed while registering the frozen analysis")
        value["owns_workspace"] = False
    value["execution_lease"] = {"schema_version": 1, "state": "active", "role": value["role"],
        "owner_id": value["owner_id"], "token": uuid.uuid4().hex, "updated_at": utc_now()}
    return store.create(value)


def set_lease_state(store: TaskStore, task_id: str, state: str, *, owner_id: str,
                    role: str | None = None, allocation: Allocation | None = None) -> dict:
    if not isinstance(state, str) or state not in STATES:
        raise ValueError("Unknown execution lease state")
    if allocation is None:
        observed = store.load(task_id)
        if observed.get("host_platform") != os.name:
            raise ValueError("Use the registered execution-lease host")
        with allocation_lock(store) as ticket:
            return set_lease_state(store, task_id, state, owner_id=owner_id, role=role, allocation=ticket)
    allocation.assert_owner(store)
    value = store.load(task_id)
    if value.get("kind") != "engineering" or value.get("owner_id") != owner_id or value.get("host_platform") != os.name:
        raise ValueError("Engineering lease owner differs")
    errors = contract_errors(value)
    if errors:
        raise ValueError("; ".join(errors))
    effective_role = role or value.get("execution_lease", {}).get("role", value["role"])
    if not isinstance(effective_role, str) or effective_role not in ROLES or ((effective_role in WRITERS) != (value["role"] in WRITERS)):
        raise ValueError("Read-only and writer capabilities cannot be exchanged")
    if state == "active":
        if value.get("delivery", {}).get("merged") or value.get("status") in {"CLEANED", "CANCELLED", "ABANDONED"}:
            raise ValueError("A closed/merged task cannot reacquire writing authority")
        if value["role"] in READERS and source_state(Path(value["worktree"])) != value.get("analysis_source"):
            raise ValueError("Analysis source snapshot changed; register a new analysis contract")
        preflight(store, task_id, value["allowed_paths"], value["checks"], value["dependencies"], role=effective_role,
            read_scope=value.get("read_scope"), inputs=value.get("inputs"), outputs=value.get("outputs"), allocation=allocation,
            base_sha=value["base_sha"] if value["role"] in READERS else None,
            analysis_root=Path(value["worktree"]) if value["role"] in READERS else None)
    lease = {**value["execution_lease"], "state": state, "role": effective_role, "updated_at": utc_now()}
    return store.mutate(task_id, lambda current: {**current, "execution_lease": lease}, "execution-lease-" + state)


def integration_errors(store: TaskStore, value: dict, *, current_dev: str | None = None, require_evidence: bool = False) -> list[str]:
    """Caller refreshes dev first; this local gate never replaces live PR/CI gates."""
    if value.get("kind") != "engineering":
        return []
    errors = contract_errors(value)
    if value.get("role") not in WRITERS:
        errors.append("Read-only roles cannot deliver code")
    lease = value.get("execution_lease", {})
    if lease.get("state") != "active" or lease.get("role") not in WRITERS:
        errors.append("Acquire an active writer/integrator lease before integration")
    dev = git(store.repo, "rev-parse", "origin/dev").decode().strip()
    if current_dev is not None and dev != current_dev:
        errors.append("Integration base changed after it was observed")
    root = Path(value["worktree"])
    state = source_state(root)
    head = state["sha"]
    if state["dirty"]:
        errors.append("Candidate contains unsubmitted source changes")
    if not _ancestor(store.repo, dev, head):
        errors.append("Candidate does not contain current dev; integrate normally and verify again")
    if value.get("head_sha") and value["head_sha"] != head:
        errors.append("Candidate head differs from the registered candidate")
    names = set(git(root, "diff", "--name-only", "-z", value["base_sha"], "--").split(b"\0"))
    names.update(git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
    errors.extend("Out-of-scope candidate: " + name for name in changed_path_errors(value, root, [name.decode() for name in names if name]))
    if require_evidence:
        errors.extend(acceptance_errors(value, root))
    errors.extend(dependency_errors(store.all(), value["task_id"], value["dependencies"], store.repo, dev))
    return errors


def handoff_contract(value: dict, root: Path, reports: list[dict], remaining: list[str]) -> tuple[dict, list[str]]:
    outputs, errors = [], []
    for name in value.get("outputs", []):
        path = (root / name).resolve()
        exists = path.is_relative_to(root.resolve()) and path.is_file()
        outputs.append({"path": name, "sha256": digest(path) if exists else None})
        if not exists:
            errors.append("Expected output is missing/outside the task: " + name)
    if value.get("role") in READERS and not reports:
        errors.append("A read-only handoff still requires executed, source-matched evidence")
    if value.get("role") in WRITERS and value.get("execution_lease", {}).get("state") == "paused":
        errors.append("Reacquire the writer lease before handing off a candidate")
    return {"schema_version": 1, "qualification": "analysis-only" if value.get("role") in READERS else "candidate-verification",
            "input_contract": value["input_contract"], "source": source_state(root), "outputs": outputs,
            "changed_files": value.get("changed_files", []),
            "evidence": reports, "remaining": remaining}, errors
