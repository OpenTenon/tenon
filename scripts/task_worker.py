#!/usr/bin/env python3
"""Bounded, model-free host worker and owned Windows Task Scheduler registration."""
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

import csv
import io
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from engineering_common import safe_exception, safe_error_payload
from engineering_common import utc_now, write_json
from task_archive import verify_task_archive, worktrees
from task_queue import reconcile
from task_store import FileLease, TaskStore


def config_path(store: TaskStore) -> Path:
    return store.root / "worker.json"


def configuration(store: TaskStore) -> dict:
    path = config_path(store)
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "schema_version": 1, "repo_id": store.repo_id, "enabled": False}
    if value.get("schema_version") != 1 or value.get("repo_id") != store.repo_id:
        raise ValueError("Worker configuration belongs to another repository/version")
    return value


def set_enabled(repo: Path, enabled: bool, acceptance_task: str | None = None) -> dict:
    store = TaskStore(repo)
    value = configuration(store)
    if enabled:
        if not acceptance_task:
            raise ValueError("Enable requires a real cleaned Git-managed acceptance task")
        task = store.load(acceptance_task)
        if (task.get("status") != "CLEANED" or task.get("managed_identity")
                or not task.get("delivery", {}).get("merged") or not all(task.get("cleanup", {}).values())):
            raise ValueError("Acceptance task has not completed the real delivery/cleanup loop")
        if set(task.get("cleanup", {})) != {"remote_deleted", "worktree_removed", "local_deleted", "tracking_deleted"}:
            raise ValueError("Acceptance cleanup inventory is incomplete")
        verify_task_archive(store, task)
        value["acceptance_task"] = acceptance_task
    value.update(enabled=enabled, updated_at=utc_now())
    write_json(config_path(store), value)
    return value


def run(repo: Path, max_seconds: float = 900, interval: float = 25) -> dict:
    if not 0 < max_seconds <= 1200 or not 0 < interval <= 300:
        raise ValueError("Worker bounds must be positive, at most 1200/300 seconds")
    store = TaskStore(repo)
    started = time.monotonic()
    with FileLease(store.root / "locks/worker.lock", timeout=0) as lease:
        code = Path(__file__).resolve().parents[1]
        commit = code.name if code.parent == (store.root / "controllers").resolve() and re.fullmatch(r"[0-9a-f]{40}", code.name) else None
        identity = {"pid": os.getpid(), "host_platform": os.name, "repo_id": store.repo_id,
                    "lease_token": lease.token, "controller_commit": commit}
        write_json(store.root / "operations/worker-latest.json", {**identity, "state": "STARTING", "tasks": [], "at": utc_now()})
        while True:
            enabled = configuration(store)["enabled"]
            value = reconcile(repo, apply=enabled)
            value.update(**identity, enabled=enabled, completed_at=utc_now())
            write_json(store.root / "operations/worker-latest.json", safe_error_payload(value))
            pending = any(item["state"] in {"WAITING", "BACKOFF", "QUEUED"} for item in value["tasks"])
            if enabled and not pending:
                try:
                    from maintenance import run_due
                    upkeep = run_due(repo)
                    value["maintenance"] = {name: upkeep.get(name) for name in ("status", "report", "next_attempt_at")}
                except (OSError, ValueError, KeyError, RuntimeError, ImportError, subprocess.SubprocessError) as error:
                    value["maintenance"] = {"status": "BLOCKED", "reason": safe_exception(error)}
                    write_json(store.root / "operations/maintenance-error.json", {**value["maintenance"], "at": utc_now()})
                write_json(store.root / "operations/worker-latest.json", safe_error_payload(value))
            remaining = max_seconds - (time.monotonic() - started)
            if not enabled or not pending or remaining <= 0:
                return safe_error_payload(value)
            time.sleep(min(interval, remaining))


def schedule_xml(repo: Path, repo_id: str, executable: Path, launcher: Path, sid: str) -> str:
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", namespace)
    def node(parent, name, text=None, **attributes):
        item = ET.SubElement(parent, "{" + namespace + "}" + name, attributes)
        item.text = text
        return item
    task = ET.Element("{" + namespace + "}Task", {"version": "1.2"})
    registration = node(task, "RegistrationInfo")
    node(registration, "Description", "Tenon owned queue " + repo_id)
    triggers = node(task, "Triggers")
    timer = node(triggers, "TimeTrigger")
    repetition = node(timer, "Repetition")
    node(repetition, "Interval", "PT5M")
    node(repetition, "StopAtDurationEnd", "false")
    node(timer, "StartBoundary", __import__("datetime").datetime.now().replace(microsecond=0).isoformat())
    node(timer, "Enabled", "true")
    login = node(triggers, "LogonTrigger")
    node(login, "Enabled", "true")
    node(login, "UserId", sid)
    principal = node(node(task, "Principals"), "Principal", id="Author")
    node(principal, "UserId", sid)
    node(principal, "LogonType", "InteractiveToken")
    node(principal, "RunLevel", "LeastPrivilege")
    settings = node(task, "Settings")
    for name, text in (("MultipleInstancesPolicy", "IgnoreNew"), ("DisallowStartIfOnBatteries", "false"),
                       ("StopIfGoingOnBatteries", "false"), ("StartWhenAvailable", "true"),
                       ("Enabled", "true"), ("Hidden", "true"), ("ExecutionTimeLimit", "PT0S")):
        node(settings, name, text)
    action = node(node(task, "Actions", Context="Author"), "Exec")
    node(action, "Command", str(executable))
    node(action, "Arguments", subprocess.list2cmdline([str(launcher), "worker", "run", "--max-seconds", "900"]))
    node(action, "WorkingDirectory", str(repo))
    return ET.tostring(task, encoding="unicode")


def scheduler(repo: Path, action: str) -> dict:
    if os.name != "nt":
        raise ValueError("Windows scheduler integration requires the registered Windows host")
    store = TaskStore(repo)
    primary = worktrees(repo)[0]["path"]
    name = "Tenon-" + store.repo_id
    xml_path = store.root / "operations/worker-task.xml"
    if action == "install":
        install_path = store.root / "bin/installation.json"
        if not install_path.is_file():
            raise ValueError("Install the source-pinned stable entry first")
        installation = json.loads(install_path.read_text(encoding="utf-8"))
        if installation.get("repo_id") != store.repo_id or Path(installation["repo"]).resolve() != primary:
            raise ValueError("Stable entry repository identity differs")
        executable = Path(sys.executable).with_name("pythonw.exe")
        if not executable.is_file():
            raise ValueError("A windowless Python executable is required for background scheduling")
        user = next(csv.reader(io.StringIO(subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True))))
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_text(schedule_xml(primary, store.repo_id, executable, store.root / "bin/tenon.py", user[1]), encoding="utf-8")
    environment = os.environ.copy()
    environment.update(TENON_TASK_NAME=name, TENON_TASK_XML=str(xml_path),
                       TENON_TASK_DESCRIPTION="Tenon owned queue " + store.repo_id)
    script = '''$ErrorActionPreference = 'Stop'
$existing = Get-ScheduledTask -TaskName $env:TENON_TASK_NAME -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $env:TENON_TASK_DESCRIPTION) { throw 'Scheduled task ownership differs' }
'''
    if action == "install":
        script += '''Register-ScheduledTask -TaskName $env:TENON_TASK_NAME -Xml (Get-Content -LiteralPath $env:TENON_TASK_XML -Raw -Encoding UTF8) -Force | Out-Null
Start-ScheduledTask -TaskName $env:TENON_TASK_NAME
'''
    elif action == "remove":
        # This removes only our registered scheduler entry; it never stops a running command.
        script += '''if ($existing) { Unregister-ScheduledTask -TaskName $env:TENON_TASK_NAME -Confirm:$false }
'''
    elif action == "status":
        script += '''if ($existing) { Get-ScheduledTaskInfo -TaskName $env:TENON_TASK_NAME | Select-Object LastRunTime,LastTaskResult,NextRunTime | ConvertTo-Json -Compress } else { Write-Output '{}' }
'''
    else:
        raise ValueError("Unknown scheduler action")
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=True)
    return {"status": "SCHEDULED" if action == "install" else action.upper(), "task_name": name,
            "enabled": configuration(store)["enabled"], "details": completed.stdout.strip()}


def status(repo: Path, *, schedule_probe=None, process_probe=None) -> dict:
    from engineering_health import _json, _primary
    from worker_health import inspect_resident
    store = TaskStore(repo, initialize=False)
    config = configuration(store)
    primary, common = _primary(repo)
    resident = inspect_resident(primary, common / "tenon", store.repo_id,
        _json(store.root / "bin/installation.json"), schedule_probe=schedule_probe, process_probe=process_probe)
    if resident is None:
        return {"configuration": config, "scheduler": scheduler(repo, "status") if os.name == "nt" else None}
    if config["enabled"] is not True:
        resident.update(status="BLOCKED", reason="Automatic worker is disabled for this repository")
    return {"configuration": config, "status": "READY" if resident["status"] == "READY" else "BLOCKED", "background": resident,
            "scheduler": resident.get("scheduler")}


def launch(repo: Path) -> dict:
    store = TaskStore(repo)
    if not configuration(store)["enabled"]:
        return {"status": "READ_ONLY", "reason": "Worker activation awaits a completed acceptance task"}
    primary = worktrees(repo)[0]["path"]
    launcher = store.root / "bin/tenon.py"
    if not launcher.is_file():
        raise ValueError("Install the stable entry before launching a worker")
    log = store.root / "operations/worker.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as output:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen([sys.executable, str(launcher), "worker", "run", "--max-seconds", "900"],
            cwd=primary, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, **options)
    return {"status": "STARTED", "pid": process.pid, "log": str(log)}
