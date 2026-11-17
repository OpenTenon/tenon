#!/usr/bin/env python3
"""Process-level recovery drills confined to persistent nonce-owned Git fixtures."""
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
import contextlib
import ctypes
import json
import os
import queue
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engineering_common import digest, run_id, utc_now, write_json, stop_process

CASES = ("exit-resume", "disconnect-resume", "windows-sharing", "duplicate", "controllers")
CI_QUALIFICATION = "Simulated CI for an owned local fixture; no hosted CI, app restart or network SLA proof"


def _environment() -> dict:
    keys = {"PATH", "PATHEXT", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
            "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMDRIVE", "LANG", "LC_ALL"}
    value = {name: content for name, content in os.environ.items() if name in keys}
    value.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                 PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    return value


def _git(repo: Path, *args: str) -> bytes:
    repo = Path(repo).resolve()
    root = repo.parent
    _boundary(root / "fixture.json", ready=False)
    primary = root / "primary/.git"
    if not os.path.lexists(primary) or _reparse(primary) or not primary.is_dir() or os.path.lexists(primary / "commondir"):
        raise ValueError("Fixture primary has no independent owned Git metadata")
    if repo == root / "primary":
        metadata = primary
    elif repo == root / "task":
        pointer = repo / ".git"
        if not os.path.lexists(pointer) or _reparse(pointer) or not pointer.is_file() or pointer.stat().st_size > 4096:
            raise ValueError("Fixture linked task Git pointer is missing or unowned")
        raw = pointer.read_text(encoding="utf-8").strip()
        if not raw.startswith("gitdir: ") or len(raw.splitlines()) != 1:
            raise ValueError("Fixture linked task Git pointer is invalid")
        metadata = (repo / raw[8:]).resolve()
        if (metadata.parent != primary / "worktrees" or not metadata.is_dir() or _reparse(metadata)
                or _reparse(metadata.parent) or (metadata / "commondir").is_symlink()
                or (metadata / (metadata / "commondir").read_text().strip()).resolve() != primary):
            raise ValueError("Fixture linked task Git pointer escaped its primary metadata")
    else:
        raise ValueError("Fixture Git accepts only its owned primary or linked task")
    environment = _environment()
    environment["GIT_COMMON_DIR"] = str(primary)
    return subprocess.check_output(["git", "--no-optional-locks", "--git-dir", str(metadata),
        "--work-tree", str(repo), "-C", str(repo), *args], cwd=repo,
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, env=environment, timeout=30)


@contextlib.contextmanager
def _fixture_context(root: Path):
    """Keep reused lifecycle Git discovery inside this private nonce directory."""
    primary = root / "primary"
    if Path(_git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve() != primary / ".git":
        raise ValueError("Fixture Git context has no independent primary metadata")
    if os.path.lexists(root / "task"):
        if Path(_git(root / "task", "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve() != primary / ".git":
            raise ValueError("Fixture linked task does not share its private primary metadata")
    variables = {"GIT_CEILING_DIRECTORIES": str(root), "GIT_DIR": None, "GIT_WORK_TREE": None, "GIT_COMMON_DIR": None}
    previous = {name: os.environ.get(name) for name in variables}
    for name, value in variables.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _reparse(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def _record(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Fixture recovery record exceeds its inspection bound")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Fixture recovery record is not an object")
    return value


def _ready_records(root: Path, value: dict):
    from engineering_metrics import _journal
    base = root / "primary/.git/tenon"
    identity = _record(base / "repository.json")
    if identity.get("schema_version") != 1 or identity.get("repo_id") != value.get("repo_id"):
        raise ValueError("Fixture TaskStore identity differs from its manifest")
    diagnostics = []
    _, recovered = _journal(base / "tasks/fixture.jsonl", "fixture", value["repo_id"], diagnostics)
    snapshot = _record(base / "tasks/fixture.json")
    if diagnostics:
        raise ValueError("Fixture task journal is incomplete or inconsistent")
    candidates = [row for row in (snapshot, recovered) if row and row.get("schema_version") == 2
                  and row.get("repo_id") == value["repo_id"] and row.get("task_id") == "fixture"
                  and type(row.get("revision")) is int]
    task = max(candidates, key=lambda row: row["revision"]) if candidates else {}
    delivery = task.get("delivery") or {}
    if (task.get("owner_id") != value["owner_token"] or task.get("branch") != value["branch"]
            or task.get("head_sha") != value["head_sha"] or delivery.get("head_sha") != value["head_sha"]
            or delivery.get("merge_commit_sha") != value["merge_sha"] or delivery.get("merged") is not True
            or delivery.get("number") != 1 or task.get("repository") != _FixtureBackend.name
            or task.get("host_platform") != os.name or task.get("delivery_mode") != "auto"
            or Path(task.get("worktree", "")).resolve() != root / "task"):
        raise ValueError("Fixture task owner, source or resource contract differs from its manifest")
    queued = _record(base / "queue/fixture.json")
    if (queued.get("schema_version") != 1 or queued.get("repo_id") != value["repo_id"]
            or queued.get("task_id") != "fixture" or queued.get("owner_id") != value["owner_token"]
            or queued.get("candidate_sha") != value["head_sha"] or queued.get("stage") != "CLEANUP"):
        raise ValueError("Fixture queue owner or candidate differs from its manifest")


def _scratch(common: Path, *, create=False) -> Path:
    common = Path(common)
    if not common.is_absolute() or common.resolve() != common or not common.is_dir() or _reparse(common):
        raise ValueError("Drill storage requires the canonical existing Git common directory")
    for directory in (common / "tenon", common / "tenon/drill-scratch"):
        if create:
            directory.mkdir(exist_ok=True)
        if not directory.is_dir() or _reparse(directory) or directory.resolve() != directory:
            raise ValueError("Persistent drill scratch is missing or redirected")
    return common / "tenon/drill-scratch"


def _new_fixture(common: Path) -> Path:
    return Path(tempfile.mkdtemp(prefix="tenon-drill-", dir=_scratch(Path(common).resolve(), create=True))).resolve()


def _boundary(path: Path, *, ready=True) -> dict:
    value = _record(path)
    if value.get("schema_version") != 1 or value.get("scenario") not in CASES or (ready and value.get("phase") != "READY"):
        raise ValueError("Private fixture manifest is unsupported or setup is incomplete")
    production = Path(value["production_common"])
    scratch = _scratch(production)
    root = Path(value["root"])
    if (not root.is_absolute() or root.resolve() != root or root.parent != scratch
            or not root.name.startswith("tenon-drill-") or _reparse(root)):
        raise ValueError("Fixture escaped its repository's persistent drill scratch")
    if path.resolve() != root / "fixture.json" or value.get("owner_token") != (root / "owner-token").read_text():
        raise ValueError("Fixture manifest/ownership token differs")
    return value


def _control(path: Path) -> dict:
    value = _boundary(path)
    root, production = Path(value["root"]), Path(value["production_common"])
    primary, remote, target = root / "primary", root / "remote.git", root / "task"
    for item in (primary, remote, target, root / "offline.git"):
        if os.path.lexists(item) and (_reparse(item) or item.resolve().parent != root):
            raise ValueError("Fixture contains an escaped Git resource")
    def reject_walk_error(error):
        raise error
    for directory, names, files in os.walk(root, followlinks=False, onerror=reject_walk_error):
        for name in [*names, *files]:
            item = Path(directory) / name
            try:
                reparse = os.path.lexists(item) and _reparse(item)
            except FileNotFoundError:
                # A peer's atomic JSON replacement can remove this listed item.
                # Root/resource/contract inspection outside this walk stays strict.
                continue
            if reparse:
                raise ValueError("Fixture contains a reparse/symlink; preserve all recovery material")
    common = Path(_git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    if common != primary / ".git" or common == production:
        raise ValueError("Private worker refuses production/shared Git state")
    if os.path.lexists(target) and Path(_git(target, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve() != common:
        raise ValueError("Fixture linked task points outside its own primary Git metadata")
    for direction in ((), ("--push",)):
        urls = _git(primary, "remote", "get-url", *direction, "--all", "origin").decode().splitlines()
        if urls != [str(remote)]:
            raise ValueError("Fixture fetch/push origin is not its unique local bare remote")
    if value.get("phase") == "READY":
        if value.get("branch") != "dev-drill-" + value["scenario"]:
            raise ValueError("Fixture task branch differs from its dedicated scenario")
        _ready_records(root, value)
        records, row = [], {}
        try:
            fields = _git(primary, "worktree", "list", "--porcelain", "-z").decode().split("\0")
        except subprocess.CalledProcessError as error:
            if error.returncode != 129:
                raise
            # The supported Ubuntu 22.04 Git predates NUL worktree output.
            fields = _git(primary, "-c", "core.quotePath=false", "worktree", "list", "--porcelain").decode().splitlines()
        for field in fields:
            if not field:
                if row:
                    records.append(row)
                    row = {}
            else:
                key, _, entry = field.partition(" ")
                if key == "worktree" and entry.startswith('"'):
                    raise ValueError("Fixture worktree path cannot be inspected unambiguously by this Git version")
                row[key] = entry
        if row:
            records.append(row)
        paths = []
        for row in records:
            registered = Path(row.get("worktree", "")).resolve()
            if registered not in (primary, target) or registered in paths:
                raise ValueError("Fixture has an extra or escaped registered worktree")
            expected = value["merge_sha"] if registered == primary else value["head_sha"]
            branch = "refs/heads/dev" if registered == primary else "refs/heads/" + value["branch"]
            if row.get("HEAD") != expected or row.get("branch") != branch:
                raise ValueError("Fixture registered worktree branch or source differs")
            paths.append(registered)
        if primary not in paths:
            raise ValueError("Fixture primary worktree is not registered")
    return value


def _append(root: Path, action: str, **fields):
    with (root / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": utc_now(), "action": action, **fields}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class _FixtureBackend:
    """Constructed only after _control validates a newly owned isolated fixture."""
    name = "drill/isolated"

    def __init__(self, manifest: Path):
        self.manifest = manifest
        self.data = _control(manifest)
        self.root = Path(self.data["root"])
        self.repo = self.root / "primary"

    def _transport(self, *args):
        _control(self.manifest)
        try:
            return _git(self.repo, *args)
        except subprocess.CalledProcessError as error:
            if (self.root / "offline").exists() and not (self.root / "remote.git").exists():
                _append(self.root, "local-transport-unavailable", exit_code=error.returncode)
                raise TimeoutError("Owned fixture transport is temporarily unavailable") from error
            raise

    def refresh(self):
        self._transport("fetch", "--quiet", "origin", "refs/heads/dev:refs/remotes/origin/dev")

    def remote_head(self, branch):
        if branch != self.data["branch"]:
            raise ValueError("Private backend refuses any non-fixture branch")
        value = self._transport("ls-remote", "--heads", "origin", "refs/heads/" + branch).decode().strip()
        return value.split()[0] if value else None

    def delete_remote(self, branch, head):
        if branch != self.data["branch"] or head != self.data["head_sha"]:
            raise ValueError("Fixture deletion identity differs")
        self._transport("push", "--quiet", "--force-with-lease=refs/heads/" + branch + ":" + head,
                        "origin", ":refs/heads/" + branch)
        _append(self.root, "remote-deleted", head=head)

    def pull(self, number):
        if number != 1:
            raise ValueError("Unknown fixture PR")
        fault = self.root / "exit-once"
        if fault.exists():
            fault.unlink()
            _append(self.root, "controller-exit-before-cleanup", pid=os.getpid())
            os._exit(37)  # Only this independently launched fixture controller exits.
        return {"number": 1, "merged": True, "state": "closed", "base": {"ref": "dev"},
                "head": {"sha": self.data["head_sha"]}, "merge_commit_sha": self.data["merge_sha"],
                "merged_at": self.data["started_at"], "html_url": "https://example.invalid/drill/1"}

    def latest_run(self, sha, event, number=None):
        from ci_gate import REQUIRED_JOBS
        expected = self.data["head_sha"] if event == "pull_request" else self.data["merge_sha"]
        if sha != expected:
            return None
        run = 1 if event == "pull_request" else 2
        return {"workflow_run": {"id": run, "run_attempt": 1, "head_sha": sha, "event": event,
                                  "status": "completed", "conclusion": "success", "qualification": CI_QUALIFICATION},
                "jobs": [{"name": name, "run_id": run, "run_attempt": 1, "status": "completed", "conclusion": "success"}
                         for name in REQUIRED_JOBS | {"ci-required"}]}

    def diagnostics(self, *_):
        raise ValueError("Unexpected simulated CI failure")


def _seed(root: Path, scenario: str, production: Path) -> Path:
    from task_store import TaskStore
    from task_queue import submit
    primary, remote, target = root / "primary", root / "remote.git", root / "task"
    if (root.resolve() != root or root.parent != _scratch(production.resolve()) or _reparse(root) or any(root.iterdir())):
        raise ValueError("New fixture must be an empty owned directory in persistent drill scratch")
    token = uuid.uuid4().hex
    (root / "owner-token").write_text(token, encoding="utf-8")
    manifest = root / "fixture.json"
    initial = {"schema_version": 1, "root": str(root.resolve()), "owner_token": token, "scenario": scenario,
               "production_common": str(production.resolve()), "phase": "CREATING", "started_at": utc_now()}
    write_json(manifest, initial)
    primary.mkdir()
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], cwd=root, check=True, env=_environment(),
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    subprocess.run(["git", "--git-dir", str(primary / ".git"), "--work-tree", str(primary),
                    "init", "--quiet", str(primary)], cwd=root, check=True, env=_environment(),
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    observed = Path(_git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    if observed != primary / ".git" or observed == production.resolve():
        raise ValueError("Explicit fixture initialization did not create independent Git metadata")
    _git(primary, "symbolic-ref", "HEAD", "refs/heads/main")
    for name, value in (("user.name", "Tenon isolated drill"), ("user.email", "fixture@example.invalid"),
                        ("core.autocrlf", "false"), ("credential.helper", ""),
                        ("protocol.allow", "never"), ("protocol.file.allow", "always"),
                        ("core.hooksPath", str(root / "no-hooks"))):
        _git(primary, "config", name, value)
    (root / "no-hooks").mkdir()
    (primary / ".gitignore").write_bytes(b"build/\n__pycache__/\n")
    (primary / "input.txt").write_bytes(b"initial fixture\n")
    (primary / "scripts").mkdir()
    for source in Path(__file__).resolve().parent.iterdir():
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, primary / "scripts" / source.name)
    _git(primary, "add", ".")
    _git(primary, "commit", "--quiet", "-m", "Isolated drill source")
    main_sha = _git(primary, "rev-parse", "HEAD").decode().strip()
    _git(primary, "branch", "dev")
    _git(primary, "remote", "add", "origin", str(remote))
    _git(primary, "push", "--quiet", "origin", "main", "dev")
    _git(primary, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    branch = "dev-drill-" + scenario
    _git(primary, "worktree", "add", "--quiet", "-b", branch, str(target), "dev")
    (target / "input.txt").write_bytes(b"isolated delivered result\n")
    _git(target, "add", "input.txt")
    _git(target, "commit", "--quiet", "-m", "Isolated drill delivery")
    head = _git(target, "rev-parse", "HEAD").decode().strip()
    _git(primary, "push", "--quiet", "origin", head + ":refs/heads/" + branch)
    _git(primary, "checkout", "--quiet", "dev")
    _git(primary, "merge", "--no-ff", "--quiet", "-m", "Isolated local integration", branch)
    merge = _git(primary, "rev-parse", "HEAD").decode().strip()
    _git(primary, "push", "--quiet", "origin", "dev")
    _git(primary, "fetch", "--quiet", "origin", "dev:refs/remotes/origin/dev")
    data = {**initial, "branch": branch, "head_sha": head, "main_sha": main_sha,
            "merge_sha": merge, "qualification": CI_QUALIFICATION}
    with _fixture_context(root):
        store = TaskStore(primary)
        store.create({"task_id": "fixture", "worktree": str(target), "branch": branch, "repository": _FixtureBackend.name,
            "kind": "general", "host_platform": os.name, "owner_id": token, "delivery_mode": "auto",
            "status": "MERGED", "head_sha": head, "base_sha": merge, "started_at": data["started_at"],
            "allowed_paths": ["input.txt"], "checks": ["make verify"],
            "delivery": {"number": 1, "merged": True, "head_sha": head, "merge_commit_sha": merge,
                         "merged_at": data["started_at"], "html_url": "https://example.invalid/drill/1"}})
        submit(primary, "fixture")
    data.update(phase="READY", repo_id=store.repo_id)
    write_json(manifest, data)
    return manifest


def _snapshot(manifest: Path) -> dict:
    data = _control(manifest)
    with _fixture_context(Path(data["root"])):
        return _snapshot_owned(manifest, data)


def _snapshot_owned(manifest: Path, data: dict) -> dict:
    from task_store import TaskStore
    from task_queue import read_queue
    from task_archive import worktrees, verify_task_archive
    root = Path(data["root"])
    store = TaskStore(root / "primary")
    task = store.load("fixture")
    remote = root / "remote.git" if (root / "remote.git").exists() else root / "offline.git"
    refs = _git(root / "primary", "for-each-ref", "--format=%(refname)", "refs/heads/" + data["branch"], "refs/remotes/origin/" + data["branch"]).decode().splitlines()
    remote_refs = dict(line.split(" ", 1) for line in subprocess.check_output(["git", "--git-dir", str(remote), "-C", str(root),
        "for-each-ref", "--format=%(refname) %(objectname)"], cwd=root, env=_environment(), timeout=20).decode().splitlines())
    result = {"task_status": task["status"], "queue_state": read_queue(store, "fixture")["state"],
              "directory": (root / "task").exists(), "registration": any(item["path"] == root / "task" for item in worktrees(root / "primary")),
              "task_refs": refs, "remote_task_ref": "refs/heads/" + data["branch"] in remote_refs,
              "dev_remote_present": remote_refs.get("refs/heads/dev") == data["merge_sha"],
              "main_remote_present": remote_refs.get("refs/heads/main") == data["main_sha"]}
    if task.get("archive"):
        result["archive_sha256"] = task["archive"]["sha256"]
        result["archive_files"] = len(verify_task_archive(store, task)["files"])
    return result


def _closed(value: dict):
    if (value["task_status"] != "CLEANED" or value["queue_state"] != "DONE" or value["directory"]
            or value["registration"] or value["task_refs"] or value["remote_task_ref"]
            or not value["dev_remote_present"] or not value["main_remote_present"] or not value.get("archive_sha256")):
        raise AssertionError("Real fixture Git/resource closure is incomplete")


def _line(process, timeout=20):
    output = queue.Queue()
    def read():
        output.put(process.stdout.readline())
    threading.Thread(target=read, daemon=True).start()
    try:
        value = output.get(timeout=timeout)
    except queue.Empty as error:
        raise TimeoutError("Fixture process handshake did not arrive") from error
    if value.rstrip(b"\r\n") != b"READY":
        raise ValueError("Fixture process failed its readiness handshake")


def _spawn(manifest: Path, action: str, tick=0):
    _control(manifest)
    from controller_leases import entry_command
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(entry_command(Path(__file__).resolve(), "--fixture-worker", str(manifest), "--tick", str(tick), action),
        cwd=Path(manifest).parent / "primary", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_environment(), **options)
    process.drill_manifest = manifest
    process.drill_label = str(process.pid) + "-" + str(tick) + "-" + action
    return process


def _finish(process, *, timeout=60, expected=0):
    try:
        output, error = process.communicate(timeout=timeout)
    except BaseException:
        if process.poll() is None:
            stop_process(process)
        raise
    manifest = getattr(process, "drill_manifest", None)
    if manifest is not None:
        _control(manifest)
        destination = manifest.parent / "process-output"
        destination.mkdir(exist_ok=True)
        (destination / (process.drill_label + ".stdout.log")).write_bytes(output)
        (destination / (process.drill_label + ".stderr.log")).write_bytes(error)
        write_json(destination / (process.drill_label + ".json"), {"pid": process.pid, "exit_code": process.returncode, "expected_exit": expected})
    if process.returncode != expected:
        raise ValueError("Owned fixture process exit differed: " + str(process.returncode) + "; " + (output + error).decode("utf-8", errors="replace")[-1500:])
    return output.decode("utf-8", errors="replace")


def _run_child(manifest: Path, action="resume", *, expected=0, tick=0):
    process = _spawn(manifest, action, tick)
    try:
        _line(process)
        process.stdin.write(b"GO\n")
        process.stdin.flush()
        return _finish(process, expected=expected)
    finally:
        if process.poll() is None:
            stop_process(process)


def _sharing_holder(manifest: Path):
    if os.name != "nt":
        raise ValueError("Native Windows sharing is required")
    data = _control(manifest)
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(Path(data["root"]) / "task"), 0x80000000, 3, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        print("READY", flush=True)
        if sys.stdin.buffer.readline() != b"GO\n":
            raise ValueError("Sharing holder requires its parent release handshake")
    finally:
        kernel.CloseHandle(handle)


def fixture_worker(manifest: Path, action: str, tick: int = 0) -> int:
    data = _control(manifest)
    if data.get("phase") != "READY" or not 0 <= tick <= 1000:
        raise ValueError("Fixture setup/tick is not ready for recovery")
    write_json(Path(data["root"]) / "processes" / (str(os.getpid()) + "-" + str(tick) + "-" + action + ".json"),
               {"pid": os.getpid(), "action": action, "tick": tick, "started_at": utc_now()})
    if action == "hold-sharing":
        _sharing_holder(manifest)
        return 0
    if action not in {"resume", "exit"}:
        raise ValueError("Unknown private fixture action")
    print("READY", flush=True)
    if sys.stdin.buffer.readline() != b"GO\n":
        raise ValueError("Private controller requires its parent handshake")
    root = Path(data["root"])
    if action == "exit":
        (root / "exit-once").write_bytes(b"owned controller only")
    from task_lifecycle import Lifecycle
    from task_queue import reconcile
    logical_time = datetime.fromisoformat(data["started_at"]) + timedelta(minutes=10 * (tick + 1))
    with _fixture_context(root):
        result = reconcile(root / "primary", apply=True,
            factory=lambda repo, task_id: Lifecycle(repo, task_id, _FixtureBackend(manifest)), now=logical_time)
    print(json.dumps(result), flush=True)
    return 0


def _cleanup(manifest: Path):
    data = _control(manifest)
    _closed(_snapshot(manifest))
    root = Path(data["root"])
    for parent, directories, files in os.walk(root, followlinks=False):
        if any(_reparse(Path(parent) / name) for name in [*directories, *files]):
            raise ValueError("Fixture contains an unknown reparse/symlink; preserve it")
    def retry_readonly(function, supplied, error):
        path = Path(supplied)
        if not path.resolve().is_relative_to(root) or _reparse(path) or not path.is_file():
            raise error[1]
        path.chmod(path.stat().st_mode | stat.S_IWRITE)
        function(path)
    # Keep ownership/recovery markers until every disposable subtree is gone.
    for path in list(root.iterdir()):
        if path.name in {"fixture.json", "owner-token"}:
            continue
        if path.is_dir():
            if path.resolve().parent != root:
                raise ValueError("Fixture subtree escaped the verified temporary root")
            shutil.rmtree(path, onerror=retry_readonly)
        else:
            path.unlink()
    (root / "fixture.json").unlink()
    (root / "owner-token").unlink()
    root.rmdir()
    if root.exists():
        raise ValueError("Owned fixture removal is incomplete")


def _scenario(manifest: Path, scenario: str) -> list[dict]:
    observations = []
    root = manifest.parent
    if scenario == "exit-resume":
        _run_child(manifest, "exit", expected=37)
        value = _snapshot(manifest)
        if value["task_status"] == "CLEANED" or not value["directory"] or not value["remote_task_ref"]:
            raise AssertionError("Exit must retain the unfinished resources")
        from task_store import lease_is_held
        if lease_is_held(root / "primary/.git/tenon/locks/integration.lock"):
            raise AssertionError("Exited controller retained an OS lease")
        observations.append(value)
    elif scenario == "disconnect-resume":
        source, target = root / "remote.git", root / "offline.git"
        if source.resolve().parent != root or target.resolve().parent != root or _reparse(source):
            raise ValueError("Local transport move escaped its owned fixture")
        (root / "offline").write_bytes(b"fixture transport only")
        source.rename(target)
        _run_child(manifest, tick=0)
        value = _snapshot(manifest)
        from task_queue import read_queue
        from task_store import TaskStore
        state = read_queue(TaskStore(root / "primary"), "fixture")
        if state.get("error_category") != "TRANSIENT" or state["state"] != "WAITING" or not value["directory"]:
            raise AssertionError("Disconnected local transport was not recoverably retained")
        observations.append(value)
        if (target.resolve().parent != root or source.resolve().parent != root or _reparse(target) or source.exists()):
            raise ValueError("Restoring the local transport escaped its owned fixture")
        target.rename(source)
        (root / "offline").unlink()
    elif scenario == "windows-sharing":
        holder = _spawn(manifest, "hold-sharing")
        try:
            _line(holder)
            _run_child(manifest, tick=0)
            value = _snapshot(manifest)
            from task_queue import read_queue
            from task_store import TaskStore
            state = read_queue(TaskStore(root / "primary"), "fixture")
            if state.get("error_category") != "RESOURCE" or state["state"] != "WAITING" or not value["directory"]:
                raise AssertionError("Real Windows sharing did not preserve the unfinished directory")
            observations.append(value)
        finally:
            if holder.poll() is None:
                holder.stdin.write(b"GO\n")
                holder.stdin.flush()
            _finish(holder)
    if scenario == "controllers":
        processes = [_spawn(manifest, "resume", 1) for _ in range(2)]
        try:
            for process in processes:
                _line(process)
            for process in processes:
                process.stdin.write(b"GO\n")
                process.stdin.flush()
            for process in processes:
                _finish(process)
        finally:
            for process in processes:
                if process.poll() is None:
                    stop_process(process)
    else:
        _run_child(manifest, tick=1)
    value = _snapshot(manifest)
    _closed(value)
    observations.append(value)
    if scenario == "duplicate":
        before = value
        _run_child(manifest, tick=2)
        after = _snapshot(manifest)
        if after != before:
            raise AssertionError("Repeated completed reconciliation changed actual resources/archive")
        observations.append(after)
    return observations


def run_drills(repo: Path, *, cases=None) -> dict:
    selected = list(CASES if cases is None else cases)
    if not selected or len(set(selected)) != len(selected) or any(name not in CASES for name in selected):
        raise ValueError("Unknown or duplicate drill scenario")
    common = Path(subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), "rev-parse",
        "--path-format=absolute", "--git-common-dir"], cwd=repo, env=_environment(), stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE, timeout=30).decode().strip()).resolve()
    _scratch(common, create=True)
    output = common / "tenon/operations/drills" / run_id()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    report = {"schema_version": 1, "kind": "engineering-recovery-drill", "started_at": utc_now(),
              "production_common": str(common), "report": str(output / "summary.json"),
              "qualification": CI_QUALIFICATION, "retry_clock": "explicit logical future tick; not a measured five-minute SLA", "scenarios": rows}
    for scenario in selected:
        started = time.monotonic()
        if scenario == "windows-sharing" and os.name != "nt":
            rows.append({"scenario": scenario, "status": "NOT_RUN", "reason": "A native Windows sharing handle is required", "fixture_created": False})
            continue
        root = _new_fixture(common)
        row = {"scenario": scenario, "fixture": str(root), "status": "FAIL", "fixture_removed": False}
        rows.append(row)
        try:
            manifest = _seed(root, scenario, common)
            row["observations"] = _scenario(manifest, scenario)
            from task_store import TaskStore
            from task_archive import verify_archive, verify_task_archive
            from maintenance import restore_check
            with _fixture_context(root):
                store = TaskStore(root / "primary")
                row["restore_check"] = restore_check(root / "primary", "fixture")
                if row["restore_check"].get("status") != "PASS":
                    raise ValueError("Fixture archive did not pass its real private restore exercise")
                task = store.load("fixture")
                verify_task_archive(store, task)
            copy = output / scenario / "fixture-archive.zip"
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(task["archive"]["path"], copy)
            retained = verify_archive(copy, task["archive"]["sha256"])
            row["archive_copy"] = {"path": str(copy), "sha256": task["archive"]["sha256"],
                "files": len(retained["files"]), "verified_before_fixture_removal": True,
                "verified_after_fixture_removal": False, "qualification": CI_QUALIFICATION}
            if row["restore_check"]["restored_files"] != len(retained["files"]):
                raise ValueError("Fixture restore/copy inventories differ")
            for source, name in ((manifest, "fixture.json"), (root / "events.jsonl", "events.jsonl"),
                                 (store.path("fixture"), "task.json"), (store.path("fixture").with_suffix(".jsonl"), "task.jsonl"),
                                 (store.root / "queue/fixture.json", "queue.json")):
                if source.exists():
                    destination = output / scenario / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            for source in (root / "processes").glob("*.json"):
                destination = output / scenario / "processes" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            for source in (root / "process-output").glob("*"):
                destination = output / scenario / "process-output" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            _cleanup(manifest)
            retained = verify_archive(copy, row["archive_copy"]["sha256"])
            if len(retained["files"]) != row["archive_copy"]["files"]:
                raise ValueError("Retained fixture archive inventory changed after fixture removal")
            row["archive_copy"]["verified_after_fixture_removal"] = True
            row.update(status="PASS", fixture_removed=True)
        except Exception as error:
            row.update(error_type=type(error).__name__, reason=str(error), fixture_preserved=root.exists(),
                       recovery_manifest=str(root / "fixture.json"))
        row["duration_seconds"] = round(time.monotonic() - started, 3)
        write_json(output / "summary.json", report)
    statuses = {row["status"] for row in rows}
    report.update(status="FAIL" if "FAIL" in statuses else "NOT_RUN" if "NOT_RUN" in statuses else "PASS", completed_at=utc_now())
    write_json(output / "summary.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--tick", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("action", nargs="?", choices=("resume", "exit", "hold-sharing"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.fixture_worker is None or args.action is None:
        parser.error("Use git tenon maintenance drill; this script only hosts guarded private fixture workers")
    try:
        return fixture_worker(args.fixture_worker.resolve(), args.action, args.tick)
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__, "reason": str(error)}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
