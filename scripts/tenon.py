#!/usr/bin/env python3
"""Source-pinned engineering entry; task workspaces are disposable, this entry is not."""
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


import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import shlex
import stat
import uuid
from pathlib import Path

from engineering_common import ROOT, git, source_state, utc_now, write_json
from task_archive import deploy_controller, worktrees
from task_lifecycle import GitHub, ci_green
from controller_leases import integration_scope
from task_store import FileLease, TaskStore, identifier


def capabilities() -> dict:
    return {"python": sys.version.split()[0], "platform": os.name,
            "tools": {name: shutil.which(name) for name in ("git", "make", "wsl", "gh")}}


def start_workspace(repo: Path, task_id: str, topic: str, goal: str, allowed: list[str],
                    checks: list[str], dependencies: list[str] | None = None,
                    verify_command: list[str] | None = None, local_only: bool = False,
                    workspace_root: Path | None = None, backend=None, *, kind: str | None = None,
                    role: str = "writer", read_scope: list[str] | None = None,
                    inputs: list[str] | None = None, outputs: list[str] | None = None, dry_run: bool = False) -> dict:
    from contextlib import nullcontext
    from task import start
    from task_contract import READERS, allocation_lock, inspect_preflight, preflight

    task_id = identifier(task_id)
    if role in READERS:
        raise ValueError("Reader roles do not create disposable workspaces. Register a local-only analysis with scripts/task.py start --kind engineering --role "
                         + role + " on the existing frozen source snapshot; it owns no branch/worktree")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", topic) or len(topic) > 48:
        raise ValueError("Topic must be a short lowercase hyphenated name")
    if (not isinstance(goal, str) or not goal.strip() or not isinstance(allowed, list)
            or not isinstance(checks, list) or not checks or (not allowed and role not in READERS)):
        raise ValueError("A goal, bounded file scope and acceptance commands are required")
    if any(not isinstance(item, str) or Path(item).is_absolute() or ".." in item.replace("\\", "/").split("/") for item in allowed):
        raise ValueError("File scope must stay inside the repository")
    if kind not in {None, "engineering", "general", "legacy"}:
        raise ValueError("Unknown task kind")
    if verify_command is not None and (not isinstance(verify_command, list) or not verify_command
            or any(not isinstance(item, str) or not item for item in verify_command)):
        raise ValueError("Verification command must be a nonempty argv array")
    store = TaskStore(repo, initialize=not dry_run)
    primary = worktrees(repo)[0]["path"]
    base_root = (workspace_root or primary.parent / ".tenon-worktrees" / store.repo_id).resolve()
    target = (base_root / task_id).resolve()
    if (target.parent != base_root or target == primary or primary.is_relative_to(target)
            or target.is_relative_to(store.common)):
        raise ValueError("Workspace path overlaps protected repository storage")
    branch = "dev-" + task_id + "-" + topic
    backend = backend or GitHub(repo)
    workspace_lock = nullcontext() if dry_run else FileLease(store.root / "locks/workspace-start.lock")
    with workspace_lock:
        if store.path(task_id).exists() or store.path(task_id).with_suffix(".jsonl").exists():
            raise FileExistsError("Task exists; inspect or resume it")
        if not dry_run:
            backend.refresh()
        base = git(repo, "rev-parse", "origin/dev").decode().strip()
        intent = store.root / "operations/workspace-setup" / (task_id + ".json")
        prior = json.loads(intent.read_text(encoding="utf-8")) if intent.exists() else None
        if dry_run and prior is not None:
            raise ValueError("Interrupted setup already exists; inspect or resume its recorded contract")
        if dry_run:
            verifier = {"verify_command": verify_command}
        else:
            from host_profile import bind_verifier
            verifier = bind_verifier(repo, verify_command, prior.get("contract") if prior else None)
        requested = "general" if kind == "legacy" else kind
        if prior and "kind" not in prior.get("contract", {}):
            if requested not in {None, "general"}:
                raise ValueError("Existing setup has an unclassified legacy contract; use general or a new task ID")
            effective_kind = None
        else:
            effective_kind = requested or ((prior or {}).get("contract", {}).get("kind")) or "engineering"
        if effective_kind not in {None, "engineering", "general"}:
            raise ValueError("Existing setup task kind is unsupported")
        if effective_kind != "engineering" and (role != "writer" or read_scope is not None or inputs is not None or outputs is not None):
            raise ValueError("Role/read/input/output contracts require engineering task kind")
        manager = allocation_lock(store) if effective_kind == "engineering" and not dry_run else nullcontext(None)
        with manager as ticket:
            prepared, capacity = None, None
            if effective_kind == "engineering":
                options = {"role": role, "read_scope": read_scope, "inputs": inputs, "outputs": outputs, "base_sha": base}
                if dry_run:
                    prepared, capacity = inspect_preflight(store, task_id, allowed, checks, dependencies or [], **options)
                else:
                    prepared = preflight(store, task_id, allowed, checks, dependencies or [], allocation=ticket, **options)
            selected_allowed = prepared["allowed_paths"] if prepared else allowed
            selected_checks = prepared["checks"] if prepared else checks
            selected_dependencies = prepared["dependencies"] if prepared else dependencies or []
            mode = "local-only" if local_only or (prepared and role in READERS) else "auto"
            contract = {"task_id": task_id, "topic": topic, "goal": goal, "allowed_paths": selected_allowed,
                        "checks": selected_checks, "dependencies": selected_dependencies, "verify_command": verifier["verify_command"],
                        "delivery_mode": mode, **{key: value for key, value in verifier.items() if key != "verify_command"}}
            if effective_kind is not None:
                contract["kind"] = effective_kind
            if prepared:
                contract.update(role=role, read_scope=prepared["read_scope"], inputs=prepared["inputs"], outputs=prepared["outputs"])
            existing = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", "refs/heads/" + branch],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if dry_run:
                if existing or target.exists() or any(item["path"] == target for item in worktrees(repo)):
                    raise FileExistsError("Workspace or branch already exists")
                return {"schema_version": 1, "kind": "workspace-start-preview", "status": "READY",
                        "dry_run": True, "base_sha": base, "base_qualification": "local origin/dev observation",
                        "branch": branch, "worktree": str(target), "contract": contract, "capacity": capacity,
                        "acceptance_argv": prepared["input_contract"]["acceptance_argv"] if prepared else None,
                        "qualification": "Advisory local checks only; actual start refreshes dev, binds its verifier and rechecks allocation."}
            if prior:
                if (prior.get("state") != "CREATING" or prior.get("contract") != contract
                        or prior.get("repository") != backend.name or prior.get("worktree") != str(target)
                        or prior.get("branch") != branch):
                    raise ValueError("Interrupted setup differs from the requested contract or current dev; inspect its intent")
                if existing or target.exists():
                    matches = [item for item in worktrees(repo) if item["path"] == target]
                    old = prior.get("base_sha")
                    transition = prior.get("base_transition", {})
                    admissible = {old}
                    if transition.get("from") == old:
                        admissible.add(transition.get("to"))
                    if (not existing or not target.is_dir() or len(matches) != 1 or "locked" in matches[0]
                            or matches[0].get("branch") != "refs/heads/" + branch or matches[0].get("HEAD") not in admissible
                            or source_state(target)["dirty"]):
                        raise ValueError("Interrupted workspace has changed; preserve it for inspection")
                    actual = matches[0]["HEAD"]
                    if actual != base:
                        if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", actual, base],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode:
                            raise ValueError("Interrupted startup base is not an ancestor of current dev")
                        prior["base_transition"] = {"from": old, "to": base, "observed_head": actual,
                                                    "prepared_at": utc_now()}
                        write_json(intent, prior)
                        subprocess.run(["git", "-C", str(target), "merge", "--ff-only", base],
                                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        if git(target, "rev-parse", "HEAD").decode().strip() != base or source_state(target)["dirty"]:
                            raise ValueError("Startup changed while fast-forwarding; preserve it")
                    if old != base:
                        prior.update(base_sha=base, base_history=prior.get("base_history", []) + [{"from": old, "to": base}])
                        write_json(intent, prior)
                elif prior.get("base_sha") != base:
                    prior.update(base_sha=base, base_history=prior.get("base_history", []) + [{"from": prior.get("base_sha"), "to": base}])
                    write_json(intent, prior)
            elif existing or target.exists():
                raise FileExistsError("Workspace/branch exists without this setup intent")
            if not prior:
                write_json(intent, {"schema_version": 1, "task_id": task_id, "repository": backend.name,
                                   "base_sha": base, "branch": branch, "worktree": str(target),
                                   "contract": contract, "state": "CREATING", "created_at": utc_now()})
            if not existing:
                base_root.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", branch, str(target), base],
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            options = {"kind": effective_kind}
            if prepared:
                options.update(role=role, read_scope=prepared["read_scope"], inputs=prepared["inputs"], outputs=prepared["outputs"], allocation=ticket)
            value = start(task_id, goal, selected_allowed, selected_checks, selected_dependencies, target,
                          verify_command=verifier["verify_command"], delivery_mode=mode, **options)
            value = store.mutate(task_id, lambda item: {**item, "workspace_backend": "git",
                "workspace_root": str(base_root), "environment": capabilities(),
                **{key: value for key, value in verifier.items() if key != "verify_command"}}, "workspace-registered")
            current_intent = json.loads(intent.read_text(encoding="utf-8"))
            write_json(intent, {**current_intent, "schema_version": 1, "task_id": task_id, "state": "REGISTERED",
                               "branch": branch, "worktree": str(target), "base_sha": base, "contract": contract})
            return value


def verification_argv(value: dict, root: Path) -> list[str]:
    from host_profile import validate_task_inputs
    validate_task_inputs(value)
    argv = value.get("verify_command") or [sys.executable, "scripts/engineering.py", "verify"]
    posix = str(root).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", posix):
        posix = "/mnt/" + posix[0].lower() + posix[2:]
    return [part.replace("{worktree}", str(root)).replace("{worktree_posix}", posix) for part in argv]


def install_git_alias(repo: Path, launcher: Path, previous: dict | None, requested: bool) -> tuple[dict | None,str]:
    """Own only the exact local alias we installed; never shadow an unrelated alias."""
    def values(local):
        result = subprocess.run(["git","-C",str(repo),"config",*(["--local"] if local else []),
            "--null","--get-all","alias.tenon"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode not in {0,1}:
            raise ValueError("Cannot inspect repository Git alias")
        return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    local, effective = values(True),values(False)
    old = (previous or {}).get("git_alias")
    owned = old if (isinstance(old,dict) and old.get("scope")=="local" and old.get("key")=="alias.tenon"
        and local==[old.get("value")] and effective and effective[-1]==old.get("value")) else None
    if not requested:
        return owned,"OWNED_RETAINED" if owned else "NOT_REQUESTED"
    if effective and (not owned or any(item!=owned["value"] for item in effective)):
        return owned,"PRESERVED_EXISTING"
    command = "!f() { exec "+shlex.quote(Path(sys.executable).as_posix())+" -B "+shlex.quote(launcher.as_posix())+' "$@"; }; f'
    if local != [command]:
        subprocess.run(["git","-C",str(repo),"config","--local","alias.tenon",command],check=True,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if values(True) != [command]:
        raise ValueError("Repository alias installation was not confirmed")
    return {"scope":"local","key":"alias.tenon","value":command},"INSTALLED"


def _alias_values(repo: Path, local=True):
    result = subprocess.run(["git", "-C", str(repo), "config", *(["--local"] if local else []),
        "--null", "--get-all", "alias.tenon"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode not in {0, 1}:
        raise ValueError("Cannot inspect repository Git alias")
    return [part.decode("utf-8") for part in result.stdout.split(b"\0") if part]


def _set_alias_values(repo: Path, values):
    if not isinstance(values, list) or len(values) > 1 or any(not isinstance(value, str) for value in values):
        raise ValueError("Owned local Git alias requires zero or one value")
    # Git atomically commits one config update. Both publication and recovery
    # must avoid an observable unset-before-add state owned by neither tuple.
    operation = ["--replace-all", "alias.tenon", values[0]] if values else ["--unset-all", "alias.tenon"]
    result = subprocess.run(["git", "-C", str(repo), "config", "--local", *operation],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0 and not (not values and result.returncode == 5):
        raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
    if _alias_values(repo) != values:
        raise ValueError("Owned local Git alias update was not confirmed")


def _alias_publication(repo, launcher, previous, requested):
    local, effective = _alias_values(repo), _alias_values(repo, False)
    old = (previous or {}).get("git_alias")
    owned = old if (isinstance(old, dict) and old.get("scope") == "local" and old.get("key") == "alias.tenon"
        and local == [old.get("value")] and effective and effective[-1] == old.get("value")) else None
    descriptor, status, after = owned, "OWNED_RETAINED" if owned else "NOT_REQUESTED", local
    if requested:
        if effective and (not owned or any(value != owned["value"] for value in effective)):
            status = "PRESERVED_EXISTING"
        else:
            command = "!f() { exec " + shlex.quote(Path(sys.executable).as_posix()) + " -B " + shlex.quote(launcher.as_posix()) + ' "$@"; }; f'
            descriptor, status, after = {"scope": "local", "key": "alias.tenon", "value": command}, "INSTALLED", [command]
    return descriptor, status, {"changed": local != after, "before": local if local != after else None,
        "after": after if local != after else None,
        "unchanged_sha256": hashlib.sha256(json.dumps(local, ensure_ascii=False).encode("utf-8")).hexdigest()}


_ENTRY_FILES = ("tenon.py", "tenon.cmd", "installation.json")
_LEGACY_LAUNCHER_SHA256 = "9fa9889ef719416c160c56cc4b9fc2c275e0acf218587ba906af53ac83ee3a4c"


def _entry_snapshot(path):
    from controller_leases import redirected
    if not os.path.lexists(path):
        return {"present": False}
    if redirected(path) or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Installation entry is unknown, redirected or oversized: " + str(path))
    raw, info = path.read_bytes(), path.stat()
    return {"present": True, "data": base64.b64encode(raw).decode("ascii"), "sha256": hashlib.sha256(raw).hexdigest(),
            "mode": stat.S_IMODE(info.st_mode), "atime_ns": info.st_atime_ns, "mtime_ns": info.st_mtime_ns,
            "attributes": getattr(info, "st_file_attributes", None)}


def _entry_bytes(value):
    if not value.get("present"):
        return None
    raw = base64.b64decode(value["data"], validate=True)
    if len(raw) > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != value["sha256"]:
        raise ValueError("Installation transaction backup integrity differs")
    return raw


def _install_record(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write((json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            output.flush(); os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _replace_entry_bytes(path, raw):
    temporary = path.with_name(".tenon-install-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _publish_entry_file(path, raw):
    _replace_entry_bytes(path, raw)


def _restore_entry(path, before):
    raw = _entry_bytes(before)
    if raw is None:
        if os.path.lexists(path):
            path.unlink()
        return
    _replace_entry_bytes(path, raw)
    os.utime(path, ns=(before["atime_ns"], before["mtime_ns"]))
    path.chmod(before["mode"])
    if os.name == "nt" and before.get("attributes") is not None:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        kernel.SetFileAttributesW.restype = wintypes.BOOL
        if not kernel.SetFileAttributesW(str(path), before["attributes"]):
            raise ctypes.WinError(ctypes.get_last_error())


def _validate_existing_entry(store, binary, previous):
    snapshots = {name: _entry_snapshot(binary / name) for name in _ENTRY_FILES}
    if previous is None:
        if any(value["present"] for value in snapshots.values()):
            raise ValueError("Entry files exist without an owned installation record; preserve unknown resources")
        return snapshots
    if previous.get("schema_version") != 1 or previous.get("repo_id") != store.repo_id or Path(previous.get("repo", "")).resolve() != worktrees(store.repo)[0]["path"]:
        raise ValueError("Existing installation identity differs; preserve the entry")
    from controller_leases import target_launcher
    raw = _entry_bytes(snapshots["tenon.py"])
    if raw is None:
        raise ValueError("Owned installation has no launcher; explicit transaction recovery is required")
    normalized = raw.decode("utf-8").replace("\r\n", "\n")
    known = False
    for candidate in (previous.get("launcher_source_commit"), previous.get("commit"), previous.get("previous_commit")):
        if candidate and re.fullmatch(r"[0-9a-f]{40}", str(candidate)):
            try:
                known |= normalized == target_launcher(store.common, candidate).replace("\r\n", "\n")
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    # The real pre-ABI installer could publish its own old literal alongside a
    # new target record. Recognize that exact historical template, never edits.
    known |= previous.get("controller_lease_abi", 0) == 0 and hashlib.sha256(normalized.encode("utf-8")).hexdigest() == _LEGACY_LAUNCHER_SHA256
    if not known:
        raise ValueError("Existing launcher bytes are unclassified; preserve them instead of replacing")
    return snapshots


def _transaction_paths(store, transaction_id):
    from controller_leases import redirected
    if not re.fullmatch(r"[0-9a-f]{32}", str(transaction_id)):
        raise ValueError("Invalid installation transaction identity")
    directory = store.root / "installation-transactions" / transaction_id
    if directory.resolve() != directory or directory.parent != store.root / "installation-transactions":
        raise ValueError("Installation transaction escaped its storage")
    for parent in (store.root / "bin", directory.parent, directory):
        if os.path.lexists(parent) and (redirected(parent) or not parent.is_dir()):
            raise ValueError("Installation storage is redirected or unclassified")
    return directory, directory / "journal.json", store.root / "installation-pending.json"


def _rollback_installation(store, primary, transaction, journal_path, pointer):
    from engineering_common import safe_exception
    binary = store.root / "bin"
    blockers = []
    for name in _ENTRY_FILES:
        before, proposed = transaction["before"][name], transaction["proposed"][name]
        try:
            current = _entry_snapshot(binary / name)
            current_raw = _entry_bytes(current)
            old_raw, new_raw = _entry_bytes(before), _entry_bytes(proposed)
            if current_raw == old_raw:
                if current.get("present") and any(current.get(key) != before.get(key) for key in ("mode", "mtime_ns", "attributes")):
                    _restore_entry(binary / name, before)
                continue
            if current_raw != new_raw:
                raise ValueError("Entry changed independently; preserve " + name)
            _restore_entry(binary / name, before)
        except (OSError, ValueError) as error:
            blockers.append(safe_exception(error))
    alias = transaction["alias"]
    try:
        current = _alias_values(primary)
        if alias["changed"]:
            if current == alias["after"]:
                _set_alias_values(primary, alias["before"])
            elif current != alias["before"]:
                raise ValueError("Local alias changed independently; preserve it")
        elif hashlib.sha256(json.dumps(current, ensure_ascii=False).encode("utf-8")).hexdigest() != alias["unchanged_sha256"]:
            raise ValueError("Unowned local alias changed; preserve it")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        blockers.append(safe_exception(error))
    transaction.update(state="RECOVERY_BLOCKED" if blockers else "ROLLED_BACK", blockers=blockers, recovered_at=utc_now())
    _install_record(journal_path, transaction)
    if blockers:
        raise RuntimeError("Installation recovery retained unknown resources; use qualified source install --recover-only after inspection: " + str(journal_path))
    pointer.unlink(missing_ok=True)
    return {"status": "ROLLED_BACK", "transaction": transaction["transaction_id"], "journal": str(journal_path)}


def _recover_pending_install(store, primary):
    from controller_leases import read_json
    pointer = store.root / "installation-pending.json"
    if not os.path.lexists(pointer):
        return {"status": "NO_PENDING_INSTALL"}
    pending = read_json(pointer)
    if pending.get("schema_version") != 1 or pending.get("repo_id") != store.repo_id:
        raise ValueError("Pending installation belongs to an unknown repository/schema")
    directory, journal_path, pointer = _transaction_paths(store, pending.get("transaction_id"))
    transaction = read_json(journal_path)
    if (transaction.get("schema_version") != 1 or transaction.get("repo_id") != store.repo_id
            or transaction.get("transaction_id") != pending["transaction_id"] or transaction.get("primary") != str(primary)
            or set(transaction.get("before", {})) != set(_ENTRY_FILES) or set(transaction.get("proposed", {})) != set(_ENTRY_FILES)):
        raise ValueError("Installation recovery journal identity differs")
    for value in [*transaction["before"].values(), *transaction["proposed"].values()]:
        _entry_bytes(value)
    if transaction.get("state") == "COMMITTED":
        alias = transaction["alias"]
        current_alias = _alias_values(primary)
        alias_matches = current_alias == alias["after"] if alias["changed"] else hashlib.sha256(json.dumps(current_alias, ensure_ascii=False).encode("utf-8")).hexdigest() == alias["unchanged_sha256"]
        if alias_matches and all(_entry_bytes(_entry_snapshot(store.root / "bin" / name)) == _entry_bytes(transaction["proposed"][name]) for name in _ENTRY_FILES):
            pointer.unlink()
            return {"status": "RECOVERED_COMMIT", "journal": str(journal_path)}
        raise ValueError("Committed installation tuple changed independently; preserve it")
    if transaction.get("state") not in {"PREPARED", "PUBLISHING", "ROLLED_BACK", "RECOVERY_BLOCKED"}:
        raise ValueError("Unknown installation journal state; preserve it")
    return _rollback_installation(store, primary, transaction, journal_path, pointer)


def recover_installation(repo: Path):
    from controller_leases import common_directory, mutation_context
    if not (common_directory(repo) / "tenon/repository.json").is_file():
        raise ValueError("No initialized repository installation can be recovered")
    store = TaskStore(repo)
    primary = worktrees(repo)[0]["path"]
    with mutation_context(store):
        return _recover_pending_install(store, primary)


def _publish_installation(store, primary, selected_launcher, metadata, previous, requested_alias):
    from engineering_common import safe_exception
    binary = store.root / "bin"
    binary.mkdir(exist_ok=True)
    before = _validate_existing_entry(store, binary, previous)
    descriptor, alias_status, alias = _alias_publication(primary, binary / "tenon.py", previous, requested_alias)
    if descriptor:
        metadata["git_alias"] = descriptor
    proposed = {"tenon.py": {"present": True, "data": base64.b64encode(selected_launcher.encode("utf-8")).decode("ascii")},
                "tenon.cmd": {"present": False}, "installation.json": {"present": True}}
    if os.name == "nt":
        command = '@echo off\r\n"' + sys.executable + '" -B "%~dp0tenon.py" %*\r\n'
        proposed["tenon.cmd"] = {"present": True, "data": base64.b64encode(command.encode("utf-8")).decode("ascii")}
    elif before["tenon.cmd"]["present"]:
        raise ValueError("A foreign-platform command wrapper is present; preserve it")
    metadata_bytes = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").replace("\n", os.linesep).encode("utf-8")
    proposed["installation.json"]["data"] = base64.b64encode(metadata_bytes).decode("ascii")
    for item in proposed.values():
        if item["present"]:
            item["sha256"] = hashlib.sha256(base64.b64decode(item["data"])).hexdigest()
    transaction_id = uuid.uuid4().hex
    directory, journal_path, pointer = _transaction_paths(store, transaction_id)
    directory.mkdir(parents=True)
    transaction = {"schema_version": 1, "repo_id": store.repo_id, "transaction_id": transaction_id, "primary": str(primary),
        "state": "PREPARED", "created_at": utc_now(), "before": before, "proposed": proposed, "alias": alias}
    _install_record(journal_path, transaction)
    if os.path.lexists(pointer):
        raise ValueError("Another pending installation record appeared; preserve it")
    _install_record(pointer, {"schema_version": 1, "repo_id": store.repo_id, "transaction_id": transaction_id})
    try:
        transaction["state"] = "PUBLISHING"; _install_record(journal_path, transaction)
        _publish_entry_file(binary / "tenon.py", _entry_bytes(proposed["tenon.py"]))
        if proposed["tenon.cmd"]["present"]:
            _publish_entry_file(binary / "tenon.cmd", _entry_bytes(proposed["tenon.cmd"]))
        if alias["changed"]:
            _set_alias_values(primary, alias["after"])
        write_json(binary / "installation.json", metadata)
        for name in _ENTRY_FILES:
            if _entry_bytes(_entry_snapshot(binary / name)) != _entry_bytes(proposed[name]):
                raise ValueError("Published installation tuple verification differs: " + name)
        transaction["state"] = "COMMITTED"; _install_record(journal_path, transaction)
        pointer.unlink()
    except BaseException as error:
        transaction["failure"] = safe_exception(error)
        _rollback_installation(store, primary, transaction, journal_path, pointer)
        raise
    return alias_status


def audit(repo: Path, ref: str, published_rules: bool = False) -> dict:
    from host_profile import python_context
    from ci_contract import audit_source
    from github_rules import audit as audit_rules
    from task_backends import repository_name
    commit = git(repo,"rev-parse","--verify","--end-of-options",ref+"^{commit}").decode().strip()
    with python_context(repo):
        source = audit_source(repo,commit)
        published = audit_rules(repository_name(repo),repo,commit) if published_rules else None
    statuses = [source["status"]]+([published["status"]] if published else [])
    return {"schema_version":1,"kind":"engineering-audit","requested_ref":ref,"commit":commit,
            "status":"PASS" if all(value=="PASS" for value in statuses) else "FAIL",
            "source_audit":source,"published_rules":published}


def install(repo: Path, backend=None, *, git_alias: bool = False) -> dict:
    """Deploy only the current, fully green dev tree, independent of the checkout branch."""
    store = TaskStore(repo)
    backend = backend or GitHub(repo)
    backend.refresh()
    commit = git(repo, "rev-parse", "origin/dev").decode().strip()
    if not ci_green(backend.latest_run(commit, "push"), commit, "push"):
        raise ValueError("Install requires the current dev CI to be fully successful")
    if "scripts/tenon.py" not in git(repo, "ls-tree", "-r", "--name-only", commit, "scripts/tenon.py").decode().splitlines():
        raise ValueError("Current green dev has no stable entry yet")
    from controller_leases import mutation_context, source_capability, target_launcher
    abi = source_capability(store.common, commit)
    selected_launcher = target_launcher(store.common, commit)
    with mutation_context(store, initialize=True):
        primary = worktrees(repo)[0]["path"]
        _recover_pending_install(store, primary)
        controller = deploy_controller(store, commit)
        directory = controller.parent.parent
        entry = directory / "scripts/tenon.py"
        primary = worktrees(repo)[0]["path"]
        binary = store.root / "bin"
        binary.mkdir(parents=True, exist_ok=True)
        previous = json.loads((binary / "installation.json").read_text(encoding="utf-8")) if (binary / "installation.json").exists() else None
        metadata = {"schema_version": 1, "repo_id": store.repo_id, "repo": str(primary), "repository": backend.name,
                    "python_executable": str(Path(sys.executable).resolve()),
                    "commit": commit, "controller": str(directory), "installed_at": utc_now(),
                    "controller_lease_abi": abi, "controller_lease_state": "KNOWN" if abi == 1 else "UNKNOWN_LEGACY",
                    "launcher_source_commit": commit,
                    "previous_commit": previous.get("commit") if previous and previous.get("commit") != commit
                    else (previous or {}).get("previous_commit")}
        # One literal, extracted from the verified target source rather than caller.
        launcher = '''#!/usr/bin/env python3
import json, os, subprocess, sys, types
from pathlib import Path
sys.dont_write_bytecode = True
binary = Path(__file__).resolve().parent
module = None
try:
    if (binary.parent / "installation-pending.json").exists():
        raise ValueError("Installation transaction pending; readonly entry will not repair it. From retained primary run the Git-verified source installer with --repo PRIMARY install --recover-only")
    config = json.loads((binary / "installation.json").read_text(encoding="utf-8"))
    if config.get("controller_lease_abi") != 1:
        raise ValueError("Installed entry has no supported execution ABI; run its qualified source installer explicitly")
    common = binary.parent.parent
    environment = os.environ.copy()
    for key in tuple(environment):
        if key in {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM"} or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            environment.pop(key, None)
    payload = subprocess.check_output(["git", "--no-optional-locks", "--no-replace-objects", "--git-dir", str(common), "show", config["commit"] + ":scripts/controller_leases.py"],
        cwd=common, env=environment, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    module = types.ModuleType("controller_leases")
    module.__file__ = str(Path(config["controller"]) / "scripts/controller_leases.py")
    sys.modules["controller_leases"] = module
    exec(compile(payload, module.__file__, "exec"), module.__dict__)
    if module.CONTROLLER_LEASE_ABI != 1:
        raise ValueError("Unsupported controller bootstrap ABI")
    raise SystemExit(module.run_installed(config, sys.argv[1:], binary))
except (OSError, ValueError, KeyError, ImportError, RuntimeError, subprocess.SubprocessError) as error:
    print(json.dumps(module.error_result(error) if module and hasattr(module, "error_result") else {"status": "BLOCKED", "reason": str(error)}))
    raise SystemExit(2)
'''
        alias_status = _publish_installation(store, primary, selected_launcher, metadata, previous, git_alias)
        return {"status": "INSTALLED", "entry": str(binary / "tenon.py"), "commit": commit,
                "repo": str(primary), "capabilities": capabilities(),"git_alias":alias_status,
                "controller_lease_abi": abi, "controller_lease_state": metadata["controller_lease_state"],
                "launcher_source_commit": commit}


def task_status(repo: Path, task_id: str) -> dict:
    store = TaskStore(repo)
    value = store.load(task_id)
    root = Path(value["worktree"])
    if root.exists():
        from task import scope_errors
        value["scope_errors"] = scope_errors(value, root)
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--repo", type=Path, default=ROOT)
    commands = result.add_subparsers(dest="command", required=True)
    install_command = commands.add_parser("install")
    install_command.add_argument("--git-alias",action="store_true",help="Install an owned per-repository git tenon shortcut if the name is free")
    install_command.add_argument("--recover-only", action="store_true", help="Restore an interrupted known entry tuple without selecting or publishing another version")
    diagnose = commands.add_parser("doctor")
    diagnose.add_argument("--full", action="store_true", help="Read-only fixed-primary, entry, host, schedule and cleanup health")
    measuring = commands.add_parser("metrics")
    measuring.add_argument("--remote",action="store_true",help="Explicitly query remote heads; default observations are local only")
    measuring.add_argument("--month",help="Include evidence-linked improvement items for UTC month YYYY-MM")
    maintenance = commands.add_parser("maintenance")
    maintenance.add_argument("maintenance_args", nargs=argparse.REMAINDER)
    host = commands.add_parser("host").add_subparsers(dest="host_command",required=True)
    configure = host.add_parser("configure")
    configure.add_argument("--verify-command-json",required=True)
    configure.add_argument("--input",type=Path,action="append",default=[])
    configure.add_argument("--python-path",type=Path,action="append",default=[])
    auditing = commands.add_parser("audit")
    auditing.add_argument("--ref",default="origin/dev")
    auditing.add_argument("--published-rules",action="store_true")
    tasks = commands.add_parser("task").add_subparsers(dest="task_command", required=True)
    start = tasks.add_parser("start")
    start.add_argument("--id", required=True)
    start.add_argument("--topic", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--allow", action="append", default=[])
    start.add_argument("--check", action="append", required=True)
    start.add_argument("--dependency", action="append", default=[])
    start.add_argument("--verify-command-json")
    start.add_argument("--local-only", action="store_true")
    start.add_argument("--dry-run", action="store_true", help="Preview the contract from local state without creating resources")
    start.add_argument("--workspace-root", type=Path, help="Explicit workspace root, primarily for controlled environments")
    start.add_argument("--kind", choices=("engineering", "general", "legacy"), help="New tasks default to engineering; untagged interrupted setups retain their original policy")
    start.add_argument("--role", choices=("writer", "integrator", "coordinator", "reviewer"), default="writer")
    start.add_argument("--read-allow", action="append")
    start.add_argument("--input", action="append")
    start.add_argument("--output", action="append")
    lease = tasks.add_parser("lease")
    lease.add_argument("--id", required=True)
    lease.add_argument("--state", choices=("active", "paused", "completed"), required=True)
    lease.add_argument("--role", choices=("writer", "integrator", "coordinator", "reviewer"))
    for name in ("status", "resume", "verify", "submit", "deliver"):
        command = tasks.add_parser(name)
        command.add_argument("--id", required=True)
    context = tasks.add_parser("context")
    context.add_argument("--id", required=True)
    note = tasks.add_parser("note")
    note.add_argument("--id", required=True)
    note.add_argument("--event-id", required=True)
    note.add_argument("--kind", choices=("intervention","rework"), required=True)
    note.add_argument("--reason", required=True)
    attest = tasks.add_parser("attest")
    attest.add_argument("--id", required=True)
    attest.add_argument("--event-id", required=True)
    attest.add_argument("--human-interventions", type=int, required=True)
    attest.add_argument("--rework", type=int, required=True)
    attest.add_argument("--evidence-digest", required=True)
    attest.add_argument("--coverage-start", choices=("task-start","tracking-start"), required=True)
    attest.add_argument("--reason", required=True)
    handoff = tasks.add_parser("handoff")
    handoff.add_argument("--id", required=True)
    handoff.add_argument("--evidence", type=Path, action="append", default=[])
    handoff.add_argument("--remaining", action="append", default=[])
    handoff.add_argument("--human-interventions", type=int)
    handoff.add_argument("--rework", type=int)
    handoff.add_argument("--observations-complete", action="store_true")
    handoff.add_argument("--evidence-digest")
    reconciliation = commands.add_parser("reconcile")
    reconciliation.add_argument("--all", action="store_true", required=True)
    reconciliation.add_argument("--once", action="store_true", required=True)
    reconciliation.add_argument("--apply", action="store_true")
    reconciliation.add_argument("--json", action="store_true")
    worker = commands.add_parser("worker").add_subparsers(dest="worker_command", required=True)
    for name in ("install", "remove", "status", "disable"):
        worker.add_parser(name)
    enable = worker.add_parser("enable")
    enable.add_argument("--acceptance-task", required=True)
    run = worker.add_parser("run")
    run.add_argument("--max-seconds", type=float, default=900)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.command == "install":
            value = recover_installation(repo) if args.recover_only else install(repo,git_alias=args.git_alias)
        elif args.command == "host":
            from host_profile import configure,load
            configure(repo,json.loads(args.verify_command_json),args.input,args.python_path)
            value = {"status":"CONFIGURED",**load(repo)}
        elif args.command == "audit":
            value = audit(repo,args.ref,args.published_rules)
        elif args.command == "metrics":
            from engineering_metrics import collect, improvement_items
            value = collect(repo,probe_remote=args.remote)
            value["status"] = "BLOCKED" if value.get("diagnostics") else "OBSERVED"
            if args.month is not None:
                value["improvement_items"] = improvement_items(value,args.month)
        elif args.command == "doctor":
            if args.full:
                from engineering_health import full_doctor
                value = full_doctor(repo)
            else:
                value = {"status": "READY" if shutil.which("git") else "BLOCKED", "repo": str(repo),
                         "capabilities": capabilities(), "dev_sha": git(repo, "rev-parse", "origin/dev").decode().strip()}
        elif args.command == "maintenance":
            from controller_leases import entry_command
            return subprocess.run(entry_command(Path(__file__).with_name("maintenance.py"),
                "--repo", str(repo), *args.maintenance_args), cwd=repo).returncode
        elif args.command == "reconcile":
            from task_queue import reconcile
            value = reconcile(repo, apply=args.apply)
        elif args.command == "worker":
            from task_worker import run, scheduler, set_enabled, status as worker_status
            if args.worker_command in {"enable", "disable"}:
                value = set_enabled(repo, args.worker_command == "enable", getattr(args, "acceptance_task", None))
            elif args.worker_command == "run":
                value = run(repo, args.max_seconds)
            elif args.worker_command == "status":
                value = worker_status(repo)
            else:
                value = scheduler(repo, args.worker_command)
        elif args.task_command == "start":
            value = start_workspace(repo, args.id, args.topic, args.goal, args.allow, args.check,
                args.dependency, json.loads(args.verify_command_json) if args.verify_command_json else None,
                args.local_only, args.workspace_root, kind=args.kind, role=args.role,
                read_scope=args.read_allow, inputs=args.input, outputs=args.output, dry_run=args.dry_run)
        elif args.task_command == "context":
            from task_context import context
            value = context(repo,args.id)
        elif args.task_command == "note":
            from task_observations import note
            value = note(repo,args.id,args.event_id,args.kind,args.reason)
        elif args.task_command == "attest":
            from task_observations import attest
            value = attest(repo,args.id,args.event_id,args.human_interventions,args.rework,
                args.evidence_digest,args.reason,args.coverage_start)
        elif args.task_command == "handoff":
            from task import handoff
            store = TaskStore(repo,initialize=False)
            row = store.load(args.id)
            root = Path(row["worktree"]).resolve()
            if row.get("host_platform") != os.name:
                raise ValueError("Use the registered native handoff host")
            common = Path(git(root,"rev-parse","--path-format=absolute","--git-common-dir").decode().strip()).resolve()
            if common != store.common:
                raise ValueError("Registered handoff worktree belongs to another repository")
            if any(value is not None and (type(value) is not int or value < 0)
                   for value in (args.human_interventions,args.rework)):
                raise ValueError("Explicit handoff counters must be nonnegative integers or omitted")
            if args.evidence_digest and not args.observations_complete:
                raise ValueError("--evidence-digest requires --observations-complete")
            value = handoff(args.id,args.evidence,args.remaining,args.human_interventions,args.rework,root,
                observations_complete=args.observations_complete,observation_digest=args.evidence_digest)
        elif args.task_command == "lease":
            from task_contract import set_lease_state
            store = TaskStore(repo)
            owner = store.load(args.id)["owner_id"]
            value = set_lease_state(store, args.id, args.state, owner_id=owner, role=args.role)
        elif args.task_command == "status":
            value = task_status(repo, args.id)
        elif args.task_command == "verify":
            value = TaskStore(repo).load(args.id)
            root = Path(value["worktree"])
            if value.get("kind") == "engineering" and value.get("role") in {"writer", "integrator"}:
                from task_lifecycle import Lifecycle
                from types import SimpleNamespace
                with FileLease(TaskStore(repo).root / "locks" / (args.id + "-controller.lock")):
                    # Local qualification needs the shared task/evidence context,
                    # never a GitHub origin, account, transport or delivery backend.
                    context = SimpleNamespace(name=value.get("repository") or "local-verification")
                    controller = Lifecycle(repo, args.id, backend=context)
                    head = git(root, "rev-parse", "HEAD").decode().strip()
                    controller.verification(value, root, head)
                    controller.acceptance(controller.task(), root, head)
                print(json.dumps({"status": "VERIFIED", "task_id": args.id, "head_sha": head}))
                return 0
            return subprocess.run(verification_argv(value, root), cwd=root).returncode
        elif args.task_command in {"submit", "deliver"}:
            from task_lifecycle import Lifecycle
            from task_queue import submit
            from task_worker import launch
            if args.task_command == "deliver":
                store = TaskStore(repo)
                if store.load(args.id).get("host_platform") != os.name:
                    raise ValueError("Use the registered lifecycle host Git implementation")
                with integration_scope(store), FileLease(store.root / "locks" / (args.id + "-controller.lock")):
                    result = Lifecycle(repo, args.id).deliver()
                if result.get("status") in {"NEEDS_ATTENTION", "DEV_CI_FAILED", "CLEANED"}:
                    print(json.dumps(result, indent=2))
                    return 0 if result["status"] == "CLEANED" else 2
            value = {"queue": submit(repo, args.id), "worker": launch(repo)}
        else:
            from controller_leases import entry_command
            return subprocess.run(entry_command(Path(__file__).with_name("task_lifecycle.py"),
                "resume", "--repo", str(repo), "--id", args.id), cwd=repo).returncode
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0 if value.get("status") not in {"BLOCKED","FAIL"} else 2
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
