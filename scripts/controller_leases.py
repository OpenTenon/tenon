#!/usr/bin/env python3
"""ABI 1 controller admission and immutable, native read-only execution leases.

The bootstrap loads this stdlib-only module from the selected Git object. No core
module, task record or command is loaded until the actual execution PID is leased.
"""
from __future__ import annotations

import ast
import atexit
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import socket
import stat
import subprocess
import sys
import threading
import time
import tempfile
import uuid

CONTROLLER_LEASE_ABI = 1
ENTRIES = ("tenon.py", "task_lifecycle.py", "maintenance.py", "engineering_drills.py", "task_worker.py", "task_context.py")
COMMIT = re.compile(r"[0-9a-f]{40}")
_ACTIVE = {}
_SCOPES = threading.local()
GIT_LOCATION_ENV = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
                    "GIT_CONFIG_COUNT", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM"}


def git_location_keys(environment):
    return {name for name in environment if name in GIT_LOCATION_ENV or name.startswith("GIT_CONFIG_KEY_") or name.startswith("GIT_CONFIG_VALUE_")}


class ControllerBusy(TimeoutError):
    """Native contention only; access denial or invalid metadata is never busy."""
    def __init__(self, path, error=None):
        super().__init__("Controller lease timeout; preserve resources and retry: " + str(path))
        self.winerror = getattr(error, "winerror", None)


def error_result(error):
    return {"status": "BLOCKED", "reason": safe_exception(error), "error_type": type(error).__name__,
            "error_category": "RESOURCE" if isinstance(error, ControllerBusy) else "TRANSIENT" if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) else "POLICY",
            "winerror": getattr(error, "winerror", None)}


def safe_diagnostic(value, limit=8192):
    """One idempotent formatter for every persistent diagnostic text channel."""
    result = str(value)
    result = re.sub(r'(?i)(https?://)[^/\s"\']+@', r'\1<redacted>@', result)
    result = re.sub(r'(?im)(authorization\s*[:=]\s*)[^\r\n]+', r'\1<redacted>', result)
    result = re.sub(r'(?i)([?&](?:access_token|api[_-]?key|token|password|secret)=)[^&\s"\']+', r'\1<redacted>', result)
    result = re.sub(r'(?i)((?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY))\s*[:=]\s*)[^\s,;\r\n]+', r'\1<redacted>', result)
    result = re.sub(r'''(?i)((?:"|')[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)(?:"|')\s*:\s*)(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^,}\s]+)''', r'\1"<redacted>"', result)
    secrets = sorted({item for name, item in os.environ.items() if item
                      and re.search(r'token|secret|password|api.?key|authorization', name, re.I)}, key=len, reverse=True)
    for item in secrets:
        # Do not re-redact the marker for short values such as a one-character
        # password. Repeated queue/outbox/worker formatting stays idempotent.
        result = '<redacted>'.join(part.replace(item, '<redacted>') for part in result.split('<redacted>'))
    return result[-limit:]


def safe_exception(error: BaseException) -> str:
    result = str(error)
    if isinstance(error, subprocess.CalledProcessError):
        detail = error.stderr or error.stdout or b""
        detail = detail.decode("utf-8", errors="replace") if isinstance(detail, bytes) else str(detail)
        if detail.strip():
            result += ": " + detail.strip()
    return safe_diagnostic(result)


def safe_error_payload(value):
    """Preserve typed IDs/status/codes; sanitize free diagnostic fields deeply."""
    fields = {"reason", "error", "last_error", "diagnostic", "stderr", "stdout", "message", "blockers", "errors", "command", "argv"}
    def text(item):
        if isinstance(item, str):
            return safe_diagnostic(item)
        if isinstance(item, list):
            return [text(part) for part in item]
        if isinstance(item, dict):
            return {key: text(part) for key, part in item.items()}
        return item
    if isinstance(value, dict):
        return {key: text(item) if key.casefold() in fields else safe_error_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_error_payload(item) for item in value]
    return value
EARLY_GUARD = '''# Controller execution ABI 1: keep before all local/business imports.
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
'''


def redirected(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def read_json(path: Path) -> dict:
    if redirected(path) or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Controller metadata is redirected, missing or oversized: " + str(path))
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Controller metadata is not an object")
    return value


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _source_command(common: Path, *arguments: str):
    environment = os.environ.copy()
    for name in git_location_keys(environment):
        environment.pop(name, None)
    return ["git", "--no-optional-locks", "--no-replace-objects", "--git-dir", str(common), "-C", str(common), *arguments], environment


def git_source(common: Path, *arguments: str) -> bytes:
    command, environment = _source_command(common, *arguments)
    return subprocess.check_output(command,
        cwd=common, env=environment, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)


SOURCE_FILE_LIMIT = 4 * 1024 * 1024
SOURCE_TOTAL_LIMIT = 32 * 1024 * 1024
SOURCE_ENTRY_LIMIT = 4096


def _source_name(name):
    parts = name.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {prefix + digit for prefix in ("COM", "LPT") for digit in "123456789\u00b9\u00b2\u00b3"}
    return (name.startswith("scripts/") and "\\" not in name and
            not any(part in {"", ".", ".."} or part.endswith((".", " "))
                    or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                    or part.split(".")[0].rstrip(" ").upper() in reserved for part in parts))


def _source_inventory(raw):
    if len(raw) > 4 * 1024 * 1024 or (raw and not raw.endswith(b"\0")):
        raise ValueError("Controller Git inventory framing or size differs")
    entries, names, total = [], set(), 0
    for record in raw.split(b"\0")[:-1]:
        fields, separator, path = record.partition(b"\t")
        header = fields.split()
        if (not separator or len(header) != 4 or header[0] not in {b"100644", b"100755"}
                or header[1] != b"blob" or not re.fullmatch(rb"[0-9a-f]{40}", header[2])
                or not re.fullmatch(rb"0|[1-9][0-9]*", header[3])):
            raise ValueError("Controller Git source must contain regular, sized blobs")
        name, size = path.decode("utf-8"), int(header[3])
        if not _source_name(name) or name.casefold() in names:
            raise ValueError("Controller Git inventory has unsafe or duplicate source paths")
        names.add(name.casefold()); total += size
        if size > SOURCE_FILE_LIMIT or total > SOURCE_TOTAL_LIMIT or len(entries) >= SOURCE_ENTRY_LIMIT:
            raise ValueError("Controller Git source exceeds bounded payload limits")
        entries.append({"path": name, "oid": header[2].decode("ascii"), "size": size})
    return entries


def _source_blob(entry, payload):
    # Object framing corroborates exact tree membership; raw SHA256 remains the
    # independent source/manifest/local-payload equality used by verification.
    if (len(payload) != entry["size"] or hashlib.sha1(b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload).hexdigest() != entry["oid"]):
        raise ValueError("Controller Git blob bytes differ from their committed tree object")
    return payload


def _batch_unsupported(error):
    # This is the exact frozen read-only WSL bridge diagnostic. Permission,
    # transport corruption, timeout, missing objects and malformed output never
    # qualify for fallback merely because cat-file was the attempted command.
    return (error.returncode == 1 and error.stderr == b"Cross-host Git bridge refuses mutations\n"
            and not error.output)


def _batch_source(common, entries):
    command, environment = _source_command(common, "cat-file", "--batch")
    request = b"".join((entry["oid"] + "\n").encode("ascii") for entry in entries)
    # Spool native output so source verification has bounded RAM even if a
    # broken read transport emits excess bytes. No cache/runtime file is made.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run(command, input=request, stdout=output, stderr=errors,
                                cwd=common, env=environment, timeout=30, check=False)
        errors.seek(0); detail = errors.read(8192)
        if result.returncode:
            output.seek(0)
            raise subprocess.CalledProcessError(result.returncode, command, output=output.read(1), stderr=detail)
        length = output.tell()
        if length > sum(entry["size"] + 128 for entry in entries):
            raise ValueError("Controller Git batch output exceeds its inventory")
        output.seek(0)
        payloads = {}
        for entry in entries:
            expected = (entry["oid"] + " blob " + str(entry["size"]) + "\n").encode("ascii")
            if output.readline(128) != expected:
                raise ValueError("Controller Git batch header/object/type/size differs")
            payload = output.read(entry["size"])
            if len(payload) != entry["size"] or output.read(1) != b"\n":
                raise ValueError("Controller Git batch payload is truncated or misframed")
            payloads[entry["path"]] = _source_blob(entry, payload)
        if output.read(1):
            raise ValueError("Controller Git batch has unexpected trailing objects or bytes")
        return payloads


def source_files(common: Path, commit: str) -> dict:
    """One exact inventory and framed raw-byte batch; explicit bridge fallback."""
    if not COMMIT.fullmatch(commit):
        raise ValueError("Controller source requires an exact commit identifier")
    entries = _source_inventory(git_source(common, "ls-tree", "-r", "-z", "-l", "--full-tree", commit, "--", "scripts/"))
    try:
        return _batch_source(common, entries)
    except subprocess.CalledProcessError as error:
        if not _batch_unsupported(error):
            raise
        return {entry["path"]: _source_blob(entry, git_source(common, "show", commit + ":" + entry["path"])) for entry in entries}


def common_directory(repo: Path) -> Path:
    raw = subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30).decode().strip()
    return Path(raw).resolve()


def target_launcher(common: Path, commit: str) -> str:
    tree = ast.parse(git_source(common, "show", commit + ":scripts/tenon.py").decode("utf-8"))
    install = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install"), None)
    candidates = [node.value.value for node in ast.walk(install) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "launcher" for target in node.targets)
                  and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)] if install else []
    if len(candidates) != 1:
        raise ValueError("Target source has no uniquely verifiable launcher literal")
    return candidates[0]


def verify_source(directory: Path, repo: Path, expected_commit=None) -> dict:
    """Full Git source bytes / manifest SHA256 / local payload SHA256 equality."""
    directory = Path(directory)
    manifest = read_json(directory / "controller.json")
    commit = expected_commit or directory.name
    if (manifest.get("schema_version") != 1 or manifest.get("commit") != commit
            or directory.name != commit or not COMMIT.fullmatch(commit) or redirected(directory)):
        raise ValueError("Controller identity/version differs")
    entries = manifest.get("files", [])
    if not isinstance(entries, list) or any(not isinstance(item, dict) or not isinstance(item.get("path"), str) for item in entries):
        raise ValueError("Invalid controller inventory")
    names = [item["path"] for item in entries]
    common = common_directory(Path(repo))
    sources = source_files(common, commit)
    if "scripts/task_lifecycle.py" not in names or len(names) != len(set(names)) or sorted(names) != sorted(sources):
        raise ValueError("Controller inventory differs from its source commit")
    for item in entries:
        name = item["path"]
        parts = name.split("/")
        if not _source_name(name):
            raise ValueError("Unsafe controller source path")
        path = directory.joinpath(*parts)
        if (not path.is_file() or not path.resolve().is_relative_to(directory.resolve())
                or any(redirected(parent) for parent in [path, *path.parents[:len(parts)]])):
            raise ValueError("Controller source is redirected")
        source_hash = hashlib.sha256(sources[name]).hexdigest()
        if file_digest(path) != item.get("sha256") or source_hash != item.get("sha256"):
            raise ValueError("Controller integrity check failed; payload/manifest differs from committed source: " + name)
    return manifest


def cache_location(entry: Path):
    absolute = Path(entry).absolute()
    directory = absolute.parent.parent
    if (absolute.parent.name != "scripts" or directory.parent.name != "controllers"
            or directory.parent.parent.name != "tenon" or not COMMIT.fullmatch(directory.name)):
        return None
    if any(os.path.lexists(path) and redirected(path) for path in (absolute, absolute.parent, directory, directory.parent, directory.parent.parent)):
        raise ValueError("Controller entry is redirected")
    directory = directory.resolve()
    return directory.parent.parent, directory


def _regular(path: Path):
    if not os.path.lexists(path) or redirected(path) or not path.is_file():
        raise ValueError("Existing owned controller lock is missing or redirected: " + str(path))


class ReadLock:
    """OPEN_EXISTING/O_RDONLY; shared users, exclusive admission/probes; no writes."""
    def __init__(self, path: Path, *, exclusive=False, timeout=5):
        if not 0 <= timeout <= 30:
            raise ValueError("Controller lock timeout must be bounded to 0..30 seconds")
        self.path, self.exclusive, self.timeout = Path(path), exclusive, timeout
        self.stream = None
        self.locked = False
        self.pid = os.getpid()

    def __enter__(self):
        _regular(self.path)
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes as W
            class Offsets(ctypes.Structure):
                _fields_ = [("Offset", W.DWORD), ("OffsetHigh", W.DWORD)]
            class Position(ctypes.Union):
                _anonymous_ = ("words",)
                _fields_ = [("words", Offsets), ("Pointer", W.LPVOID)]
            class Overlapped(ctypes.Structure):
                _anonymous_ = ("position",)
                _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("position", Position), ("hEvent", W.HANDLE)]
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
            kernel.CreateFileW.restype = W.HANDLE
            kernel.LockFileEx.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, W.DWORD, ctypes.POINTER(Overlapped)]
            kernel.LockFileEx.restype = W.BOOL
            kernel.UnlockFileEx.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.POINTER(Overlapped)]
            kernel.UnlockFileEx.restype = W.BOOL
            kernel.CloseHandle.argtypes = [W.HANDLE]
            kernel.CloseHandle.restype = W.BOOL
            handle = kernel.CreateFileW(str(self.path), 0x80000000, 3, None, 3, 0x80 | 0x00200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle(handle)
                raise
            try:
                self.stream = os.fdopen(descriptor, "rb", buffering=0)
            except BaseException:
                os.close(descriptor)
                raise
            self._kernel, self._overlapped, self._handle = kernel, Overlapped(), handle
        else:
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
            try:
                self.stream = os.fdopen(descriptor, "rb", buffering=0)
            except BaseException:
                os.close(descriptor)
                raise
        opened = os.fstat(self.stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or getattr(opened, "st_file_attributes", 0) & 0x400:
            self.stream.close()
            raise ValueError("Controller lock handle is not a regular owned file")
        self.file_identity = [opened.st_dev, opened.st_ino]
        started = time.monotonic()
        try:
            while True:
                try:
                    if os.name == "nt":
                        import ctypes
                        if not self._kernel.LockFileEx(self._handle, 1 | (2 if self.exclusive else 0), 0, 1, 0, ctypes.byref(self._overlapped)):
                            raise ctypes.WinError(ctypes.get_last_error())
                    else:
                        import fcntl
                        fcntl.flock(self.stream.fileno(), (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    self.locked = True
                    break
                except OSError as error:
                    busy = getattr(error, "winerror", None) == 33 if os.name == "nt" else error.errno in {11, 13}
                    if not busy:
                        raise
                    if time.monotonic() - started >= self.timeout:
                        raise ControllerBusy(self.path, error) from error
                    time.sleep(0.01)
            self.stream.seek(1)
            raw = self.stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Controller lock metadata oversized")
            self.metadata = json.loads(raw)
            if not isinstance(self.metadata, dict):
                raise ValueError("Controller lock metadata is not an object")
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.stream is None or self.stream.closed:
            return
        try:
            if self.locked and self.pid == os.getpid():
                if os.name == "nt":
                    import ctypes
                    self._kernel.UnlockFileEx(self._handle, 0, 1, 0, ctypes.byref(self._overlapped))
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.locked = False

    def __exit__(self, *_):
        self.close()


def _identity(base: Path) -> dict:
    value = read_json(base / "repository.json")
    if value.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{32}", str(value.get("repo_id", ""))):
        raise ValueError("Controller repository identity is missing or unsupported")
    return value


def _lock_value(base: Path, kind: str, commit=None, **extra):
    return {"schema_version": 1, "kind": kind, "abi": 1, "repo_id": _identity(base)["repo_id"],
            "host_platform": os.name, "host": socket.gethostname(), **({"commit": commit} if commit else {}), **extra}


def _check_lock(base: Path, value: dict, kind: str, commit=None):
    expected = _lock_value(base, kind, commit)
    allowed = set(expected) | ({"manifest_sha256"} if kind == "version" else set())
    if (set(value) != allowed or any(value.get(name) != item for name, item in expected.items())
            or (kind == "version" and not re.fullmatch(r"[0-9a-f]{64}", str(value.get("manifest_sha256", ""))))):
        raise ValueError("Controller lease identity/ABI/native host differs; explicit qualified install required")


def _lock_path(base: Path, commit=None) -> Path:
    directory = base / "controller-leases"
    if not directory.is_dir() or redirected(directory) or directory.resolve() != directory:
        raise ValueError("Controller admission is unavailable; run the qualified source installer explicitly")
    return directory / ((commit + ".lock") if commit else "admission.lock")


def _write_static(path: Path, value: dict):
    if os.path.lexists(path):
        with ReadLock(path, timeout=5) as lock:
            if lock.metadata != value:
                raise ValueError("Existing controller lock metadata differs; preserve it for inspection")
        return
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(b"\n" + json.dumps(value, sort_keys=True).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(stat.S_IREAD | (stat.S_IRGRP | stat.S_IROTH if os.name != "nt" else 0))
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_record(path: Path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(value, sort_keys=True).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _file_identity(path: Path):
    _regular(path)
    value = path.stat()
    return [value.st_dev, value.st_ino]


def _identity_record(path: Path, expected: dict, opened=None):
    record = path.with_suffix(".json")
    if not record.exists():
        raise ValueError("Controller anchor identity is missing; preserve the original and run qualified recovery")
    value = read_json(record)
    if (value.get("metadata") != expected or value.get("file_identity") != _file_identity(path)
            or (opened is not None and value.get("file_identity") != opened)):
        raise ValueError("Controller anchor identity changed; original readers may still hold another file object")


def _create_anchor(path: Path, expected: dict):
    if os.path.lexists(path) or os.path.lexists(path.with_suffix(".json")):
        _identity_record(path, expected)
        _write_static(path, expected)
        return
    _write_static(path, expected)
    _atomic_record(path.with_suffix(".json"), {"metadata": expected, "file_identity": _file_identity(path)})


@contextlib.contextmanager
def integration_scope(store):
    from task_store import FileLease
    contexts = getattr(_SCOPES, "contexts", None)
    if contexts is None:
        contexts = _SCOPES.contexts = {}
    key = (str(store.root.resolve()), os.getpid())
    context = contexts.setdefault(key, {})
    if context.get("integration") is not None:
        yield
        return
    try:
        with FileLease(store.root / "locks/integration.lock") as lock:
            context["integration"] = lock
            yield
    finally:
        context.pop("integration", None)
        if not context:
            contexts.pop(key, None)


@contextlib.contextmanager
def mutation_context(store, *, initialize=False):
    """Mutating paths only: integration -> entry-install -> immutable admission."""
    from task_store import FileLease
    with integration_scope(store), contextlib.ExitStack() as stack:
        context = _SCOPES.contexts[(str(store.root.resolve()), os.getpid())]
        if context.get("mutation") is not None:
            yield context["mutation"]
            return
        stack.enter_context(FileLease(store.root / "locks/entry-install.lock"))
        directory = store.root / "controller-leases"
        if initialize:
            directory.mkdir(exist_ok=True)
            if redirected(directory) or directory.resolve() != directory:
                raise ValueError("Controller lock storage is redirected")
            present = os.path.lexists(directory / "admission.lock") or os.path.lexists(directory / "admission.json")
            if not present:
                prior = [path for path in (store.root / "controllers").glob("*/controller.json")
                         if not redirected(path) and read_json(path).get("controller_lease_abi") == 1]
                if prior or any(directory.iterdir()):
                    raise ValueError("Known controller registry lost its admission anchor; preserve and recover explicitly")
            _create_anchor(directory / "admission.lock", _lock_value(store.root, "admission"))
        admission = stack.enter_context(ReadLock(_lock_path(store.root), exclusive=True))
        _check_lock(store.root, admission.metadata, "admission")
        _identity_record(admission.path, admission.metadata, admission.file_identity)
        token = {"base": store.root.resolve(), "pid": os.getpid(), "thread": threading.get_ident(), "admission": admission}
        context["mutation"] = token
        try:
            yield token
        finally:
            context.pop("mutation", None)


def require_mutation(store, token):
    if (not token or token.get("base") != store.root.resolve() or token.get("pid") != os.getpid()
            or token.get("thread") != threading.get_ident() or token["admission"].stream.closed):
        raise ValueError("A current controller mutation admission is required")


def source_capability(common: Path, commit: str) -> int:
    sources = source_files(common, commit)
    if "scripts/controller_leases.py" not in sources:
        return 0
    helper = ast.parse(sources["scripts/controller_leases.py"].decode("utf-8"))
    values = [node.value.value for node in helper.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "CONTROLLER_LEASE_ABI" for target in node.targets)
              and isinstance(node.value, ast.Constant)]
    if values != [1]:
        raise ValueError("Unsupported controller source ABI")
    for entry in ENTRIES:
        if "scripts/" + entry not in sources:
            raise ValueError("Controller source is missing a controlled entry")
        source = sources["scripts/" + entry].decode("utf-8").replace("\r\n", "\n")
        if source.count(EARLY_GUARD) != 1:
            raise ValueError("Controller source entry has no early execution admission: " + entry)
        prefix = ast.parse(source.split(EARLY_GUARD, 1)[0])
        if any(not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
               and not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))
               for node in prefix.body):
            raise ValueError("Controller entry executes code before its admission: " + entry)
    return 1


def prepare_version(store, directory: Path, token):
    require_mutation(store, token)
    if source_capability(store.common, directory.name) != 1:
        return 0
    manifest = read_json(directory / "controller.json")
    anchor = _lock_path(store.root, directory.name)
    if "controller_lease_abi" not in manifest:
        manifest["controller_lease_abi"] = 1
        if os.path.lexists(anchor) or os.path.lexists(anchor.with_suffix(".json")):
            # A previously reclaimed cache may be re-deployed from exactly the
            # same Git bytes. Reuse its original anchor; never replace it.
            expected_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
            expected = _lock_value(store.root, "version", directory.name, manifest_sha256=expected_hash)
            _identity_record(anchor, expected)
            _write_static(anchor, expected)
        _atomic_record(directory / "controller.json", manifest)
    elif manifest["controller_lease_abi"] != 1:
        raise ValueError("Unsupported controller manifest lease ABI")
    elif not os.path.lexists(anchor) or not os.path.lexists(anchor.with_suffix(".json")):
        raise ValueError("A qualified controller lost its anchor; do not recreate it over live readers")
    value = _lock_value(store.root, "version", directory.name, manifest_sha256=file_digest(directory / "controller.json"))
    _create_anchor(anchor, value)
    return 1


def version_observation(base: Path, directory: Path, *, owned_exclusive=None) -> dict:
    """Never initialize, repair or infer PID identity from a held native lock."""
    try:
        if source_capability(base.parent, directory.name) != 1:
            return {"state": "UNKNOWN", "reason": "Legacy source has no execution ABI; sidecars do not qualify it"}
        manifest = read_json(directory / "controller.json")
        if manifest.get("controller_lease_abi") != 1:
            return {"state": "UNKNOWN", "reason": "Controller ABI metadata is missing; explicit qualified install required"}
        path = _lock_path(base, directory.name)
        identity = read_json(path.with_suffix(".json"))
        metadata = identity.get("metadata", {})
        _check_lock(base, metadata, "version", directory.name)
        _identity_record(path, metadata)
        if metadata.get("manifest_sha256") != file_digest(directory / "controller.json"):
            raise ValueError("Controller lease manifest identity differs")
        if owned_exclusive is not None:
            if (owned_exclusive.path != path or not owned_exclusive.exclusive or not owned_exclusive.locked
                    or owned_exclusive.pid != os.getpid()):
                raise ValueError("GC supplied no actual exclusive version lock")
            verify_version_lock(base, directory, owned_exclusive)
            return {"state": "IDLE", "held": False, "exclusive_for_delete": True}
        try:
            with ReadLock(path, exclusive=True, timeout=0) as probe:
                verify_version_lock(base, directory, probe)
            return {"state": "IDLE", "held": False}
        except ControllerBusy:
            with ReadLock(path, timeout=0) as reader:
                verify_version_lock(base, directory, reader)
            return {"state": "HELD", "held": True, "pid": None,
                    "qualification": "Actual native shared lease; no PID inferred"}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {"state": "UNKNOWN", "reason": str(error), "error_type": type(error).__name__,
                "winerror": getattr(error, "winerror", None)}


def registry_issues(base: Path) -> list[str]:
    issues = []
    try:
        directory = _lock_path(base).parent
        for path in directory.iterdir():
            if (redirected(path) or not path.is_file()
                    or not re.fullmatch(r"(?:admission|[0-9a-f]{40})\.(?:lock|json)", path.name)):
                issues.append("Unclassified controller lease record: " + path.name)
                continue
            if path.suffix == ".json" and path.stem != "admission":
                try:
                    record = read_json(path)
                    if set(record) != {"metadata", "file_identity"}:
                        raise ValueError("Controller anchor record has unknown fields")
                    _check_lock(base, record["metadata"], "version", path.stem)
                    _identity_record(path.with_suffix(".lock"), record["metadata"])
                except (OSError, ValueError, KeyError, TypeError) as error:
                    issues.append(path.name + ": " + str(error))
        with ReadLock(_lock_path(base), timeout=0) as lock:
            _check_lock(base, lock.metadata, "admission")
            _identity_record(lock.path, lock.metadata)
    except ControllerBusy:
        # A caller holding the actual admission exclusively is inspecting under it.
        # Its immutable identity is still verified without claiming another PID.
        try:
            record = read_json(_lock_path(base).with_suffix(".json"))
            _check_lock(base, record["metadata"], "admission")
            _identity_record(_lock_path(base), record["metadata"])
        except (OSError, ValueError, KeyError) as error:
            issues.append(str(error))
    except (OSError, ValueError, KeyError) as error:
        issues.append(str(error))
    return issues


def active_for(entry: Path) -> bool:
    location = cache_location(Path(entry))
    if location is None:
        return False
    record = _ACTIVE.get(str(location[1]))
    return bool(record and record.pid == os.getpid() and record.stream and not record.stream.closed)


def entry_command(entry: Path, *arguments: str) -> list[str]:
    """A persistent bootstrap survives a target-cache deletion before child start."""
    location = cache_location(entry)
    if location is None:
        return [sys.executable, str(entry), *arguments]
    base, _ = location
    return [sys.executable, "-B", str(base / "bin/tenon.py"), "_controller", str(entry), *arguments]


def verify_version_lock(base: Path, directory: Path, lock: ReadLock):
    _check_lock(base, lock.metadata, "version", directory.name)
    _identity_record(lock.path, lock.metadata, lock.file_identity)
    if lock.metadata.get("manifest_sha256") != file_digest(directory / "controller.json"):
        raise ValueError("Controller manifest differs from its qualified lease metadata")


@contextlib.contextmanager
def execution(entry: Path):
    location = cache_location(entry)
    if location is None:
        yield
        return
    base, directory = location
    if active_for(entry):
        yield
        return
    if source_capability(base.parent, directory.name) != 1:
        raise ValueError("Legacy controller source has no admission ABI; preserve it")
    with ReadLock(_lock_path(base), exclusive=True) as admission:
        _check_lock(base, admission.metadata, "admission")
        _identity_record(admission.path, admission.metadata, admission.file_identity)
        if (directory.parent != (base / "controllers").resolve() or not directory.is_dir() or redirected(directory)):
            raise ValueError("Controller cache disappeared or is redirected; business execution blocked")
        lease = ReadLock(_lock_path(base, directory.name))
        lease.__enter__()
        try:
            verify_version_lock(base, directory, lease)
        except BaseException:
            lease.close()
            raise
    _ACTIVE[str(directory)] = lease
    try:
        verify_source(directory, base.parent, directory.name)
        yield
    finally:
        _ACTIVE.pop(str(directory), None)
        lease.close()


def run_installed(config: dict, arguments: list[str], binary: Path) -> int:
    root = Path(config["controller"])
    binary = Path(binary).resolve()
    common = binary.parent.parent
    if binary != common / "tenon/bin":
        raise ValueError("Installed binary storage differs")
    expected = common / "tenon/controllers" / config["commit"]
    if root.resolve() != expected.resolve() or root != expected or not COMMIT.fullmatch(config["commit"]):
        raise ValueError("Installed controller escaped owned storage")
    identity = _identity(common / "tenon")
    if config.get("repo_id") != identity["repo_id"]:
        raise ValueError("Installed repository identity differs")
    raw_primary = next((line[9:] for line in git_source(common, "worktree", "list", "--porcelain").decode().splitlines()
                        if line.startswith("worktree ")), None)
    if not raw_primary or raw_primary.startswith('"') or Path(raw_primary).resolve() != Path(config["repo"]).resolve():
        raise ValueError("Installed fixed primary checkout differs")
    if arguments[:1] == ["_controller"]:
        if len(arguments) < 2:
            raise ValueError("Controlled entry is missing")
        entry = Path(arguments[1]).absolute()
        location = cache_location(entry)
        if location is None or location[0] != common / "tenon" or entry.name not in ENTRIES:
            raise ValueError("External controller route is outside this repository")
        arguments = arguments[2:]
    else:
        if any(item.split("=", 1)[0] in {"--r", "--re", "--rep", "--repo"} for item in arguments):
            raise ValueError("This installed entry is bound to its registered repository")
        entry = root / "scripts/tenon.py"
        arguments = ["--repo", config["repo"], *arguments]
    # Private external routes retain their argv, but never another production repo.
    for index, argument in enumerate(arguments):
        if argument.split("=", 1)[0] in {"--r", "--re", "--rep"}:
            raise ValueError("Repository option abbreviations are not accepted by the installed entry")
        candidate = arguments[index + 1] if argument == "--repo" and index + 1 < len(arguments) else argument[7:] if argument.startswith("--repo=") else None
        if candidate is not None and Path(candidate).resolve() != Path(config["repo"]).resolve():
            raise ValueError("External controller repository differs")
    previous_argv, previous_path, previous_cwd = sys.argv[:], sys.path[:], Path.cwd()
    bytecode, old_environment = sys.dont_write_bytecode, os.environ.get("PYTHONDONTWRITEBYTECODE")
    old_prefix = sys.pycache_prefix
    previous_git = {name: os.environ[name] for name in git_location_keys(os.environ)}
    for name in previous_git:
        os.environ.pop(name, None)
    sys.dont_write_bytecode = True
    # Ignore old bytecode as well as preventing new bytecode; this path is never created.
    sys.pycache_prefix = str(common / "tenon/controller-leases" / (".unwritten-" + uuid.uuid4().hex))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        worktree_override = subprocess.run(["git", "--no-optional-locks", "--git-dir", str(common),
            "config", "--get-all", "core.worktree"], cwd=common, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if worktree_override.returncode not in {0, 1} or worktree_override.stdout.strip():
            raise ValueError("Configured core.worktree redirects bound Git work; preserve configuration and use an explicit qualified setup")
        sys.path.insert(0, str(entry.parent))
        with execution(entry):
            sys.argv = [str(entry), *arguments]
            os.chdir(config["repo"])
            try:
                runpy.run_path(str(entry), run_name="__main__")
                return 0
            except SystemExit as result:
                return result.code if isinstance(result.code, int) else 0 if result.code is None else 1
    finally:
        sys.argv, sys.path = previous_argv, previous_path
        os.chdir(previous_cwd)
        sys.dont_write_bytecode = bytecode
        sys.pycache_prefix = old_prefix
        if old_environment is None:
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        else:
            os.environ["PYTHONDONTWRITEBYTECODE"] = old_environment
        os.environ.update(previous_git)
