#!/usr/bin/env python3
"""Read-only observation of an existing current-user resident worker."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

HEARTBEAT_MAX_AGE = 180


def _argv(command: str) -> list[str]:
    """Use the same Windows argument rules as the actual process launcher."""
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    arguments = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(arguments)


def _native_probe(repo_id: str, pid: int, child_pid: int | None) -> dict:
    if os.name != "nt":
        return {"status": "UNKNOWN", "reason": "Inspect the resident worker on its registered Windows host"}
    environment = os.environ.copy()
    environment.update(TENON_RESIDENT_ID=repo_id, TENON_RESIDENT_PID=str(pid),
                       TENON_RESIDENT_CHILD=str(child_pid or 0))
    script = """$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
function Read-Process($number) {
    if ($number -le 0) { return $null }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $number"
    if (-not $process) { return $null }
    $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid
    if ($owner.ReturnValue -ne 0) { throw 'Resident process owner is unavailable' }
    @{pid=[int]$process.ProcessId;parent_pid=[int]$process.ParentProcessId;
      executable=$process.ExecutablePath;command_line=$process.CommandLine;owner_sid=$owner.Sid;
      started_at=$process.CreationDate.ToUniversalTime().ToString('o')}
}
$link = Join-Path ([Environment]::GetFolderPath('Startup')) ('Tenon Resident Worker (' + $env:TENON_RESIDENT_ID.Substring(0,8) + ').lnk')
$startup = @{exists=$false;path=$link}
if (Test-Path -LiteralPath $link -PathType Leaf) {
    $shell = New-Object -ComObject WScript.Shell
    $entry = $shell.CreateShortcut($link)
    $startup = @{exists=$true;path=$link;executable=$entry.TargetPath;arguments=$entry.Arguments;
                 working_directory=$entry.WorkingDirectory;description=$entry.Description}
}
@{status='OBSERVED';current_sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
  startup=$startup;process=(Read-Process ([int]$env:TENON_RESIDENT_PID));
  child=(Read-Process ([int]$env:TENON_RESIDENT_CHILD))} | ConvertTo-Json -Depth 5 -Compress
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-Command", script], env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=30, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError("Cannot inspect the current-user resident process and startup shortcut")
    value = json.loads(result.stdout)
    startup = value.get("startup", {})
    if startup.get("exists"):
        startup["argv"] = _argv('"resident" ' + startup.pop("arguments"))[1:]
    for name in ("process", "child"):
        process = value.get(name)
        if process:
            process["argv"] = _argv(process.pop("command_line") or "")
    return value


def _same_path(value, expected: Path) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).is_absolute() and Path(value).resolve() == expected.resolve()


def _timestamp(value) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Resident timestamps must include a timezone")
    return parsed


def _process_identity(process, pid, executable, script, arguments, sid, heartbeat_at):
    if not isinstance(process, dict) or process.get("pid") != pid:
        raise ValueError("The recorded resident or delivery process is absent")
    if not sid or process.get("owner_sid") != sid:
        raise ValueError("Resident process belongs to a different Windows user")
    argv = process.get("argv", [])
    if (not _same_path(process.get("executable"), executable) or len(argv) != 3 + len(arguments)
            or not _same_path(argv[0], executable) or argv[1] != "-B"
            or not _same_path(argv[2], script) or argv[3:] != arguments):
        raise ValueError("Resident process executable or command differs from its registered entry")
    if _timestamp(process["started_at"]) > heartbeat_at:
        raise ValueError("Resident PID was reused after the recorded heartbeat")


def inspect_resident(primary: Path, base: Path, repo_id: str, installation: dict | None, *,
                     schedule_probe=None, process_probe=None, now=None) -> dict | None:
    from engineering_health import INSPECTION_ERRORS, _json, _schedule
    script = base / "host/resident_worker.py"
    heartbeat_path = base / "operations/resident-worker.json"
    if not script.exists() and not heartbeat_path.exists():
        return None
    value = {"mode": "resident", "status": "BLOCKED", "read_only": True}
    try:
        if not script.is_file():
            raise ValueError("The registered resident script is missing")
        if not installation or installation.get("repo_id") != repo_id or not _same_path(installation.get("repo"), primary):
            raise ValueError("Resident installation differs from this primary repository")
        python = Path(installation["python_executable"])
        if not python.is_absolute() or not python.is_file():
            raise ValueError("The registered native interpreter is unavailable")
        pythonw = python.with_name("pythonw.exe")
        if not pythonw.is_file():
            raise ValueError("The registered windowless interpreter is unavailable")
        heartbeat = _json(heartbeat_path)
        if (not heartbeat or heartbeat.get("schema_version") != 1 or heartbeat.get("repo_id") != repo_id
                or type(heartbeat.get("pid")) is not int or heartbeat["pid"] <= 0):
            raise ValueError("Resident heartbeat identity or version differs")
        state = heartbeat.get("state")
        if state not in {"IDLE", "WORKING", "RETRY", "DISABLED"}:
            raise ValueError("Resident heartbeat state is unsupported")
        clock = now or datetime.now(timezone.utc)
        at = _timestamp(heartbeat["at"])
        age = (clock - at).total_seconds()
        if age < -5:
            raise ValueError("Resident heartbeat is in the future")
        child_pid = heartbeat.get("child_pid") if state == "WORKING" else None
        if state == "WORKING" and (type(child_pid) is not int or child_pid <= 0):
            raise ValueError("Working resident has no valid delivery child identity")
        value["heartbeat"] = {key: heartbeat[key] for key in ("pid", "state", "at")}
        value["heartbeat"].update(age_seconds=age, max_idle_age_seconds=HEARTBEAT_MAX_AGE)
        observed = (process_probe or _native_probe)(repo_id, heartbeat["pid"], child_pid)
        if observed.get("status") == "UNKNOWN":
            value.update(status="UNKNOWN", reason=observed["reason"])
            return value
        sid = observed.get("current_sid")
        _process_identity(observed.get("process"), heartbeat["pid"], pythonw, script, [], sid, at)
        startup = observed.get("startup", {})
        argv = startup.get("argv", [])
        if (startup.get("exists") is not True or not _same_path(startup.get("executable"), pythonw)
                or len(argv) != 2 or argv[0] != "-B" or not _same_path(argv[1], script)
                or not _same_path(startup.get("working_directory"), primary)
                or startup.get("description") != "Tenon owned resident queue " + repo_id):
            raise ValueError("Current-user resident login shortcut is missing or differs")
        value["startup"] = {"path": startup.get("path"), "registered": True}
        value["process"] = {"pid": heartbeat["pid"], "identity_verified": True,
                            "started_at": observed["process"]["started_at"]}
        if state == "WORKING":
            child = observed.get("child")
            _process_identity(child, child_pid, python, base / "bin/tenon.py",
                              ["worker", "run", "--max-seconds", "900"], sid, at)
            if child.get("parent_pid") != heartbeat["pid"]:
                raise ValueError("Delivery child belongs to a different resident process")
            value["process"]["child_pid"] = child_pid
            value["heartbeat"]["qualification"] = "Live owned child explains the in-flight heartbeat"
        elif age > HEARTBEAT_MAX_AGE:
            raise ValueError("Resident heartbeat is stale")
        if state in {"RETRY", "DISABLED"}:
            raise ValueError("Resident requires attention: " + state)
        schedule = (schedule_probe or _schedule)("Tenon-" + repo_id)
        value["scheduler"] = schedule
        if schedule.get("status") == "UNKNOWN":
            value.update(status="UNKNOWN", reason=schedule["reason"])
            return value
        if schedule.get("exists") is not False:
            if schedule.get("description") != "Tenon owned queue " + repo_id:
                raise ValueError("Legacy scheduled task ownership differs")
            if schedule.get("enabled") is not False:
                raise ValueError("Legacy scheduled task must remain disabled for the resident worker")
        value.update(status="READY", reason="Current-user resident identity, login entry and heartbeat are healthy; legacy schedule is disabled or absent")
    except INSPECTION_ERRORS as error:
        value["reason"] = str(error)
    return value

