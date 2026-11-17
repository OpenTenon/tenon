#!/usr/bin/env python3
"""Read-only native engineering health; never initialize or repair runtime state."""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
INSPECTION_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError,
                     SyntaxError, subprocess.SubprocessError, ET.ParseError)


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)


def _json(path: Path):
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Runtime record exceeds the bounded inspection size")
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("Runtime record is not an object")
        return value
    except FileNotFoundError:
        return None


def _primary(repo: Path) -> tuple[Path, Path]:
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    try:
        fields = _git(repo, "worktree", "list", "--porcelain", "-z").decode().split("\0")
    except subprocess.CalledProcessError:
        fields = _git(repo, "worktree", "list", "--porcelain").decode().splitlines()
    raw = next((field[9:] for field in fields if field.startswith("worktree ")), None)
    if not raw or raw.startswith('"') or (os.name != "nt" and PureWindowsPath(raw).drive):
        raise ValueError("Cannot resolve a native primary checkout; inspect it on the registered host")
    primary = Path(raw).resolve()
    if not primary.is_dir() or primary == common or primary.is_relative_to(common):
        raise ValueError("The primary checkout is missing or is tooling/Git metadata storage")
    observed = Path(_git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    if observed != common:
        raise ValueError("Primary checkout and queried repository have different Git identities")
    return primary, common


def _origin(raw: str) -> str:
    match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", raw)
    if not match:
        raise ValueError("Origin uses an unsupported identity/auth format; raw URL is intentionally not reported")
    return match[1].casefold()


def _registered_task(base: Path, name: str, repo_id: str) -> dict | None:
    from engineering_metrics import _journal
    _, recovered = _journal(base / "tasks" / (name + ".jsonl"), name, repo_id, [])
    snapshot = _json(base / "tasks" / (name + ".json"))
    candidates = [item for item in (snapshot, recovered) if item and item.get("schema_version") == 2
                  and item.get("repo_id") == repo_id and item.get("task_id") == name and type(item.get("revision")) is int]
    return max(candidates, key=lambda row: row["revision"]) if candidates else None


def _registered_repositories(base: Path, repo_id: str, installation: dict | None) -> set[str]:
    values = set()
    installed = (installation or {}).get("repository")
    if installed is not None:
        if not isinstance(installed, str) or not REPOSITORY.fullmatch(installed):
            raise ValueError("Installed canonical repository identity is malformed")
        values.add(installed.casefold())
    names = {path.stem for path in (base / "tasks").glob("*") if path.suffix in {".json", ".jsonl"}}
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            continue
        item = _registered_task(base, name, repo_id)
        if not item:
            continue
        registered = item.get("repository")
        if registered:
            if not isinstance(registered, str) or not REPOSITORY.fullmatch(registered):
                raise ValueError("A registered task repository identity is malformed")
            values.add(registered.casefold())
        url = item.get("delivery", {}).get("html_url") or item.get("pr_url", "")
        match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/[0-9]+", url or "")
        if match:
            values.add(match[1].casefold())
    return values


def _launcher_template(repo: Path, commit: str) -> str:
    tree = ast.parse(_git(repo, "show", commit + ":scripts/tenon.py").decode("utf-8"))
    install = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install"), None)
    candidates = [node.value.value for node in ast.walk(install) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "launcher" for target in node.targets)
                  and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)] if install else []
    if len(candidates) != 1:
        raise ValueError("Committed installer has no uniquely inspectable launcher template")
    return candidates[0]


def _distributions(executable: str) -> list[str]:
    result = subprocess.run([executable, "--list", "--quiet"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
    if result.returncode:
        raise ValueError("Cannot inspect the registered WSL distributions")
    encoding = "utf-16-le" if b"\0" in result.stdout else "utf-8-sig"
    return [line.strip().lstrip("\ufeff") for line in result.stdout.decode(encoding).splitlines() if line.strip()]


def _schedule_details(xml: str) -> dict:
    tree = ET.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    def text(path, default=None):
        item = tree.find(path, ns)
        return item.text if item is not None else default
    return {"user_sid": text("t:Principals/t:Principal/t:UserId"),
            "logon_type": text("t:Principals/t:Principal/t:LogonType"),
            # Export-ScheduledTask omits schema defaults, including this value.
            "run_level": text("t:Principals/t:Principal/t:RunLevel", "LeastPrivilege"),
            "interval": text("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval"),
            "timer_enabled": text("t:Triggers/t:TimeTrigger/t:Enabled", "true") == "true",
            "logon_enabled": text("t:Triggers/t:LogonTrigger/t:Enabled", "true") == "true",
            "logon_user": text("t:Triggers/t:LogonTrigger/t:UserId"),
            "hidden": text("t:Settings/t:Hidden", "false") == "true",
            "start_when_available": text("t:Settings/t:StartWhenAvailable", "false") == "true",
            "instances": text("t:Settings/t:MultipleInstancesPolicy", "IgnoreNew")}


def _schedule(name: str) -> dict:
    if os.name != "nt":
        return {"status": "UNKNOWN", "reason": "Native Windows scheduler inspection is unavailable on this host"}
    environment = os.environ.copy()
    environment["TENON_HEALTH_TASK_NAME"] = name
    script = """$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$job = Get-ScheduledTask -TaskName $env:TENON_HEALTH_TASK_NAME -ErrorAction SilentlyContinue
if (-not $job) { @{exists=$false} | ConvertTo-Json -Compress; exit }
$info = Get-ScheduledTaskInfo -TaskName $env:TENON_HEALTH_TASK_NAME
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
@{exists=$true;description=$job.Description;enabled=$job.Settings.Enabled;state=[string]$job.State;actions=@($job.Actions | Select-Object Execute,Arguments,WorkingDirectory);xml=(Export-ScheduledTask -TaskName $env:TENON_HEALTH_TASK_NAME);current_sid=$identity.User.Value;current_name=$identity.Name;last_result=$info.LastTaskResult;last_run_utc=$info.LastRunTime.ToUniversalTime().ToString('o');next_run_utc=$info.NextRunTime.ToUniversalTime().ToString('o')} | ConvertTo-Json -Depth 5 -Compress
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=30, check=False)
    if result.returncode:
        raise ValueError("Cannot read the owned Windows scheduled task")
    value = json.loads(result.stdout)
    if value.get("exists"):
        value.update(_schedule_details(value.pop("xml")))
    return value


def full_doctor(repo: Path, *, schedule_probe=None, distribution_probe=None) -> dict:
    checks = []
    report = {"schema_version": 1, "kind": "engineering-health", "mode": "read-only", "checks": checks,
              "network_accessed": False, "qualification": "Local prerequisites and recorded identity; remote availability is not inferred",
              "native_tools": {name: shutil.which(name) for name in ("git", "gh", "make", "wsl")}}
    primary = common = None
    def add(name, status, reason, *, required=True, next_commands=None, **details):
        checks.append({"id": name, "status": status, "required": required, "reason": reason,
                       "next_commands": next_commands or [], **details})
    def finish():
        report["status"] = "READY" if all(item["status"] == "READY" for item in checks if item["required"]) else "BLOCKED"
        return report
    try:
        primary, common = _primary(Path(repo).resolve())
        report.update(primary=str(primary), git_common_dir=str(common))
        add("primary", "READY", "Fixed native primary checkout resolved independently of the tooling directory")
    except INSPECTION_ERRORS as error:
        add("primary", "BLOCKED", str(error), next_commands=[["git", "worktree", "list", "--porcelain"]])
        return finish()
    entry = Path(__file__).with_name("tenon.py")
    install_command = [sys.executable, str(entry), "--repo", str(primary), "install", "--git-alias"]
    base = common / "tenon"
    try:
        identity = _json(base / "repository.json")
        if not identity or identity.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{32}", str(identity.get("repo_id", ""))):
            raise ValueError("Runtime repository identity is missing or unsupported; no state was initialized")
        repo_id = identity["repo_id"]
        add("repository-record", "READY", "Existing runtime identity is readable", repo_id=repo_id)
    except INSPECTION_ERRORS as error:
        add("repository-record", "BLOCKED", str(error), next_commands=[install_command])
        return finish()
    try:
        installation = _json(base / "bin/installation.json")
        if not installation or installation.get("schema_version") != 1 or installation.get("repo_id") != repo_id:
            raise ValueError("Installed entry is missing or belongs to another repository")
        if Path(installation["repo"]).resolve() != primary:
            raise ValueError("Installed fixed primary differs from Git's primary checkout")
        commit = installation["commit"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Installed source revision is invalid")
        controller = Path(installation["controller"]).resolve()
        if controller.parent != (base / "controllers").resolve() or controller.name != commit:
            raise ValueError("Installed controller path escaped its registered storage")
        from task_archive import verify_controller_source
        verify_controller_source(controller, primary, commit)
        launcher = base / "bin/tenon.py"
        if launcher.read_text(encoding="utf-8").replace("\r\n", "\n") != _launcher_template(primary, commit).replace("\r\n", "\n"):
            raise ValueError("Stable launcher differs from its committed installer template")
        alias = installation.get("git_alias")
        if alias and _git(primary, "config", "--local", "--get-all", "alias.tenon").decode().strip() != alias.get("value"):
            raise ValueError("The recorded owned git tenon alias differs")
        add("installed-entry", "READY", "Controller source and launcher template match the installed revision", commit=commit)
    except INSPECTION_ERRORS as error:
        installation = installation if "installation" in locals() else None
        add("installed-entry", "BLOCKED", str(error), next_commands=[install_command])
    try:
        observed = _origin(_git(primary, "remote", "get-url", "origin").decode().strip())
        expected = _registered_repositories(base, repo_id, installation)
        if len(expected) > 1:
            add("origin-identity", "BLOCKED", "Registered canonical origin identities conflict", expected=sorted(expected),
                next_commands=[["git", "-C", str(primary), "remote", "get-url", "origin"], ["git", "-C", str(primary), "tenon", "metrics"]])
        elif not expected:
            add("origin-identity", "UNKNOWN", "No trusted local origin binding; explicit installation records it", next_commands=[install_command])
        elif observed not in expected:
            add("origin-identity", "BLOCKED", "Configured origin differs from the recorded canonical identity", observed=observed, expected=sorted(expected),
                next_commands=[["git", "-C", str(primary), "remote", "set-url", "origin", "git@github.com:" + next(iter(expected)) + ".git"]])
        else:
            add("origin-identity", "READY", "Configured origin matches a recorded identity", repository=observed)
    except INSPECTION_ERRORS as error:
        add("origin-identity", "BLOCKED", str(error), next_commands=[["git", "-C", str(primary), "remote", "get-url", "origin"]])
    add("remote-availability", "UNKNOWN", "No remote request was made", required=False)
    try:
        profile = _json(base / "host.json")
        if not profile or profile.get("schema_version") != 1 or profile.get("repo_id") != repo_id or profile.get("host_platform") != os.name:
            raise ValueError("A matching native host verifier profile is missing")
        from host_profile import validate_inputs
        validate_inputs(profile.get("inputs"))
        argv = profile.get("verify_command")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("Registered verifier is not an argv array")
        executable = shutil.which(argv[0])
        if not executable:
            raise ValueError("Registered verifier executable is unavailable")
        mode = "wsl" if Path(executable).stem.casefold() == "wsl" else "native"
        if mode == "wsl":
            distributions = (distribution_probe or _distributions)(executable)
            selected = next((argv[index + 1] for index, item in enumerate(argv[:-1]) if item in {"-d", "--distribution"}), None)
            if not distributions or (selected is not None and selected not in distributions):
                raise ValueError("The registered WSL distribution is not available")
        add("host-verifier", "READY", "Registered verifier prerequisites and frozen inputs are readable; verifier was not executed", mode=mode, frozen_inputs=len(profile["inputs"]))
    except INSPECTION_ERRORS as error:
        add("host-verifier", "BLOCKED", str(error), next_commands=[["git", "-C", str(primary), "tenon", "host", "--help"]])
    try:
        worker = _json(base / "worker.json")
        if not worker or worker.get("schema_version") != 1 or worker.get("repo_id") != repo_id or worker.get("enabled") is not True:
            raise ValueError("Automatic worker is not enabled for this repository")
        observed = (schedule_probe or _schedule)("Tenon-" + repo_id)
        if observed.get("status") == "UNKNOWN":
            add("scheduler", "UNKNOWN", observed["reason"], next_commands=[["git", "-C", str(primary), "tenon", "worker", "status"]])
        else:
            registered_python = (installation or {}).get("python_executable")
            binding = "installation.python_executable" if registered_python is not None else "current-native-interpreter-fallback"
            if registered_python is not None and (not isinstance(registered_python, str) or not Path(registered_python).is_absolute()):
                raise ValueError("Installed native interpreter path is invalid")
            python = Path(registered_python or sys.executable).resolve()
            expected_program = python.with_name("pythonw.exe")
            expected_args = subprocess.list2cmdline([str(base / "bin/tenon.py"), "worker", "run", "--max-seconds", "900"])
            actions = observed.get("actions", [])
            mismatches = []
            for field, expected in {"exists": True, "enabled": True, "description": "Tenon owned queue " + repo_id,
                    "logon_type": "InteractiveToken", "run_level": "LeastPrivilege", "interval": "PT5M",
                    "instances": "IgnoreNew", "timer_enabled": True, "logon_enabled": True,
                    "hidden": True, "start_when_available": True}.items():
                if observed.get(field) != expected:
                    mismatches.append(field)
            sid = observed.get("current_sid")
            account = observed.get("current_name")
            # Native exports normalize trigger UserId to this local account name.
            # Compare only WindowsIdentity's own aliases; never resolve a domain.
            aliases = {value.casefold() for value in (sid, account) if isinstance(value, str) and value}
            if not sid or any(str(observed.get(field, "")).casefold() not in aliases for field in ("user_sid", "logon_user")):
                mismatches.append("current-user ownership")
            if len(actions) != 1:
                mismatches.append("single entry action")
            else:
                if Path(actions[0].get("WorkingDirectory", "")).resolve() != primary:
                    mismatches.append("action working directory")
                if actions[0].get("Arguments") != expected_args:
                    mismatches.append("action arguments")
                executable = Path(actions[0].get("Execute", ""))
                if not executable.is_absolute() or executable.resolve() != expected_program or not executable.is_file():
                    mismatches.append("registered windowless action executable")
            if mismatches:
                raise ValueError("Owned scheduled task differs: " + ", ".join(mismatches))
            add("scheduler", "READY", "Owned enabled recovery schedule dispatches the stable entry",
                interpreter_binding={"source": binding, "python_executable": str(python), "expected_action_executable": str(expected_program)},
                historical_execution={key: observed.get(key) for key in ("last_result", "last_run_utc", "next_run_utc", "state")},
                execution_qualification="Historical result/state do not prove current worker liveness or successful cleanup")
    except INSPECTION_ERRORS as error:
        add("scheduler", "BLOCKED", str(error), next_commands=[["git", "-C", str(primary), "tenon", "worker", "status"]])
    try:
        from engineering_metrics import collect
        metrics = collect(primary)
        queues = {}
        task_ids = {row["task_id"] for row in metrics["per_task"]}
        for path in (base / "queue").glob("*.json"):
            queued = _json(path)
            if (not queued or queued.get("schema_version") != 1 or queued.get("repo_id") != repo_id
                    or queued.get("task_id") != path.stem or path.stem not in task_ids
                    or queued.get("state") not in {"QUEUED", "WAITING", "WAITING_APP", "BLOCKED", "DONE"}):
                raise ValueError("Queue ownership, version or state differs: " + path.name)
            task = _registered_task(base, path.stem, repo_id)
            if not task or not task.get("owner_id") or queued.get("owner_id") != task["owner_id"]:
                raise ValueError("Queue owner differs from its registered task: " + path.name)
            queues[path.stem] = queued
        pending = []
        blockers = []
        for row in metrics["per_task"]:
            queued = queues.get(row["task_id"], {})
            last = queued.get("last_result") or {}
            reason = queued.get("reason") or last.get("reason") or last.get("status") or row["status"]
            if row["current_failure"]:
                blockers.append({"task_id": row["task_id"], "status": row["status"], "queue_state": row["queue_state"], "reason": reason})
            if not row["delivered"] or row["cleaned_recorded"]:
                continue
            pending.append({"task_id": row["task_id"], "status": row["status"], "queue_state": row["queue_state"],
                "queue_stage": row["queue_stage"], "reason": reason,
                "next_attempt_at": queued.get("next_attempt_at"), "age_seconds": row["pending_cleanup_age_seconds"]})
        contradiction = metrics["resource_health"]["cleaned_but_resources_present"]
        if metrics["diagnostics"] or blockers or contradiction:
            add("cleanup-backlog", "BLOCKED", "Durable task blockers, contradictory resources or incomplete local records require inspection", pending=pending, pending_count=len(pending), blockers=blockers, contradictions=contradiction,
                diagnostics=metrics["diagnostics"], next_commands=[["git", "-C", str(primary), "tenon", "metrics"]])
        else:
            add("cleanup-backlog", "READY", "No durable blocker or contradictory local resources; unfinished deliveries remain unfinished",
                pending=pending, pending_count=len(pending), remote_qualification="Remote absence remains unprobed",
                next_commands=[["git", "-C", str(primary), "tenon", "metrics"]] if pending else [])
        add("worker-observation", "READY" if metrics["worker"].get("lease_held") is True else "UNKNOWN",
            "Observed OS lease and stored heartbeat only; stored PID and historical schedule result are not process-identity proof",
            required=False, observation=metrics["worker"])
    except INSPECTION_ERRORS as error:
        add("cleanup-backlog", "BLOCKED", "Cannot inspect local cleanup state: " + (str(error) if isinstance(error, ValueError) else type(error).__name__),
            next_commands=[["git", "-C", str(primary), "tenon", "metrics"]])
    if not report["native_tools"]["gh"]:
        add("native-delivery-tools", "BLOCKED", "Native GitHub CLI is unavailable; verifier capability is reported separately", next_commands=[["gh", "--version"]])
    else:
        add("native-delivery-tools", "READY", "Native Git and GitHub CLI are available; authentication was not contacted")
    return finish()
