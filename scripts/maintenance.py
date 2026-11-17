#!/usr/bin/env python3
"""Private archive recovery exercises and conservative, version-aware engineering upkeep."""
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
from engineering_common import safe_exception, safe_error_payload
from engineering_common import ROOT, digest, git, run_id, utc_now, write_json
from task_archive import safe_relative, verify_task_archive, verify_controller_source as verify_controller
from task_store import FileLease, TaskStore, process_alive


def unclassified_payloads(directory: Path, manifest: dict) -> list[str]:
    known = {Path(item["path"]) for item in manifest["files"]} | {Path("controller.json")}
    parents = {parent for path in known for parent in path.parents}
    caches = {path.parent / "__pycache__" for path in known if path.suffix == ".py"}
    allowed = {"schema_version", "commit", "files"}
    if manifest.get("controller_lease_abi") == 1:
        allowed.add("controller_lease_abi")
    unknown = ["controller.json:unclassified-metadata"] if set(manifest) - allowed else []
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
    uncertain = False
    executing = Path(__file__).resolve().parents[1]
    if executing.parent == (store.root / "controllers").resolve() and re.fullmatch(r"[0-9a-f]{40}", executing.name):
        retained.add(executing.name)
    installation = store.root / "bin/installation.json"
    if installation.exists():
        value = json.loads(installation.read_text(encoding="utf-8"))
        if value.get("repo_id") != store.repo_id:
            raise ValueError("Installed entry belongs to another repository")
        if value.get("schema_version") != 1 or any(value.get(key) is not None and not re.fullmatch(r"[0-9a-f]{40}", str(value[key])) for key in ("commit", "previous_commit")):
            uncertain = True
        retained.update(item for item in (value.get("commit"), value.get("previous_commit")) if item)
    for task in store.all():
        if task.get("status") != "CLEANED" and task.get("controller"):
            path = Path(task["controller"]).resolve()
            version = path.parent.parent
            if path.parent.name != "scripts" or version.parent != (store.root / "controllers").resolve() or not re.fullmatch(r"[0-9a-f]{40}", version.name):
                uncertain = True
            else:
                retained.add(version.name)
    heartbeat = store.root / "operations/worker-latest.json"
    foreign = registered_host(store) not in {None, os.name}
    unknown_worker = foreign or uncertain
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


def controller_inventory(store: TaskStore, *, exclusive_versions=None) -> dict:
    from controller_leases import version_observation, redirected, registry_issues
    exclusive_versions = exclusive_versions or {}
    retained, unknown_worker = retained_versions(store)
    issues = registry_issues(store.root)
    values = []
    controllers = store.root / "controllers"
    if os.path.lexists(controllers) and (redirected(controllers) or not controllers.is_dir()):
        return {"versions": [{"path": str(controllers), "state": "UNKNOWN"}], "active_worker_identity_unknown": True,
                "lease_registry_issues": ["Controller storage is redirected or unavailable"]}
    for directory in sorted(controllers.iterdir()) if controllers.exists() else []:
        if not directory.is_dir() or redirected(directory) or not re.fullmatch(r"[0-9a-f]{40}", directory.name):
            values.append({"path": str(directory), "state": "UNKNOWN"})
            continue
        try:
            manifest = verify_controller(directory, store.repo)
            unknown = unclassified_payloads(directory, manifest)
            recent = datetime.now(timezone.utc).timestamp() - directory.stat().st_mtime < 86400
            lease = version_observation(store.root, directory, owned_exclusive=exclusive_versions.get(directory.name))
            size = None if unknown else sum(path.stat().st_size for path in directory.rglob("*") if path.is_file() and not path.is_symlink())
            values.append({"path": str(directory), "commit": directory.name,
                           "state": "UNCLASSIFIED" if unknown else "UNKNOWN" if lease["state"] == "UNKNOWN" else
                                    "RETAINED" if directory.name in retained or unknown_worker or recent or issues or lease.get("held") else "RECLAIMABLE",
                           "unclassified": unknown, "lease": lease, "logical_bytes": size,
                           "within_24h": recent, "referenced": directory.name in retained})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            values.append({"path": str(directory), "state": "INVALID", "reason": safe_exception(error)})
    return {"versions": values, "active_worker_identity_unknown": unknown_worker, "lease_registry_issues": issues}


def prune_controllers(repo: Path, apply: bool = False) -> dict:
    from controller_leases import ReadLock, common_directory, mutation_context, redirected, verify_version_lock
    common = common_directory(repo)
    if not (common / "tenon/repository.json").is_file():
        return {"status": "BLOCKED", "mode": "apply" if apply else "read-only", "removed": [],
                "reason": "Runtime identity is absent; controller inventory never initializes a repository"}
    store = TaskStore(repo)
    if not apply:
        return {"status": "PASS", "mode": "read-only", **controller_inventory(store), "removed": []}
    if registered_host(store) not in {None, os.name}:
        raise ValueError("Controller reclamation must run on the registered native host")
    preflight = controller_inventory(store)
    if not any(item.get("state") == "RECLAIMABLE" for item in preflight["versions"]):
        return {"status": "PASS", "mode": "apply", **preflight, "removed": [], "logical_bytes_removed": 0,
                "reason": "No qualified unreferenced controller is eligible; unknown material remains preserved"}
    removed = []
    before = shutil.disk_usage(store.root).free
    removed_bytes = 0
    with mutation_context(store):
        inventory = controller_inventory(store)
        for item in inventory["versions"]:
            if item["state"] != "RECLAIMABLE" or not apply:
                continue
            root = (store.root / "controllers").resolve()
            target = Path(item["path"])
            if redirected(root) or redirected(target) or target.resolve() != target or target.parent != root or not root.is_relative_to(store.root.resolve()):
                raise ValueError("Controller deletion escaped its owned storage")
            with ReadLock(store.root / "controller-leases" / (item["commit"] + ".lock"), exclusive=True, timeout=0) as lease:
                verify_version_lock(store.root, target, lease)
                # Recompute references, age, registry and exact native ownership for each deletion.
                fresh = controller_inventory(store, exclusive_versions={item["commit"]: lease})
                selected = next((row for row in fresh["versions"] if row.get("commit") == item["commit"]), None)
                if not selected or selected["state"] != "RECLAIMABLE":
                    continue
                manifest = verify_controller(target, store.repo)
                if unclassified_payloads(target, manifest):
                    raise ValueError("Controller gained unclassified files before reclamation")
                for parent, directories, files in os.walk(target, followlinks=False):
                    for name in [*directories, *files]:
                        if redirected(Path(parent) / name):
                            raise ValueError("Controller contains an unowned reparse/symlink entry")
                if target.resolve().parent != root or redirected(target):
                    raise ValueError("Controller changed before its exact removal")
                shutil.rmtree(target)
                removed.append(item["commit"])
                removed_bytes += selected["logical_bytes"]
    return {"status": "PASS", "mode": "apply", **inventory, "removed": removed,
            "logical_bytes_removed": removed_bytes, "free_bytes_before": before,
            "free_bytes_after": shutil.disk_usage(store.root).free,
            "capacity_qualification": "Observed free space may include unrelated filesystem activity; archives are never reclaimed"}


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
                result["checks"].append({"id": check_id, "status": "FAIL", "reason": safe_exception(error)})
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
            result["checks"].append({"id": "source-or-rules", "status": "BLOCKED", "reason": safe_exception(error)})
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


def proposed_work_items(result: dict) -> list[dict]:
    work = [{"check":item["id"],"state":"PROPOSED","reason":item.get("reason") or item.get("errors"),"evidence":result["report"]}
            for item in result["checks"] if item["status"] != "PASS"]
    work.extend({"check":"metrics:"+item["code"],"state":"PROPOSED","priority":item["priority"],
                 "task_ids":item["task_ids"],"reason":item["action"],"evidence":result["report"],"observations":item["evidence"]}
                for item in result.get("improvement_items",{}).get("items",[]))
    return work


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
        try:
            from engineering_metrics import collect, improvement_items
            metrics = collect(repo,as_of=now,probe_remote=False)
            result["engineering_metrics_v1"] = metrics
            result["improvement_items"] = improvement_items(metrics,now.strftime("%Y-%m"))
            directory = Path(result["report"]).parent
            write_json(directory/"metrics.json",metrics)
            write_json(directory/"improvement-items.json",result["improvement_items"])
            result["metrics_report"] = str(directory/"metrics.json")
            result["improvement_items_report"] = str(directory/"improvement-items.json")
            if metrics.get("diagnostics"):
                result["checks"].append({"id":"metrics-observation","status":"BLOCKED","reason":"Some metric observations are incomplete","diagnostics":metrics["diagnostics"]})
                if result["status"] == "PASS":
                    result["status"] = "BLOCKED"
        except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
            result["checks"].append({"id":"metrics-observation","status":"BLOCKED","reason":safe_exception(error)})
            if result["status"] == "PASS":
                result["status"] = "BLOCKED"
        tasks = [task for task in store.all() if task.get("archive")]
        cursor = state.get("restore_cursor", 0)
        if tasks and not any(item["status"] != "PASS" for item in result["checks"] if item["id"].startswith("archive:")):
            try:
                result["restore_exercise"] = restore_check(repo, tasks[cursor % len(tasks)]["task_id"])
                cursor += 1
            except (OSError, ValueError, KeyError) as error:
                result["checks"].append({"id": "restore-exercise", "status": "FAIL", "reason": safe_exception(error)})
                result["status"] = "FAIL"
        queue_idle = True
        for path in (store.root / "queue").glob("*.json"):
            try:
                value = json.loads(path.read_bytes())
                if value.get("schema_version") != 1 or value.get("repo_id") != store.repo_id or value.get("state") != "DONE":
                    queue_idle = False
            except (OSError, ValueError, AttributeError):
                queue_idle = False
        if result["status"] == "PASS" and queue_idle:
            try:
                result["controller_reclamation"] = prune_controllers(repo, apply=True)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
                result["controller_reclamation"] = {"status": "BLOCKED", "reason": safe_exception(error)}
                result["status"] = "BLOCKED"
        else:
            result["controller_reclamation"] = {"status": "NOT_RUN", "reason": "Maintenance audit is not passing or the real queue is not idle"}
        write_json(Path(result["report"]), result)
        write_json(Path(result["work_items"]), proposed_work_items(result))
        delay = timedelta(days=30) if result["status"] == "PASS" else timedelta(days=1)
        write_json(state_path, {"schema_version": 1, "repo_id": store.repo_id, "last_report": result["report"],
                              "last_status": result["status"], "restore_cursor": cursor, "next_attempt_at": (now + delay).isoformat()})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
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
    commands.add_parser("drill", help="Run independent processes against owned temporary Git fixtures")
    args = parser.parse_args()
    try:
        if args.command == "audit":
            value = audit(args.repo.resolve(), not args.offline)
        elif args.command == "restore-check":
            value = restore_check(args.repo.resolve(), args.id)
        elif args.command == "prune-controllers":
            value = prune_controllers(args.repo.resolve(), args.apply)
        elif args.command == "drill":
            from engineering_drills import run_drills
            value = run_drills(args.repo.resolve())
        else:
            value = run_due(args.repo.resolve(), args.force)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        value = {"status": "BLOCKED", "reason": safe_exception(error)}
    print(json.dumps(value, indent=2))
    return 0 if value["status"] in {"PASS", "NOT_DUE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
