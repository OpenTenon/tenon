#!/usr/bin/env python3
"""Deterministic isolated Skill workflow acceptance; never measures LLM routing."""
from __future__ import annotations
import argparse
import contextlib
import functools
import threading
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from engineering_common import ROOT, digest, source_state as _raw_source_state, utc_now, write_json
from skill_eval import OFFLINE_SCENARIOS, codex_command, configured_mcp_overrides, load_cases


_ENVIRONMENT_LOCK = threading.RLock()
_ENVIRONMENT_KEYS = {"PATH", "PATHEXT", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC",
                     "SYSTEMDRIVE", "LANG", "LC_ALL", "HOME", "USERPROFILE", "APPDATA",
                     "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR", "CODEX_HOME"}


def git_environment() -> dict:
    """Never inherit GIT_* discovery, config, hooks, objects, index or executable injection."""
    env = {key: value for key, value in os.environ.items() if key in _ENVIRONMENT_KEYS}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
               GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.hooksPath", GIT_CONFIG_VALUE_0=os.devnull)
    return env


@contextlib.contextmanager
def controlled_environment(env):
    """Process-scoped guard for shared APIs without env parameters; always restore the caller.

    Fixture workers are independent single-purpose child processes. The reentrant
    guard also protects direct Fixture/API calls and nested source fingerprints.
    No persistent user/system configuration or home directory is modified.
    """
    with _ENVIRONMENT_LOCK:
        previous = dict(os.environ)
        os.environ.clear()
        os.environ.update(env)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(previous)


def source_state(root: Path = ROOT):
    """Bind public fingerprints to the requested checkout, never inherited Git metadata."""
    root = root.resolve()
    with controlled_environment(git_environment()):
        actual = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        if actual != root:
            raise ValueError("Source fingerprint checkout identity differs")
        return _raw_source_state(root)


def owned_directory(directory: Path):
    """Reject redirection before allocating any fixture files; use only fresh owned children."""
    raw = directory.absolute()
    if not raw.is_dir():
        raise ValueError("An existing owned fixture directory is required")
    for path in (raw, *raw.parents):
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Fixture directory cannot traverse symlinks or reparse points")
    return raw.resolve()


def child_environment(home: Path) -> dict:
    """Fresh child environment: no provider credentials, global Git config or user home."""
    env = git_environment()
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
        directory = home / key.lower()
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    env.update(CODEX_HOME=str(home / "codex"), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    Path(env["CODEX_HOME"]).mkdir(exist_ok=True)
    return env


def configuration_check(executable: str | None = None) -> dict:
    """Only --version and mcp list; outputs may contain secrets, so never retain them."""
    result = {"status": "NOT_RUN", "model_calls": 0, "operation": "version + effective mcp list only",
              "discovery": "explicit existing entry" if executable else "PATH then desktop native entry",
              "cli_version": None, "diagnostic": None}
    try:
        cli = codex_command(executable)
    except (OSError, ValueError) as error:
        result["diagnostic"] = "Existing safe CLI unavailable (" + type(error).__name__ + ")"
        return result
    result["entry_kind"] = "native" if Path(cli[0]).suffix.lower() == ".exe" else "native-or-node"
    with tempfile.TemporaryDirectory(prefix="tenon-skill-config-") as temporary:
        root = Path(temporary).resolve()
        env = child_environment(root / "child")
        fixture, config = root / "project", Path(env["CODEX_HOME"])
        fixture.mkdir(); (fixture / ".codex").mkdir()
        subprocess.run(["git", "init", "--quiet", str(fixture)], env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        marker = root / "server-must-not-start"
        command = "command=" + json.dumps(sys.executable) + "\nargs=" + json.dumps(
            ["-c", "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('started')"]) + "\n"
        files = {
            config / "config.toml": 'model_provider="offline_fixture"\n[model_providers.offline_fixture]\nname="offline"\nbase_url="http://127.0.0.1:9/v1"\nwire_api="responses"\n[mcp_servers.base]\n' + command
                + "[projects." + json.dumps(str(fixture)) + ']\ntrust_level="trusted"\n',
            config / "layer.config.toml": "[mcp_servers.profile]\n" + command,
            fixture / ".codex/config.toml": "[mcp_servers.project]\n" + command,
        }
        for path, content in files.items():
            path.write_text(content, encoding="utf-8")
        before = {str(path): digest(path) for path in files}
        try:
            version = subprocess.run([*cli, "--version"], cwd=fixture, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
            text = version.stdout.decode("utf-8", errors="replace").strip()
            if version.returncode or not re.fullmatch(r"codex-cli [0-9A-Za-z.+_-]+", text):
                result["diagnostic"] = "Existing entry cannot provide a supported version identity"
                return result
            result["cli_version"] = text
            overrides = configured_mcp_overrides(fixture, cli, "layer", env=env)
            observed = {value for value in overrides if value != "-c"}
            assertions = {
                "all_three_layers_resolved_and_disabled": observed == {
                    "mcp_servers.base.enabled=false", "mcp_servers.profile.enabled=false",
                    "mcp_servers.project.enabled=false"},
                "fixture_configs_unchanged": before == {str(path): digest(path) for path in files},
                "no_server_started": not marker.exists(),
                "no_credentials_created": not any((root / "child").rglob("auth.json")),
            }
            result.update(status="PASS" if all(assertions.values()) else "FAIL", assertions=assertions)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            # No stderr, inventory, config text or exception message can escape this boundary.
            status = "NOT_RUN" if isinstance(error, (OSError, subprocess.SubprocessError)) or str(error).startswith("Cannot resolve effective") else "FAIL"
            result.update(status=status, diagnostic="Configuration-only capability not confirmed (" + type(error).__name__ + ")")
    return result


def _git(repo, *args):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), *args],
                                   env=git_environment(), stderr=subprocess.PIPE, timeout=30)


def _files(root):
    return {path.relative_to(root).as_posix(): digest(path) for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()}


def _expect_rejected(action):
    try:
        action()
    except ValueError:
        return True
    return False


class Fixture:
    """An independent repository, bare origin and linked task; no production Git metadata."""
    def __init__(self, directory: Path):
        self.directory = owned_directory(directory)
        self.primary, self.remote, self.worktree = [self.directory / name for name in ("primary", "origin.git", "task")]
        if any(os.path.lexists(path) for path in (self.primary, self.remote, self.worktree)):
            raise ValueError("Fixture resources must be fresh owned paths")
        home = Path(tempfile.mkdtemp(prefix="environment-", dir=self.directory))
        self.environment = child_environment(home)
        with controlled_environment(self.environment):
            self._initialize()

    def _initialize(self):
        self.primary.mkdir()
        _git(self.primary, "init", "--quiet", "--initial-branch=dev")
        self.verify_primary_identity()
        for key, value in (("user.name", "Skill offline fixture"), ("user.email", "fixture@example.invalid"),
                           ("core.autocrlf", "false"), ("core.hooksPath", str(self.directory / "no-hooks")),
                           ("protocol.allow", "never"), ("protocol.file.allow", "always")):
            _git(self.primary, "config", key, value)
        shutil.copytree(Path(__file__).resolve().parent, self.primary / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (self.primary / ".gitignore").write_text("build/\n__pycache__/\n", encoding="utf-8")
        (self.primary / "scripts/offline_target.py").write_text("VALUE = 0\n", encoding="utf-8")
        _git(self.primary, "add", "."); _git(self.primary, "commit", "--quiet", "-m", "owned offline fixture")
        _git(self.primary, "init", "--quiet", "--bare", str(self.remote))
        _git(self.primary, "remote", "add", "origin", str(self.remote))
        _git(self.primary, "push", "--quiet", "origin", "HEAD:refs/heads/dev")
        _git(self.primary, "worktree", "add", "--quiet", "-b", "dev-offline", str(self.worktree), "HEAD")
        self.head = _git(self.worktree, "rev-parse", "HEAD").decode().strip()
        self.verify_identity()
        from task_store import TaskStore
        self.store = TaskStore(self.primary)
        from task import start
        self.task = start("offline", "Bounded offline acceptance fixture", ["scripts/offline_target.py"],
                          ["make verify"], [], self.worktree, kind="engineering")
        self.original = source_state(self.primary)

    def verify_primary_identity(self):
        metadata = self.primary / ".git"
        if (not metadata.is_dir() or metadata.is_symlink()
                or getattr(metadata.lstat(), "st_file_attributes", 0) & 0x400
                or (metadata / "commondir").exists()):
            raise ValueError("Fixture primary metadata is absent or redirected")
        common = Path(_git(self.primary, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
        top = Path(_git(self.primary, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        if common != metadata or top != self.primary:
            raise ValueError("Fixture primary Git identity escaped its owned root")

    def verify_identity(self):
        for path in (self.primary, self.remote, self.worktree):
            if not path.is_dir() or path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Fixture resource identity is redirected or absent")
        self.verify_primary_identity()
        metadata = self.primary / ".git"
        for repo in (self.primary, self.worktree):
            common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
            top = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
            if common != metadata or top != repo:
                raise ValueError("Fixture Git metadata/worktree identity escaped its owned root")
        bare = Path(_git(self.remote, "rev-parse", "--absolute-git-dir").decode().strip()).resolve()
        if bare != self.remote or _git(self.remote, "rev-parse", "--is-bare-repository").strip() != b"true":
            raise ValueError("Fixture bare origin identity differs")
        if _git(self.primary, "remote", "get-url", "origin").decode().strip() != str(self.remote):
            raise ValueError("Fixture origin is not the exact owned local bare repository")

    def refs(self):
        return {"local": _git(self.primary, "for-each-ref", "--format=%(refname) %(objectname)").decode(),
                "remote": _git(self.primary, "ls-remote", "origin").decode()}

    def unchanged_primary(self):
        return source_state(self.primary) == self.original



def fixture_guard(function):
    @functools.wraps(function)
    def guarded(fixture):
        with controlled_environment(fixture.environment):
            fixture.verify_identity()
            return function(fixture)
    return guarded


@fixture_guard
def scope_scenario(fixture: Fixture):
    from task import scope_errors
    from skill_eval import implementation_assertion
    before = fixture.refs()
    target = fixture.worktree / "scripts/offline_target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    inside = not scope_errors(fixture.store.load("offline"), fixture.worktree)
    outside = fixture.worktree / "specs/forbidden.json"
    outside.parent.mkdir(); outside.write_text("{}\n", encoding="utf-8")
    denied = bool(scope_errors(fixture.store.load("offline"), fixture.worktree))
    outside.unlink(); outside.parent.rmdir()
    # Use the existing AST-vetted observable helper oracle, never prompt words.
    helper = fixture.worktree / "scripts/eval_target.py"
    helper.write_text("def exit_status(code):\n    return 'PASS'\n", encoding="utf-8")
    broken = not implementation_assertion(fixture.worktree)["passed"]
    helper.write_text("def exit_status(code):\n    return 'PASS' if code == 0 else 'BLOCKED' if code == 2 else 'FAIL'\n", encoding="utf-8")
    fixed = implementation_assertion(fixture.worktree)
    helper.unlink()
    return {"bounded_edit_accepted": inside, "actual_scope_escape_rejected": denied,
            "broken_behavior_rejected": broken, "repaired_behavior_matches_all_codes": fixed["passed"],
            "refs_preserved": before == fixture.refs(), "primary_preserved": fixture.unchanged_primary()}


@fixture_guard
def partial_scenario(fixture: Fixture):
    from engineering_common import execute, new_summary, finish_summary, validate_evidence
    directory = fixture.worktree / "build/verification/offline"
    report = directory / "summary.json"
    summary = new_summary(fixture.worktree, "verification")
    summary["checks"] = [execute([sys.executable, "-c", "assert 2 + 2 == 4"], fixture.worktree,
                                  directory / "fixture.log", check_id="offline-arithmetic")]
    finish_summary(summary, fixture.worktree, report)
    valid = validate_evidence(report, fixture.worktree)["status"] == "PASS"
    rejected = []
    for change in ({"kind": "verification-partial"}, {"partial": True},
                   {"kind": "engineering-unittest-shard"}, {"shard": {"index": 0, "count": 2}}):
        write_json(report, {**summary, **change})
        rejected.append(_expect_rejected(lambda: validate_evidence(report, fixture.worktree)))
    return {"executed_fixture_evidence_is_valid": valid, "every_partial_marker_rejected": all(rejected),
            "primary_preserved": fixture.unchanged_primary()}


@fixture_guard
def context_scenario(fixture: Fixture):
    import task_context
    import task_observations as observations
    from task import start
    from task_contract import set_lease_state
    # Analysis records bind a real observed source; a later edit makes it stale.
    analysis = start("analysis", "Read-only frozen source", [], ["make verify"], [], fixture.worktree,
                     kind="engineering", role="reviewer")
    before = _files(fixture.store.root)
    first = task_context.context(fixture.primary, "analysis")
    readonly = before == _files(fixture.store.root)
    target = fixture.worktree / "scripts/offline_target.py"
    target.write_text("VALUE = 2\n", encoding="utf-8")
    stale = task_context.context(fixture.primary, "analysis")
    _git(fixture.worktree, "restore", "scripts/offline_target.py")
    set_lease_state(fixture.store, "analysis", "completed", owner_id=analysis["owner_id"])
    observations.note(fixture.primary, "offline", "r1", "rework", "Observed offline correction")
    before = _files(fixture.store.root / "tasks")
    same = observations.note(fixture.primary, "offline", "r1", "rework", "Observed offline correction")
    noop = same["status"] == "UNCHANGED" and before == _files(fixture.store.root / "tasks")
    conflict = _expect_rejected(lambda: observations.note(fixture.primary, "offline", "r1", "intervention", "Different fact"))
    summary = task_context.context(fixture.primary, "offline")
    return {"readonly_context_preserves_all_runtime_bytes": readonly and first["source"]["mode"] == "live",
            "changed_frozen_context_is_stale": stale["source"]["mode"] == "stale",
            "duplicate_event_is_byte_stable": noop, "conflict_is_atomic": conflict and before == _files(fixture.store.root / "tasks"),
            "whole_task_totals_unknown": all(value is None for value in summary["manual_observations"]["totals"].values()),
            "primary_preserved": fixture.unchanged_primary()}


class LocalBackend:
    """Real local bare-ref operations; PR/CI responses are explicitly simulated."""
    name = "offline/fixture"
    def __init__(self, fixture):
        self.fixture = fixture
        self.remote_deletes = 0
    def refresh(self):
        pass
    def pull(self, number):
        head = self.fixture.head
        return {"number": 1, "merged": True, "state": "closed", "head": {"sha": head},
                "base": {"ref": "dev"}, "merge_commit_sha": head, "merged_at": "2026-11-17T00:00:00+00:00",
                "html_url": "https://example.invalid/offline/pr/1"}
    def latest_run(self, sha, event, number=None):
        from ci_gate import REQUIRED_JOBS
        return {"workflow_run": {"id": 1, "run_attempt": 1, "head_sha": sha, "event": event,
                                 "status": "completed", "conclusion": "success"},
                "jobs": [{"name": name, "run_id": 1, "run_attempt": 1, "status": "completed", "conclusion": "success"}
                         for name in sorted(REQUIRED_JOBS | {"ci-required"})]}
    def remote_head(self, branch):
        rows = _git(self.fixture.primary, "ls-remote", "origin", "refs/heads/" + branch).decode().split()
        return rows[0] if rows else None
    def delete_remote(self, branch, expected):
        if self.remote_head(branch) != expected:
            raise ValueError("Offline remote head advanced")
        _git(self.fixture.primary, "push", "--quiet", "--force-with-lease=refs/heads/" + branch + ":" + expected,
             "origin", ":refs/heads/" + branch)
        self.remote_deletes += 1


@fixture_guard
def cleanup_scenario(fixture: Fixture):
    from task_archive import verify_archive, worktrees
    from task_lifecycle import Lifecycle
    from task_observations import note
    from task_contract import set_lease_state
    _git(fixture.primary, "push", "--quiet", "origin", "HEAD:refs/heads/dev-offline")
    _git(fixture.primary, "update-ref", "refs/remotes/origin/dev-offline", fixture.head)
    set_lease_state(fixture.store, "offline", "paused", owner_id=fixture.task["owner_id"])
    backend = LocalBackend(fixture)
    fixture.store.mutate("offline", lambda row: {**row, "status": "DEV_GREEN", "head_sha": fixture.head,
        "green_dev_sha": fixture.head, "dev_ci": {"conclusion": "success"},
        "delivery": {"number": 1, "head_sha": fixture.head, "merged": True,
                     "html_url": "https://example.invalid/offline/pr/1", "merge_commit_sha": fixture.head}}, "simulated-local-ci")
    controller = Lifecycle(fixture.primary, "offline", backend)
    blocker = fixture.worktree / "build/unknown-owned-evidence.log"
    blocker.parent.mkdir(exist_ok=True); blocker.write_text("preserve until classified\n", encoding="utf-8")
    retained_payload = blocker.read_bytes()
    blocked = _expect_rejected(controller.cleanup)
    preserved = blocker.exists() and fixture.worktree.exists() and backend.remote_head("dev-offline") == fixture.head
    # Resolve only our known fixture artifact into the canonical archive directory.
    retained = fixture.worktree / "build/verification/recovered.log"
    retained.parent.mkdir(parents=True); blocker.replace(retained)
    cleaned = controller.cleanup()
    archive = cleaned["archive"]
    manifest = verify_archive(Path(archive["path"]), archive["sha256"])
    source_entry = next(item for item in manifest["files"] if item["path"] == "worktree/build/verification/recovered.log")
    with zipfile.ZipFile(archive["path"]) as saved:
        restored = saved.read(source_entry["path"])
    before = _files(fixture.store.root / "tasks")
    note(fixture.primary, "offline", "after-cleanup", "rework", "Observed recovery complete")
    note_before = _files(fixture.store.root / "tasks")
    note(fixture.primary, "offline", "after-cleanup", "rework", "Observed recovery complete")
    from task_observations import manual_summary
    return {"unclassified_file_blocks_and_preserves_resources": blocked and preserved,
            "cleanup_resumes_to_cleaned": cleaned["status"] == "CLEANED",
            "remote_local_tracking_refs_absent": backend.remote_head("dev-offline") is None
                and not _git(fixture.primary, "for-each-ref", "refs/heads/dev-offline", "refs/remotes/origin/dev-offline").strip(),
            "directory_registration_absent": not fixture.worktree.exists()
                and all(row["path"] != fixture.worktree for row in worktrees(fixture.primary)),
            "archive_restores_exact_evidence": hashlib.sha256(restored).hexdigest() == source_entry["sha256"]
                and restored == retained_payload,
            "post_cleanup_note_does_not_recreate_workspace": not fixture.worktree.exists()
                and before != note_before and note_before == _files(fixture.store.root / "tasks"),
            "unknown_totals_preserved": all(value is None for value in manual_summary(fixture.store.load("offline"))["totals"].values()),
            "one_remote_delete": backend.remote_deletes == 1, "primary_preserved": fixture.unchanged_primary()}


SCENARIOS = {"scope-and-local-implementation": scope_scenario, "partial-evidence": partial_scenario,
             "context-and-events": context_scenario, "cleanup-recovery": cleanup_scenario}


def fixture_worker(directory: Path, scenario: str):
    directory = owned_directory(directory)
    started = time.monotonic()
    result = {"scenario": scenario, "status": "FAIL", "assertions": {}, "model_calls": 0}
    try:
        fixture = Fixture(directory)
        result["assertions"] = SCENARIOS[scenario](fixture)
        result["status"] = "PASS" if result["assertions"] and all(result["assertions"].values()) else "FAIL"
    except Exception as error:
        result["diagnostic"] = type(error).__name__ + ": " + str(error)[:500]
    result["duration_seconds"] = time.monotonic() - started
    write_json(directory / "result.json", result)
    return result


def run_offline(root: Path = ROOT, *, codex_bin: str | None = None, include_config=True):
    original = source_state(root)
    scenarios = []
    for scenario in sorted(SCENARIOS):
        with tempfile.TemporaryDirectory(prefix="tenon-skill-offline-") as temporary:
            directory = Path(temporary).resolve()
            environment = child_environment(directory / "child")
            try:
                process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--fixture-worker", str(directory),
                    "--scenario", scenario], cwd=directory, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
                path = directory / "result.json"
                result = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
                    "scenario": scenario, "status": "FAIL", "diagnostic": "Worker produced no report", "assertions": {}}
                if process.returncode:
                    result["status"] = "FAIL"
                    result.setdefault("diagnostic", "Worker failed; no raw process output retained")
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                result = {"scenario": scenario, "status": "FAIL", "assertions": {},
                          "diagnostic": "Worker transport/report failure (" + type(error).__name__ + ")"}
            scenarios.append(result)
    config = configuration_check(codex_bin) if include_config else {"status": "NOT_RUN", "diagnostic": "Explicitly excluded", "model_calls": 0}
    after = source_state(root)
    return {"schema_version": 1, "kind": "skill-offline-acceptance",
            "status": "PASS" if all(row["status"] == "PASS" for row in scenarios) and config["status"] != "FAIL" and original == after else "FAIL",
            "source": original, "source_after": after, "source_unchanged": original == after, "completed_at": utc_now(),
            "model_regression_corpus": {"status": "NOT_RUN", "case_ids": [case["id"] for case in load_cases(root)]},
            "offline_scenario_count": len(scenarios), "scenarios": scenarios, "configuration": config,
            "model_calls": 0, "model_metrics": {"status": "NOT_RUN", "trigger_accuracy": None, "completion_rate": None, "usage": None},
            "qualification": "Scripted local workflow assertions, not LLM routing/completion accuracy. Local bare remotes only; PR/CI snapshots in cleanup are simulated. No hosted delivery, hardware, signoff or release evidence."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--codex-bin")
    parser.add_argument("--skip-config", action="store_true", help="Report configuration integration NOT_RUN explicitly")
    parser.add_argument("--fixture-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.fixture_worker:
        if not args.scenario:
            parser.error("Fixture worker requires a scenario")
        return 0 if fixture_worker(args.fixture_worker, args.scenario)["status"] == "PASS" else 1
    report = (args.report or ROOT / "build/verification/skill-offline/results.json").resolve()
    boundary = (ROOT / "build/verification").resolve()
    if not boundary.is_relative_to(ROOT.resolve()) or not report.is_relative_to(boundary) or report == boundary:
        parser.error("Offline report must remain inside build/verification")
    tracked = _git(ROOT, "ls-files", "--", str(report)).strip()
    if tracked:
        parser.error("Offline report cannot overwrite tracked source")
    result = run_offline(codex_bin=args.codex_bin, include_config=not args.skip_config)
    write_json(report, result)
    print(json.dumps({"status": result["status"], "report": str(report), "model_calls": 0,
                      "configuration": result["configuration"], "model_metrics": result["model_metrics"]}, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
