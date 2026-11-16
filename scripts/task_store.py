#!/usr/bin/env python3
"""Repository-shared v2 task state, atomic journals and owned resource leases."""
from __future__ import annotations
import copy
import ctypes
import json
import os
import re
import socket
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from engineering_common import git, utc_now, write_json

SUCCESS = {"INTEGRATED", "MERGED", "DEV_GREEN", "ARCHIVED", "CLEANING", "CLEANED"}


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Invalid task identifier")
    return value


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return kernel.GetLastError() != 87
        code = ctypes.c_ulong()
        try:
            return not kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class FileLease:
    """OS-owned byte/advisory lock; process exit releases it without lock stealing."""
    def __init__(self, path: Path, kind: str = "state-write", timeout: float = 10):
        self.path, self.kind, self.timeout = path, kind, timeout
        self.token = uuid.uuid4().hex

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.touch(exist_ok=True)
        self.handle = self.path.open("r+b")
        if self.path.stat().st_size==0:
            self.handle.write(b"\n");self.handle.flush()
        started = time.monotonic()
        while True:
            try:
                self.handle.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()-started>=self.timeout:
                    self.handle.close()
                    raise TimeoutError("Task lock is busy: "+str(self.path))
                time.sleep(0.025)
        value = {"token":self.token,"pid":os.getpid(),"host":socket.gethostname(),
                 "platform":os.name,"kind":self.kind,"created_at":utc_now()}
        self.handle.seek(0)
        self.handle.write(b"\n"+json.dumps(value).encode("utf-8"))
        self.handle.truncate();self.handle.flush();os.fsync(self.handle.fileno())
        return self

    def __exit__(self, *_):
        self.handle.seek(0)
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN)
        self.handle.close()


class TaskStore:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        raw = git(self.repo,"rev-parse","--path-format=absolute","--git-common-dir").decode().strip()
        self.common = Path(raw).resolve()
        self.root = self.common / "tenon"
        identity = self.root / "repository.json"
        if not identity.exists():
            self.root.mkdir(parents=True,exist_ok=True)
            with FileLease(self.root / "locks/repository.lock"):
                if not identity.exists():
                    write_json(identity,{"schema_version":1,"repo_id":uuid.uuid4().hex,"created_at":utc_now()})
        self.repo_id = json.loads(identity.read_text(encoding="utf-8"))["repo_id"]

    def path(self, task_id: str) -> Path:
        return self.root / "tasks" / (identifier(task_id) + ".json")

    def _load(self, task_id: str) -> dict:
        path = self.path(task_id)
        journal = path.with_suffix(".jsonl")
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() and not journal.exists() else None
        if journal.exists():
            for line in journal.read_text(encoding="utf-8").splitlines(keepends=True):
                if not line.endswith("\n"):
                    break
                event = json.loads(line)
                if value is None or event["revision"] > value.get("revision",0):
                    value = event["state"]
        if value is None:
            raise FileNotFoundError(path)
        if value.get("repo_id") != self.repo_id or value.get("schema_version") != 2:
            raise ValueError("Task belongs to another repository or unsupported schema")
        return value

    def inbox(self, task_id: str) -> list[Path]:
        return sorted((self.root/"inbox"/identifier(task_id)).glob("*.json"))

    def fold_inbox(self, value: dict, files: list[Path]) -> dict:
        for path in files:
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            if event.get("repo_id")!=self.repo_id or event.get("task_id")!=value["task_id"]:
                raise ValueError("Foreign task inbox event")
            attempt = event["attempt"]
            attempts = value.setdefault("verification_attempts",[])
            if not any(item["run_id"]==attempt["run_id"] for item in attempts):
                attempts.append(attempt)
        return value

    def enqueue_attempt(self, task_id: str, attempt: dict):
        path = self.root/"inbox"/identifier(task_id)/(uuid.uuid4().hex+".json")
        write_json(path,{"repo_id":self.repo_id,"task_id":task_id,"attempt":attempt})

    def load(self, task_id: str) -> dict:
        return self.fold_inbox(copy.deepcopy(self._load(task_id)),self.inbox(task_id))

    def mutate(self, task_id: str, change, action: str = "update", create: bool = False) -> dict:
        path = self.path(task_id)
        with FileLease(self.root / "locks" / (identifier(task_id)+".lock")):
            try:
                value = self._load(task_id)
                if create:
                    raise FileExistsError(path)
            except FileNotFoundError:
                if not create:
                    raise
                value = {"schema_version":2,"repo_id":self.repo_id,"task_id":task_id,"revision":0}
            pending = self.inbox(task_id)
            candidate = self.fold_inbox(copy.deepcopy(value),pending)
            result = change(candidate)
            value = result if result is not None else candidate
            value.update(schema_version=2,repo_id=self.repo_id,task_id=task_id,
                         revision=value.get("revision",0)+1,updated_at=utc_now())
            path.parent.mkdir(parents=True,exist_ok=True)
            event = {"revision":value["revision"],"at":utc_now(),"action":action,"state":value}
            journal = path.with_suffix(".jsonl")
            if journal.exists():
                raw = journal.read_bytes()
                if raw and not raw.endswith(b"\n"):
                    with journal.open("r+b") as output:
                        output.truncate(raw.rfind(b"\n")+1)
            with journal.open("a",encoding="utf-8") as output:
                output.write(json.dumps(event,ensure_ascii=False)+"\n");output.flush();os.fsync(output.fileno())
            write_json(path,value)
            for item in pending:
                item.unlink(missing_ok=True)
            return copy.deepcopy(value)

    def _create(self, value: dict) -> dict:
        try:
            url = git(self.repo,"remote","get-url","origin").decode().strip()
            match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?",url)
            value.setdefault("repository",match[1] if match else None)
            old_url = value.get("delivery",{}).get("html_url","")
            old_match = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/",old_url)
            if old_match and (not value.get("repository") or old_match[1].lower()!=value["repository"].lower()):
                raise ValueError("Legacy PR belongs to another origin")
        except ValueError:
            raise
        except Exception:
            value.setdefault("repository",None)
        pins = Path(value["worktree"])/"tools/toolchain.json"
        if pins.is_file():
            import hashlib
            value.setdefault("toolchain_key",hashlib.sha256(pins.read_bytes()).hexdigest())
        return self.mutate(value["task_id"],lambda current:{**current,**value},"start",True)

    def create(self, value: dict) -> dict:
        with FileLease(self.root/"locks/registration.lock"):
            if value.get("branch"):
                for existing in self.all():
                    if existing.get("status")!="CLEANED" and (existing.get("branch")==value["branch"] or Path(existing["worktree"]).resolve()==Path(value["worktree"]).resolve()):
                        if existing["task_id"]==value["task_id"]:
                            raise FileExistsError(self.path(value["task_id"]))
                        raise ValueError("Worktree/branch already belongs to another task")
            return self._create(value)

    def all(self) -> list[dict]:
        values = []
        names = sorted({path.stem for path in (self.root/"tasks").glob("*") if path.suffix in {".json",".jsonl"}})
        for name in names:
            try:
                values.append(self.load(name))
            except FileNotFoundError:
                pass
        return values

    def migrate(self, worktree: Path, managed_identity: str | None = None) -> list[dict]:
        worktree = worktree.resolve()
        other = Path(git(worktree,"rev-parse","--path-format=absolute","--git-common-dir").decode().strip()).resolve()
        if other != self.common:
            raise ValueError("Legacy worktree belongs to another repository")
        values = []
        for path in (worktree/"build/tasks").glob("*.json"):
            old = json.loads(path.read_text(encoding="utf-8"))
            task_id = identifier(old["task_id"])
            if self.path(task_id).exists():
                values.append(self.load(task_id));continue
            value = copy.deepcopy(old)
            value.update(legacy=copy.deepcopy(old),schema_version=2,repo_id=self.repo_id,
                         worktree=str(worktree),host_platform=os.name,managed_identity=managed_identity,
                         owner_id=uuid.uuid4().hex,legacy_completed_at=old.get("completed_at"))
            if old.get("status") == "INTEGRATED":
                value["status"] = "CLEANUP_PENDING"
                value["delivered_at"] = old.get("delivery",{}).get("merged_at")
            value.pop("completed_at",None)
            values.append(self.create(value))
        return values

    def for_worktree(self, worktree: Path) -> dict | None:
        branch = git(worktree,"branch","--show-current").decode().strip()
        candidates = [item for item in self.all() if item.get("branch")==branch and item.get("status")!="CLEANED"]
        if len(candidates)>1:
            raise ValueError("Multiple task owners claim this branch")
        return candidates[0] if candidates else None


@contextmanager
def resource_lease(repo: Path, kind: str):
    store = TaskStore(repo)
    task = store.for_worktree(repo)
    if not task:
        yield None;return
    path = store.root / "leases" / (task["task_id"]+"-"+uuid.uuid4().hex+".json")
    write_json(path,{"task_id":task["task_id"],"kind":kind,"host":socket.gethostname(),
                     "platform":os.name,"pid":os.getpid(),"created_at":utc_now()})
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def activity_blockers(store: TaskStore, task_id: str) -> list[str]:
    blockers = []
    for path in (store.root/"leases").glob(identifier(task_id)+"-*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        if value.get("host")==socket.gethostname() and value.get("platform")==os.name and not process_alive(int(value["pid"])):
            # A dead command lease is not silently treated as successful execution.
            blockers.append("Interrupted resource requires resume/inspection: "+path.name)
        else:
            blockers.append("Active resource: "+path.name)
    return blockers
