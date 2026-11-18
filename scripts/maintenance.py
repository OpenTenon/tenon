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


def retained_versions(store: TaskStore, *, reasons=None) -> tuple[set[str], bool]:
    retained = set()
    def keep(value, reason):
        retained.add(value)
        if reasons is not None:
            reasons.setdefault(value, []).append(reason)
    uncertain = False
    executing = Path(__file__).resolve().parents[1]
    if executing.parent == (store.root / "controllers").resolve() and re.fullmatch(r"[0-9a-f]{40}", executing.name):
        keep(executing.name, "executing-controller")
    installation = store.root / "bin/installation.json"
    if installation.exists():
        value = json.loads(installation.read_text(encoding="utf-8"))
        if value.get("repo_id") != store.repo_id:
            raise ValueError("Installed entry belongs to another repository")
        if value.get("schema_version") != 1 or any(value.get(key) is not None and not re.fullmatch(r"[0-9a-f]{40}", str(value[key])) for key in ("commit", "previous_commit")):
            uncertain = True
        for key, reason in (("commit", "current-installed"), ("previous_commit", "previous-installed")):
            if value.get(key):
                keep(value[key], reason)
    for task in store.all():
        if task.get("status") != "CLEANED" and task.get("controller"):
            path = Path(task["controller"]).resolve()
            version = path.parent.parent
            if path.parent.name != "scripts" or version.parent != (store.root / "controllers").resolve() or not re.fullmatch(r"[0-9a-f]{40}", version.name):
                uncertain = True
            else:
                keep(version.name, "active-task:" + str(task.get("task_id", "unknown")))
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
                keep(observed["controller_commit"], "native-worker-lease")
    if heartbeat.exists():
        value = observed
        if value.get("host_platform") != os.name or process_alive(int(value.get("pid", 0))):
            if value.get("controller_commit"):
                keep(value["controller_commit"], "recorded-worker-reference")
            else:
                unknown_worker = True
    return retained, unknown_worker


def controller_inventory(store: TaskStore, *, exclusive_versions=None) -> dict:
    from controller_leases import version_observation, redirected, registry_issues
    exclusive_versions = exclusive_versions or {}
    reasons = {}
    retained, unknown_worker = retained_versions(store, reasons=reasons)
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
            retention_reasons = list(reasons.get(directory.name, []))
            retention_reasons += [name for condition, name in ((recent, "within-24h"),
                (unknown_worker, "worker-identity-unknown"), (bool(issues), "lease-registry-issues"),
                (bool(lease.get("held")), "active-os-execution-lease"),
                (lease["state"] == "UNKNOWN", "execution-identity-unknown-or-legacy"),
                (bool(unknown), "unclassified-payloads")) if condition]
            size = None if unknown else sum(path.stat().st_size for path in directory.rglob("*") if path.is_file() and not path.is_symlink())
            values.append({"path": str(directory), "commit": directory.name,
                           "state": "UNCLASSIFIED" if unknown else "UNKNOWN" if lease["state"] == "UNKNOWN" else
                                    "RETAINED" if directory.name in retained or unknown_worker or recent or issues or lease.get("held") else "RECLAIMABLE",
                           "unclassified": unknown, "lease": lease, "logical_bytes": size,
                           "within_24h": recent, "referenced": directory.name in retained,
                           "retention_reasons": sorted(set(retention_reasons))})
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


def _inventory_json(path: Path) -> dict:
    """Bounded metadata read, with exception types preserved for the observer."""
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024):
        raise ValueError("Inventory metadata is redirected, nonregular or oversized")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Inventory metadata must be an object")
    return value


def _inventory_directory(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise ValueError("Inventory directory is redirected or unknown")


def retained_drill_reports(common: Path) -> dict:
    """Read original reports and owned boundaries only; never replay a fixture."""
    common = common.resolve()
    reports, scratch = common / "tenon/operations/drills", common / "tenon/drill-scratch"
    rows = []
    try:
        for parent in (common / "tenon", common / "tenon/operations", reports):
            _inventory_directory(parent)
        paths = sorted(reports.iterdir())
    except FileNotFoundError:
        return {"rows": [], "namespace": "MISSING", "qualification": "No deletion attribution or cleanup permission"}
    except (PermissionError, OSError, ValueError) as error:
        return {"rows": [{"inspection": "UNKNOWN", "error_type": type(error).__name__}], "namespace": "UNKNOWN"}
    for directory in paths:
        path = directory / "summary.json"
        try:
            _inventory_directory(directory)
            report = _inventory_json(path)
            if (type(report.get("schema_version")) is not int or report["schema_version"] != 1 or report.get("kind") != "engineering-recovery-drill"
                    or Path(report.get("production_common", "")) != common or not isinstance(report.get("scenarios"), list)):
                raise ValueError("Report schema/repository differs")
        except (OSError, ValueError, TypeError) as error:
            rows.append({"report": str(path), "inspection": "UNKNOWN", "error_type": type(error).__name__})
            continue
        for scenario in report["scenarios"]:
            if isinstance(scenario, dict) and (scenario.get("status") == "PASS" or scenario.get("fixture_created") is False):
                continue
            row = {"report": str(path), "scenario": scenario.get("scenario") if isinstance(scenario, dict) else None,
                   "reported_status": scenario.get("status") if isinstance(scenario, dict) else None,
                   "inspection": "UNKNOWN", "phase": None, "ownership": "UNKNOWN"}
            try:
                raw = scenario.get("recovery_manifest") if isinstance(scenario, dict) else None
                if not isinstance(raw, str):
                    raise ValueError("Recovery manifest is missing")
                manifest = Path(raw)
                if (not manifest.is_absolute() or manifest.name != "fixture.json" or manifest.parent.parent != scratch
                        or not manifest.parent.name.startswith("tenon-drill-") or ".." in manifest.parts):
                    raise ValueError("Manifest is outside this repository's owned scratch")
                for directory_path in (scratch, manifest.parent):
                    _inventory_directory(directory_path)
                info = (manifest.parent / "owner-token").lstat()
                if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > 4096
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise ValueError("Owner token is unknown or redirected")
                data = _inventory_json(manifest)
                if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
                        or data.get("phase") not in {"CREATING", "READY"} or not isinstance(data.get("owner_token"), str)
                        or not data["owner_token"] or Path(data.get("production_common", "")) != common
                        or Path(data.get("root", "")) != manifest.parent):
                    raise ValueError("Manifest repository/root differs")
                # ready=False verifies the existing owner token and persistent
                # boundary. It does not call _control/TaskStore/fake backend.
                from engineering_drills import _boundary
                data = _boundary(manifest, ready=False)
                row.update(inspection="OBSERVED", phase=data.get("phase"), ownership="BOUNDARY_OWNER_MATCH",
                           recovery_manifest=str(manifest), qualification="Partial setup observation; not readiness or deletion permission")
            except FileNotFoundError:
                row["inspection"] = "MISSING"
                row["qualification"] = "Observed missing metadata; cause and prior deletion remain unknown"
            except PermissionError:
                row["inspection"] = "UNKNOWN_PERMISSION"
            except (OSError, ValueError, TypeError) as error:
                row.update(inspection="UNKNOWN", error_type=type(error).__name__)
            rows.append(row)
    return {"rows": rows, "namespace": "OBSERVED", "qualification": "Retained failed drill evidence only; no recovery, mutation or deletion"}


def archive_capacity(store, tasks) -> dict:
    """Logical ZIP bytes for registered archives, including task supplements."""
    root = store.root
    references, unknown = {}, []
    reference_count = 0
    for task in tasks:
        task_id = task.get("task_id")
        supplements = (root / "operations" / task_id
                       if isinstance(task_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", task_id) else None)
        for item in [task.get("archive"), *task.get("archive_history", [])]:
            if item is None:
                continue
            reference_count += 1
            raw = item.get("path") if isinstance(item, dict) else None
            path = Path(raw) if isinstance(raw, str) else None
            if (path is None or not path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".zip"
                    or not (path.is_relative_to(root / "archives")
                            or supplements is not None and path.is_relative_to(supplements))):
                unknown.append({"reason": "unqualified-archive-reference"})
                continue
            references.setdefault(os.path.normcase(str(path)), path)

    sizes, identities = {}, {}
    unknown_identity = 0
    for key, path in references.items():
        try:
            for parent in reversed(path.parents):
                if parent.is_relative_to(root):
                    _inventory_directory(parent)
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise ValueError("Archive file is redirected or unknown")
            sizes[key] = info.st_size
            if info.st_ino > 0 and info.st_dev >= 0:
                identities.setdefault((info.st_dev, info.st_ino), info.st_size)
            else:
                unknown_identity += 1
        except (OSError, ValueError) as error:
            unknown.append({"reason": "registered-archive-not-observed", "path": key, "error_type": type(error).__name__})
    return {"scope": "Registered private archives and supplements", "archive_retention": "indefinite",
            "references": reference_count, "duplicate_references": reference_count-len(references)-sum(
                row["reason"] == "unqualified-archive-reference" for row in unknown),
            "observed_zip_paths": len(sizes), "confirmed_unique_files": len(identities),
            "known_path_bytes": sum(sizes.values()), "confirmed_unique_bytes": sum(identities.values()),
            "unknown_identity_paths": unknown_identity, "unknown_entries": unknown,
            "total_bytes": sum(identities.values()) if not unknown and not unknown_identity else None,
            "qualification": "Logical ZIP sizes, not allocated disk space. Distinct files with identical hashes are counted separately; unknowns stay unknown."}


def audit(repo: Path, live: bool = True, include_source: bool = True) -> dict:
    store = TaskStore(repo)
    result = {"schema_version": 1, "kind": "engineering-maintenance", "run_id": run_id(), "started_at": utc_now(), "checks": []}
    tasks = store.all()
    result["merged_cleanup_backlog"] = [{"task_id": task["task_id"], "status": task.get("status"),
        "merged_at": task.get("delivered_at") or task["delivery"].get("merged_at"),
        "qualification": "Recorded merged but not CLEANED; inspect existing live gates before acting"}
        for task in tasks if isinstance(task.get("delivery"), dict) and task["delivery"].get("merged") is True and task.get("status") != "CLEANED"]
    result["retained_drill_reports"] = retained_drill_reports(store.common)
    result["archive_capacity"] = archive_capacity(store, tasks)
    for task in tasks:
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
    work.extend({"check":"retained-drill:"+str(index),"state":"PROPOSED","priority":"P2",
                 "reason":"Inspect retained failed drill evidence; do not delete or replay automatically",
                 "evidence":row.get("report"),"observations":row}
                for index,row in enumerate(result.get("retained_drill_reports",{}).get("rows",[])))
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
