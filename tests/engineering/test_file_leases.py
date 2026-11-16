"""Native first-use file locks and non-mutating probes, including empty Windows files."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from test_engineering import ROOT
import task_store
from task_store import FileLease, lease_is_held


class FileLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tenon-lease-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_empty_file_is_locked_before_metadata_write(self):
        path = self.root/"empty.lock"
        path.touch()
        native = task_store._native_lock
        sizes = []
        def acquire(handle):
            sizes.append(path.stat().st_size)
            return native(handle)
        with mock.patch.object(task_store,"_native_lock",side_effect=acquire):
            with FileLease(path):
                self.assertEqual(sizes,[0])
                self.assertTrue(lease_is_held(path))
        self.assertFalse(lease_is_held(path))
        self.assertEqual(json.loads(path.read_text().strip())["platform"],os.name)

    def test_probe_never_creates_or_changes_empty_or_owner_metadata(self):
        missing = self.root/"not-created/lease.lock"
        self.assertFalse(lease_is_held(missing))
        self.assertFalse(missing.parent.exists())
        empty = self.root/"empty.lock"
        empty.touch()
        before = empty.stat().st_mtime_ns
        self.assertFalse(lease_is_held(empty))
        self.assertEqual(empty.read_bytes(),b"")
        self.assertEqual(empty.stat().st_mtime_ns,before)
        owned = self.root/"owned.lock"
        with FileLease(owned) as lease:
            lease.handle.seek(0)
            payload, modified = lease.handle.read(),owned.stat().st_mtime_ns
            self.assertTrue(lease_is_held(owned))
            lease.handle.seek(0)
            self.assertEqual(lease.handle.read(),payload)
            self.assertEqual(owned.stat().st_mtime_ns,modified)
        self.assertFalse(lease_is_held(owned))
        self.assertEqual(owned.read_bytes(),payload)

    def test_probe_access_denied_is_conservatively_held(self):
        with mock.patch.object(os,"open",side_effect=PermissionError("fixture access denied")):
            self.assertTrue(lease_is_held(self.root/"unavailable.lock"))

    def test_thread_first_use_is_serial_and_never_leaks_handles(self):
        path = self.root/"first-use.lock"
        barrier = threading.Barrier(8)
        guard = threading.Lock()
        active = maximum = 0
        def acquire(index):
            nonlocal active,maximum
            barrier.wait()
            with FileLease(path):
                with guard:
                    active += 1
                    maximum = max(maximum,active)
                time.sleep(0.01)
                with guard:
                    active -= 1
            return index
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sorted(pool.map(acquire,range(8))),list(range(8)))
        self.assertEqual(maximum,1)
        path.unlink()
        self.assertFalse(path.exists())

    def test_metadata_failure_and_unlock_failure_close_the_owned_handle(self):
        path = self.root/"failure.lock"
        lease = FileLease(path,kind=object())
        with self.assertRaises(TypeError):
            lease.__enter__()
        self.assertTrue(lease.handle.closed)
        self.assertFalse(lease_is_held(path))

    def test_fd_wrapper_failure_closes_the_raw_descriptor(self):
        path = self.root/"fdopen-failure.lock"
        with mock.patch.object(os,"fdopen",side_effect=OSError("fixture wrapper failure")):
            with self.assertRaises(OSError):
                FileLease(path).__enter__()
            self.assertTrue(lease_is_held(path))
        self.assertFalse(lease_is_held(path))
        path.unlink()
        lease = FileLease(path)
        lease.__enter__()
        with mock.patch.object(task_store,"_native_unlock",side_effect=OSError("fixture unlock failure")):
            with self.assertRaises(OSError):
                lease.__exit__(None,None,None)
        self.assertTrue(lease.handle.closed)
        self.assertFalse(lease_is_held(path))

    def test_timeout_does_not_rewrite_the_current_owner(self):
        path = self.root/"busy.lock"
        with FileLease(path) as owner:
            owner.handle.seek(0)
            before = owner.handle.read()
            other = FileLease(path,timeout=0)
            with self.assertRaises(TimeoutError):
                other.__enter__()
            self.assertTrue(other.handle.closed)
            owner.handle.seek(0)
            self.assertEqual(owner.handle.read(),before)

    def test_separate_process_first_use_and_probe_use_native_ownership(self):
        path = self.root/"process.lock"
        signal = self.root/"start"
        script = ("import sys,time,os\nfrom pathlib import Path\nsys.path.insert(0,"+repr(str(ROOT/"scripts"))+")\n"
                  "from task_store import FileLease\np=Path(sys.argv[1]); gate=Path(sys.argv[2]); name=sys.argv[3]\n"
                  "while not gate.exists(): time.sleep(0.01)\n"
                  "with FileLease(p):\n marker=p.with_suffix('.inside')\n descriptor=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY)\n print('HELD',flush=True)\n time.sleep(0.05)\n os.close(descriptor)\n marker.unlink()\n")
        options = {"creationflags":subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {"start_new_session":True}
        processes = [subprocess.Popen([sys.executable,"-c",script,str(path),str(signal),str(index)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,**options) for index in range(4)]
        signal.touch()
        try:
            for process in processes:
                output,error = process.communicate(timeout=20)
                self.assertEqual(process.returncode,0,error.decode(errors="replace"))
                self.assertIn(b"HELD",output)
        finally:
            from engineering_common import stop_process
            for process in processes:
                if process.poll() is None:
                    stop_process(process)
        self.assertFalse(lease_is_held(path))
        self.assertTrue(json.loads(path.read_text().strip())["token"])


if __name__=="__main__":
    unittest.main()
