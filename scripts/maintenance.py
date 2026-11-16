#!/usr/bin/env python3
"""Private archive recovery exercises and conservative, version-aware engineering upkeep."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from engineering_common import ROOT, digest, git, run_id, utc_now, write_json
from task_archive import safe_relative, verify_task_archive, verify_controller_source as verify_controller
from task_store import FileLease, TaskStore, process_alive


def unclassified_payloads(directory: Path, manifest: dict) -> list[str]:
    known = {Path(item["path"]) for item in manifest["files"]} | {Path("controller.json")}
    parents = {parent for path in known for parent in path.parents}
    caches = {path.parent / "__pycache__" for path in known if path.suffix == ".py"}
    unknown = ["controller.json:unclassified-metadata"] if set(manifest) - {"schema_version", "commit", "files"} else []
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in [*directories, *files]:
            path = Path(parent) / name
            relative = path.relative_to(directory)
            reparse = path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            bytecode = relative.parent in caches and relative.suffix in {".pyc", ".pyo"} and any(
                path.suffix == ".py" and relative.name.startswith(path.stem + ".") and path.parent == relative.parent.parent for path in known)
            if reparse or (relative not in known and relative not in parents and relative not in caches and not bytecode):
                unknown.append(relative.as_posix())
                if name in directories:
                    directories.remove(name)
    return unknown


def restore_check(repo: Path, task_id: str) -> dict:
    store = TaskStore(repo)
    task = store.load(task_id)
    manifest = verify_task_archive(store, task)
    scratch = store.root / "restore-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    if scratch.is_symlink() or not scratch.resolve().is_relative_to(store.root.resolve()):
        raise ValueError("Restore scratch storage escaped this repository")
    required = sum(item["size"] for item in manifest["files"]) + 1024 * 1024
    if shutil.disk_usage(scratch).free < required:
        raise OSError("Insufficient capacity for a private restore exercise")
    with tempfile.TemporaryDirectory(prefix=task_id + "-", dir=scratch) as temporary:
        destination = Path(temporary).resolve()
        if not destination.is_relative_to(scratch.resolve()):
            raise ValueError("Restore target escaped owned scratch storage")
        with zipfile.ZipFile(task["archive"]["path"]) as archive:
            for item in manifest["files"]:
                target = destination / safe_relative(item["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item["path"]) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                if digest(target) != item["sha256"]:
                    raise ValueError("Restored payload differs")
        restored = len(manifest["files"])
    return {"status": "PASS", "task_id": task_id, "restored_files": restored,
            "archive_sha256": task["archive"]["sha256"], "qualification": "private recovery exercise; not source verification or signoff"}


def registered_host(store: TaskStore) -> str | None:
    for path in (store.root / "host.json", store.root / "bin/installation.json"):
        if not path.exists():
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("repo_id") != store.repo_id:
            raise ValueError("Host/entry registration belongs to another repository")
        if value.get("host_platform") in {"nt", "posix"}:
            return value["host_platform"]
        primary = str(value.get("repo", ""))
        if re.match(r"^[A-Za-z]:[/\\]", primary) or primary.startswith("\\\\"):
            return "nt"
        if primary.startswith("/"):
            return "posix"
    return None


def retained_versions(store: TaskStore) -> tuple[set[str], bool]:
    retained = set()
    executing = Path(__file__).resolve().parents[1]
    if executing.parent == (store.root / "controllers").resolve() and re.fullmatch(r"[0-9a-f]{40}", executing.name):
        retained.add(executing.name)
    installation = store.root / "bin/installation.json"
    if installation.exists():
        value = json.loads(installation.read_text(encoding="utf-8"))
        if value.get("repo_id") != store.repo_id:
            raise ValueError("Installed entry belongs to another repository")
        retained.update(item for item in (value.get("commit"), value.get("previous_commit")) if item)
    for task in store.all():
        if task.get("status") != "CLEANED" and task.get("controller"):
            pieces = str(task["controller"]).replace("\\", "/").split("/")
            retained.update(item for item in pieces if re.fullmatch(r"[0-9a-f]{40}", item))
    heartbeat = store.root / "operations/worker-latest.json"
    foreign = registered_host(store) not in {None, os.name}
    unknown_worker = foreign
    observed = json.loads(heartbeat.read_text(encoding="utf-8")) if heartbeat.exists() else {}
    lease_path = store.root / "locks/worker.lock"
    if lease_path.exists() and not foreign:
        from task_store import lease_is_held
        if lease_is_held(lease_path):
            try:
                with lease_path.open("rb") as stream:
                    stream.seek(1)  # Windows byte-range locking protects byte zero.
                    lease = json.loads(stream.read())
            except (OSError, ValueError):
                lease = {}
            if (not observed.get("controller_commit") or observed.get("pid") != lease.get("pid")
                    or observed.get("lease_token") != lease.get("token")):
                unknown_worker = True
            else:
                retained.add(observed["controller_commit"])
    if heartbeat.exists():
        value = observed
        if value.get("host_platform") != os.name or process_alive(int(value.get("pid", 0))):
            if value.get("controller_commit"):
                retained.add(value["controller_commit"])
            else:
                unknown_worker = True
    return retained, unknown_worker


def controller_inventory(store: TaskStore) -> dict:
    retained, unknown_worker = retained_versions(store)
    values = []
    for directory in sorted((store.root / "controllers").iterdir()) if (store.root / "controllers").exists() else []:
        if not directory.is_dir() or directory.is_symlink() or not re.fullmatch(r"[0-9a-f]{40}", directory.name):
            values.append({"path": str(directory), "state": "UNKNOWN"})
            continue
        try:
            manifest = verify_controller(directory, store.repo)
            unknown = unclassified_payloads(directory, manifest)
            recent = datetime.now(timezone.utc).timestamp() - directory.stat().st_mtime < 86400
            values.append({"path": str(directory), "commit": directory.name,
                           "state": "UNCLASSIFIED" if unknown else "RETAINED" if directory.name in retained or unknown_worker or recent else "RECLAIMABLE",
                           "unclassified": unknown})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            values.append({"path": str(directory), "state": "INVALID", "reason": str(error)})
    return {"versions": values, "active_worker_identity_unknown": unknown_worker}


def prune_controllers(repo: Path, apply: bool = False) -> dict:
    store = TaskStore(repo)
    if not apply:
        return {"status": "PASS", "mode": "read-only", **controller_inventory(store), "removed": []}
    if registered_host(store) not in {None, os.name}:
        raise ValueError("Controller reclamation must run on the registered native host")
    removed = []
    with FileLease(store.root / "locks/entry-install.lock"), FileLease(store.root / "locks/integration.lock"):
        inventory = controller_inventory(store)
        for item in inventory["versions"]:
            if item["state"] != "RECLAIMABLE" or not apply:
                continue
            root = (store.root / "controllers").resolve()
            target = Path(item["path"])
            if target.is_symlink() or target.resolve().parent != root or not root.is_relative_to(store.root.resolve()):
                raise ValueError("Controller deletion escaped its owned storage")
            manifest = verify_controller(target, store.repo)
            if unclassified_payloads(target, manifest):
                raise ValueError("Controller gained unclassified files before reclamation")
            # Never descend through links/junctions, even in disposable cached bytecode.
            for parent, directories, files in os.walk(target, followlinks=False):
                for name in [*directories, *files]:
                    entry = Path(parent) / name
                    if entry.is_symlink() or getattr(entry.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                        raise ValueError("Controller contains an unowned reparse/symlink entry")
            shutil.rmtree(target)
            removed.append(item["commit"])
    return {"status": "PASS", "mode": "apply" if apply else "read-only", **inventory, "removed": removed}


def audit(repo: Path, live: bool = True, include_source: bool = True) -> dict:
    store = TaskStore(repo)
    result = {"schema_version": 1, "kind": "engineering-maintenance", "run_id": run_id(), "started_at": utc_now(), "checks": []}
    for task in store.all():
        if not task.get("archive"):
            continue
        archives = [task["archive"], *task.get("archive_history", [])]
        seen = set()
        for index, info in enumerate(archives):
            key = (info["path"], info["sha256"])
            if key in seen:
                continue
            seen.add(key)
            check_id = "archive:" + task["task_id"] + (":history-" + str(index) if index else "")
            try:
                manifest = verify_task_archive(store, {**task, "archive": info})
                result["checks"].append({"id": check_id, "status": "PASS", "files": len(manifest["files"])})
            except (OSError, ValueError, KeyError) as error:
                result["checks"].append({"id": check_id, "status": "FAIL", "reason": str(error)})
    result["controllers"] = controller_inventory(store)
    states = {item["state"] for item in result["controllers"]["versions"]}
    result["checks"].append({"id": "controllers", "status": "FAIL" if "INVALID" in states else "BLOCKED" if "UNCLASSIFIED" in states else "PASS"})
    if include_source:
        try:
            from ci_contract import audit_source
            source = git(repo, "rev-parse", "origin/dev").decode().strip()
            source_report = audit_source(repo, source)
            result["checks"].append({"id": "source-contract", **source_report})
            if live:
                from github_rules import audit as audit_rules
                from task_lifecycle import repository_name
                result["checks"].append({"id": "published-rules", **audit_rules(repository_name(repo), repo, source)})
        except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as error:
            result["checks"].append({"id": "source-or-rules", "status": "BLOCKED", "reason": str(error)})
    usage = shutil.disk_usage(store.root)
    result["capacity"] = {"free_bytes": usage.free, "total_bytes": usage.total, "archive_retention": "indefinite"}
    statuses = {item["status"] for item in result["checks"]}
    result.update(status="FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS", completed_at=utc_now())
    directory = store.root / "maintenance" / result["run_id"]
    write_json(directory / "summary.json", result)
    work = [{"check": item["id"], "state": "PROPOSED", "reason": item.get("reason") or item.get("errors"),
             "evidence": str(directory / "summary.json")} for item in result["checks"] if item["status"] != "PASS"]
    write_json(directory / "work-items.json", work)
    return {**result, "report": str(directory / "summary.json"), "work_items": str(directory / "work-items.json")}


def run_due(repo: Path, force: bool = False) -> dict:
    store = TaskStore(repo)
    state_path = store.root / "maintenance-state.json"
    now = datetime.now(timezone.utc)
    with FileLease(store.root / "locks/maintenance.lock", timeout=0):
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if state and (state.get("schema_version") != 1 or state.get("repo_id") != store.repo_id):
            raise ValueError("Maintenance state belongs to another repository/version")
        if not force and state.get("next_attempt_at") and datetime.fromisoformat(state["next_attempt_at"]) > now:
            return {"status": "NOT_DUE", "next_attempt_at": state["next_attempt_at"]}
        result = audit(repo)
        tasks = [task for task in store.all() if task.get("archive")]
        cursor = state.get("restore_cursor", 0)
        if tasks and not any(item["status"] != "PASS" for item in result["checks"] if item["id"].startswith("archive:")):
            try:
                result["restore_exercise"] = restore_check(repo, tasks[cursor % len(tasks)]["task_id"])
                cursor += 1
            except (OSError, ValueError, KeyError) as error:
                result["checks"].append({"id": "restore-exercise", "status": "FAIL", "reason": str(error)})
                result["status"] = "FAIL"
        write_json(Path(result["report"]), result)
        write_json(Path(result["work_items"]), [{"check": item["id"], "state": "PROPOSED", "reason": item.get("reason") or item.get("errors"),
                   "evidence": result["report"]} for item in result["checks"] if item["status"] != "PASS"])
        delay = timedelta(days=30) if result["status"] == "PASS" else timedelta(days=1)
        write_json(state_path, {"schema_version": 1, "repo_id": store.repo_id, "last_report": result["report"],
                              "last_status": result["status"], "restore_cursor": cursor, "next_attempt_at": (now + delay).isoformat()})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("audit")
    check.add_argument("--offline", action="store_true")
    restore = commands.add_parser("restore-check")
    restore.add_argument("--id", required=True)
    prune = commands.add_parser("prune-controllers")
    prune.add_argument("--apply", action="store_true")
    due = commands.add_parser("run")
    due.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "audit":
            value = audit(args.repo.resolve(), not args.offline)
        elif args.command == "restore-check":
            value = restore_check(args.repo.resolve(), args.id)
        elif args.command == "prune-controllers":
            value = prune_controllers(args.repo.resolve(), args.apply)
        else:
            value = run_due(args.repo.resolve(), args.force)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        value = {"status": "BLOCKED", "reason": str(error)}
    print(json.dumps(value, indent=2))
    return 0 if value["status"] in {"PASS", "NOT_DUE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
