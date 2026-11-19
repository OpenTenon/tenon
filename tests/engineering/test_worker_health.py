"""Resident health regressions use isolated records and read-only native probes."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from worker_health import inspect_resident, _argv


class ResidentHealthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.primary = Path(self.temporary.name).resolve()
        self.base = self.primary / ".git/tenon"
        self.repo_id = "a" * 32
        self.script = self.base / "host/resident_worker.py"
        self.script.parent.mkdir(parents=True)
        self.script.write_bytes(b"# isolated resident; never executed\n")
        self.python = self.primary / "python.exe"
        self.python.write_bytes(b"fixture interpreter")
        self.python.with_name("pythonw.exe").write_bytes(b"fixture windowless interpreter")
        self.installation = {"repo_id": self.repo_id, "repo": str(self.primary),
                             "python_executable": str(self.python)}
        self.now = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)
        self.heartbeat = {"schema_version": 1, "repo_id": self.repo_id, "pid": 123,
                          "state": "IDLE", "at": self.now.isoformat()}
        self.heartbeat_path = self.base / "operations/resident-worker.json"
        self.schedule = {"exists": True, "enabled": False, "description": "Tenon owned queue " + self.repo_id}
        self.observed = {"status": "OBSERVED", "current_sid": "owner",
            "startup": {"exists": True, "path": "owned.lnk", "executable": str(self.python.with_name("pythonw.exe")),
                        "argv": ["-B", str(self.script)], "working_directory": str(self.primary),
                        "description": "Tenon owned resident queue " + self.repo_id},
            "process": {"pid": 123, "owner_sid": "owner", "executable": str(self.python.with_name("pythonw.exe")),
                        "argv": [str(self.python.with_name("pythonw.exe")), "-B", str(self.script)],
                        "started_at": (self.now - timedelta(minutes=1)).isoformat()}}

    def write_heartbeat(self):
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(json.dumps(self.heartbeat), encoding="utf-8")

    def inspect(self):
        return inspect_resident(self.primary, self.base, self.repo_id, self.installation,
            schedule_probe=lambda _: copy.deepcopy(self.schedule),
            process_probe=lambda *_: copy.deepcopy(self.observed), now=self.now)

    def test_idle_resident_is_ready_and_observation_preserves_files_and_disabled_schedule(self):
        self.heartbeat["at"] = self.now.isoformat().replace("+00:00", "Z")
        self.write_heartbeat()
        before = {path: path.read_bytes() for path in self.primary.rglob("*") if path.is_file()}
        result = self.inspect()
        self.assertEqual(result["status"], "READY", result)
        self.assertTrue(result["process"]["identity_verified"])
        self.assertFalse(result["scheduler"]["enabled"])
        self.assertEqual(before, {path: path.read_bytes() for path in self.primary.rglob("*") if path.is_file()})
        self.assertFalse(self.schedule["enabled"])

    def test_absent_resident_keeps_the_legacy_health_path(self):
        self.script.unlink()
        self.assertIsNone(self.inspect())

    def test_missing_or_foreign_heartbeat_blocks_resident_mode(self):
        self.assertEqual(self.inspect()["status"], "BLOCKED")
        for field, value in (("repo_id", "foreign"), ("schema_version", 2), ("pid", True)):
            with self.subTest(field=field):
                before = self.heartbeat[field]
                self.heartbeat[field] = value
                self.write_heartbeat()
                self.assertEqual(self.inspect()["status"], "BLOCKED")
                self.heartbeat[field] = before

    def test_dead_foreign_and_reused_processes_are_rejected(self):
        self.write_heartbeat()
        original = copy.deepcopy(self.observed["process"])
        invalid = [None, {**original, "owner_sid": "foreign"},
            {**original, "executable": str(self.python)},
            {**original, "argv": [str(self.python.with_name("pythonw.exe")), "-B", "foreign.py"]},
            {**original, "started_at": (self.now + timedelta(seconds=1)).isoformat()}]
        for process in invalid:
            with self.subTest(process=process):
                self.observed["process"] = process
                self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_stale_future_and_unqualified_clocks_are_rejected(self):
        for at in (self.now - timedelta(seconds=181), self.now + timedelta(seconds=6),
                   self.now.replace(tzinfo=None)):
            with self.subTest(at=at):
                self.heartbeat["at"] = at.isoformat()
                self.write_heartbeat()
                self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_missing_or_changed_login_entry_is_rejected(self):
        self.write_heartbeat()
        original = copy.deepcopy(self.observed["startup"])
        invalid = [{**original, "exists": False}, {**original, "working_directory": str(self.base)},
            {**original, "argv": ["-B", str(self.script), "extra"]},
            {**original, "description": "unowned"}, {**original, "executable": str(self.python)}]
        for startup in invalid:
            with self.subTest(startup=startup):
                self.observed["startup"] = startup
                self.assertEqual(self.inspect()["status"], "BLOCKED")

    def working(self):
        self.heartbeat.update(state="WORKING", child_pid=456,
                              at=(self.now - timedelta(minutes=5)).isoformat())
        self.observed["process"]["started_at"] = (self.now - timedelta(hours=1)).isoformat()
        self.observed["child"] = {"pid": 456, "parent_pid": 123, "owner_sid": "owner",
            "executable": str(self.python),
            "argv": [str(self.python), "-B", str(self.base / "bin/tenon.py"),
                     "worker", "run", "--max-seconds", "900"],
            "started_at": (self.now - timedelta(minutes=6)).isoformat()}
        self.write_heartbeat()

    def test_live_owned_delivery_child_qualifies_an_older_working_heartbeat(self):
        self.working()
        result = self.inspect()
        self.assertEqual(result["status"], "READY", result)
        self.assertEqual(result["process"]["child_pid"], 456)

    def test_dead_wrong_parent_and_foreign_delivery_children_block(self):
        self.working()
        original = copy.deepcopy(self.observed["child"])
        for child in (None, {**original, "parent_pid": 999}, {**original, "owner_sid": "foreign"},
                      {**original, "argv": [str(self.python), "-B", "other.py"]}):
            with self.subTest(child=child):
                self.observed["child"] = child
                self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_live_retry_or_disabled_resident_requires_attention(self):
        for state in ("RETRY", "DISABLED"):
            with self.subTest(state=state):
                self.heartbeat["state"] = state
                self.write_heartbeat()
                result = self.inspect()
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIn(state, result["reason"])

    def test_enabled_or_foreign_legacy_schedule_blocks_without_repair(self):
        self.write_heartbeat()
        self.schedule["enabled"] = True
        result = self.inspect()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(self.schedule["enabled"])
        self.schedule.update(enabled=False, description="foreign")
        self.assertEqual(self.inspect()["status"], "BLOCKED")
        self.schedule = {"exists": False}
        self.assertEqual(self.inspect()["status"], "READY")

    def test_unavailable_native_observation_stays_unknown(self):
        self.write_heartbeat()
        self.observed = {"status": "UNKNOWN", "reason": "native process inspection unavailable"}
        self.assertEqual(self.inspect()["status"], "UNKNOWN")

    @unittest.skipUnless(os.name == "nt", "Windows command-line parsing requires shell32")
    def test_native_argument_parser_preserves_spaces_and_rejectable_extra_arguments(self):
        self.assertEqual(_argv('"C:\\Python Tools\\pythonw.exe" -B "C:\\work tree\\resident.py"'),
                         ["C:\\Python Tools\\pythonw.exe", "-B", "C:\\work tree\\resident.py"])
        self.assertEqual(_argv('"entry" -B "script.py" extra')[1:], ["-B", "script.py", "extra"])

