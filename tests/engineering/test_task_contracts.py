"""Real-Git task contract allocation and compatibility; no remote services required."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from test_engineering import ROOT, RepositoryFixture
import engineering
import engineering_common as common
import task
import task_contract as contract
from task_store import TaskStore


class ContractFixture(RepositoryFixture):
    def setUp(self):
        super().setUp()
        (self.root/"scripts").mkdir()
        for name in ("a.py","b.py","c.py","build_ics55_manual_pdk.py"):
            (self.root/"scripts"/name).write_text("# fixture\n",encoding="utf-8")
        (self.root/"AGENTS.md").write_text("# Protected hardware rules\nKeep the rails separate.\n\n## Agent Engineering Workflow\nExisting workflow.\n",encoding="utf-8")
        self.run_git("add",".")
        self.run_git("commit","--quiet","-m","contract fixture inputs")
        self.run_git("update-ref","refs/remotes/origin/dev","HEAD")
        self.base = self.run_git("rev-parse","HEAD").decode().strip()
        self.store = TaskStore(self.root)

    def workspace(self, name):
        root = self.root/"build/workspaces"/name
        root.parent.mkdir(parents=True,exist_ok=True)
        self.run_git("worktree","add","--quiet","-b","dev-"+name+"-contract",str(root),"origin/dev")
        return root

    def start(self, name, scope, **options):
        root = self.workspace(name)
        checks = options.pop("checks", [[sys.executable, "-c", "print('isolated evidence fixture')"]]
                             if options.get("role") in contract.READERS else ["make verify"])
        value = task.start(name,"Bounded engineering fixture",scope,checks,[],root,kind="engineering",**options)
        return root,value

    def evidence(self, root, *, full=True):
        value = common.new_summary(root,"verification" if full else "review")
        folder = root/"build/verification"/value["run_id"]
        folder.mkdir(parents=True,exist_ok=True)
        names = sorted(engineering.required_ids("all")-{"repository-unchanged"}) if full else ["review-inputs"]
        for index,name in enumerate(names):
            value["checks"].append(common.execute([sys.executable,"-c","print('isolated evidence fixture')"],root,
                folder/(str(index)+".log"),check_id=name))
        report = folder/"summary.json"
        common.finish_summary(value,root,report)
        return report

    def seed_dependency(self, name, *, green=True, dependencies=None):
        return self.store.create({"task_id":name,"worktree":str(self.root/"build/retired"/name),
            "branch":"dev-"+name+"-old","status":"CLEANED" if green else "ACTIVE","dependencies":dependencies or [],
            "delivery":{"merged":green,"head_sha":self.base,"merge_commit_sha":self.base},
            "dev_ci":{"conclusion":"success" if green else "cancelled","head_sha":self.base},"green_dev_sha":self.base})


class ContractTests(ContractFixture):
    def test_normalized_paths_and_engineering_boundaries_precede_git_resource_creation(self):
        refs = self.run_git("for-each-ref")
        worktrees = self.run_git("worktree","list","--porcelain")
        value = contract.preflight(self.store,"normalized",["./scripts//a.py",r"scripts\a.py"],["make verify"],[])
        self.assertEqual(value["allowed_paths"],["scripts/a.py"])
        for scope in (["*"],["rtl/design.sv"],["specs/tier0_profiles.json"],["scripts/ics55.map"],
                      ["scripts/build_ics55_manual_pdk.py"],["../outside"],["C:relative"],["/absolute"],["scripts/../x.py"]):
            with self.subTest(scope=scope),self.assertRaises(ValueError):
                contract.preflight(self.store,"invalid",scope,["make verify"],[])
        self.assertEqual(refs,self.run_git("for-each-ref"))
        self.assertEqual(worktrees,self.run_git("worktree","list","--porcelain"))

    def test_primary_writer_rejected_but_legacy_registration_unchanged(self):
        with self.assertRaises(ValueError):
            task.start("strict-primary","fixture",["scripts/a.py"],["make verify"],[],self.root,kind="engineering")
        value = task.start("legacy","fixture",["*"],["make verify"],[],self.root)
        self.assertNotIn("kind",value)
        self.assertNotIn("execution_lease",value)
        self.assertEqual(value["allowed_paths"],["*"])

    def test_broad_script_scope_never_authorizes_pdk_installer_or_product_files(self):
        root,value = self.start("bounded",["scripts/*"])
        (root/"scripts/build_ics55_manual_pdk.py").write_text("# forbidden PDK change\n",encoding="utf-8")
        (root/"outside.sv").write_text("// out of scope\n",encoding="utf-8")
        self.assertEqual(task.scope_errors(value,root),["outside.sv","scripts/build_ics55_manual_pdk.py"])

    def test_only_agents_engineering_section_can_change(self):
        root,value = self.start("agent-guide",["AGENTS.md"])
        source = root/"AGENTS.md"
        original = source.read_text(encoding="utf-8")
        source.write_text(original.replace("Existing workflow.","Updated engineering workflow."),encoding="utf-8")
        self.assertEqual(task.scope_errors(value,root),[])
        source.write_text(original.replace("Keep the rails separate.","Merge the rails."),encoding="utf-8")
        self.assertEqual(task.scope_errors(value,root),["AGENTS.md"])

    def test_allocation_ticket_spans_preflight_workspace_and_registration_without_recursion(self):
        with contract.allocation_lock(self.store) as ticket:
            prepared = contract.preflight(self.store,"atomic",["scripts/a.py"],["make verify"],[],allocation=ticket)
            root = self.workspace("atomic")
            with mock.patch.object(contract.Allocation,"__enter__",side_effect=AssertionError("recursive allocation lock")):
                value = task.start("atomic","fixture",prepared["allowed_paths"],["make verify"],[],root,
                                   kind="engineering",allocation=ticket)
        self.assertEqual(value["kind"],"engineering")
        with self.assertRaises(ValueError):
            contract.preflight(self.store,"expired",["scripts/b.py"],["make verify"],[],allocation=ticket)

    def test_registration_rechecks_after_unlocked_preflight(self):
        contract.preflight(self.store,"racing",["scripts/a.py"],["make verify"],[])
        self.start("winner",["scripts/a.py"])
        root = self.workspace("racing")
        with self.assertRaises(contract.AllocationBusy):
            task.start("racing","fixture",["scripts/a.py"],["make verify"],[],root,kind="engineering")
        self.assertFalse(self.store.path("racing").exists())

    def test_two_disjoint_writers_and_read_only_reviewer_share_the_pool(self):
        self.start("first",["scripts/a.py"])
        self.start("second",["scripts/b.py"])
        root,value = self.start("reviewer",[],role="reviewer")
        self.assertEqual(value["delivery_mode"],"local-only")
        self.assertEqual(value["execution_lease"]["role"],"reviewer")
        with self.assertRaises(contract.AllocationBusy):
            contract.preflight(self.store,"third",["scripts/c.py"],["make verify"],[])
        (root/"scripts/a.py").write_text("# prohibited reviewer edit\n",encoding="utf-8")
        self.assertEqual(task.scope_errors(value,root),["Frozen analysis source changed"])

    def test_concurrent_registrations_never_exceed_two_writers(self):
        roots = [self.workspace("parallel-"+str(index)) for index in range(3)]
        def create(index):
            try:
                return task.start("parallel-"+str(index),"fixture",["scripts/"+"abc"[index]+".py"],
                    ["make verify"],[],roots[index],kind="engineering")
            except (ValueError,TimeoutError) as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=3) as pool:
            values = list(pool.map(create,range(3)))
        self.assertEqual(sum(isinstance(value,dict) for value in values),2)
        self.assertEqual(len(self.store.all()),2)

    def test_separate_host_processes_share_the_same_writer_limit(self):
        roots = [self.workspace("process-"+str(index)) for index in range(3)]
        script = ("import sys\nfrom pathlib import Path\nsys.path.insert(0,"+repr(str(ROOT/"scripts"))+")\nimport task\n"
                  "try:\n task.start(sys.argv[1],'fixture',[sys.argv[2]],['make verify'],[],Path(sys.argv[3]),kind='engineering')\n"
                  "except (ValueError,TimeoutError):\n raise SystemExit(2)\n")
        options = {"creationflags":subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {"start_new_session":True}
        processes = [subprocess.Popen([sys.executable,"-c",script,"process-"+str(index),"scripts/"+"abc"[index]+".py",str(root)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,**options) for index,root in enumerate(roots)]
        codes = []
        try:
            for process in processes:
                _,diagnostic = process.communicate(timeout=30)
                self.assertIn(process.returncode,{0,2},diagnostic.decode(errors="replace"))
                codes.append(process.returncode)
        finally:
            for process in processes:
                if process.poll() is None:
                    common.stop_process(process)
        self.assertEqual(codes.count(0),2)
        self.assertEqual(codes.count(2),1)
        self.assertEqual(len(self.store.all()),2)

    def test_overlap_is_conservative_case_insensitive_and_integrator_is_unique(self):
        self.assertTrue(contract.scopes_overlap(["scripts/A.py"],["scripts/a.py"]))
        self.assertTrue(contract.scopes_overlap(["scripts/a*.py"],["scripts/ab*.sh"]))
        self.assertFalse(contract.scopes_overlap(["scripts/a*.py"],["scripts/b*.py"]))
        self.start("integrator",["scripts/a.py"],role="integrator")
        with self.assertRaises(contract.AllocationBusy):
            contract.preflight(self.store,"another-integrator",["scripts/b.py"],["make verify"],[],role="integrator")
        with self.assertRaises(contract.AllocationBusy):
            contract.preflight(self.store,"overlap",["scripts/*.py"],["make verify"],[])

    def test_pause_completion_and_owned_reacquisition_respect_scope_and_slots(self):
        _,first = self.start("paused",["scripts/a.py"])
        self.start("other",["scripts/b.py"])
        contract.set_lease_state(self.store,"paused","paused",owner_id=first["owner_id"])
        with self.assertRaises(contract.AllocationBusy):
            contract.preflight(self.store,"overlap-paused",["scripts/a.py"],["make verify"],[])
        _,third = self.start("replacement",["scripts/c.py"])
        with self.assertRaises(contract.AllocationBusy):
            contract.set_lease_state(self.store,"paused","active",owner_id=first["owner_id"])
        with self.assertRaises(ValueError):
            contract.set_lease_state(self.store,"replacement","completed",owner_id="foreign-owner")
        contract.set_lease_state(self.store,"replacement","completed",owner_id=third["owner_id"])
        restored = contract.set_lease_state(self.store,"paused","active",owner_id=first["owner_id"])
        self.assertEqual(restored["execution_lease"]["state"],"active")

    def test_merged_task_cannot_reacquire_writing_and_no_longer_occupies_a_slot(self):
        _,value = self.start("closed",["scripts/a.py"])
        self.store.mutate("closed",lambda row:{**row,"delivery":{"merged":True}},"merged-fixture")
        with self.assertRaises(ValueError):
            contract.set_lease_state(self.store,"closed","active",owner_id=value["owner_id"])
        prepared = contract.preflight(self.store,"new-task",["scripts/a.py"],["make verify"],[])
        self.assertEqual(prepared["role"],"writer")

    def test_missing_pending_duplicate_and_cyclic_dependencies_are_blocked(self):
        self.seed_dependency("pending",green=False)
        self.seed_dependency("cycle-a",dependencies=["cycle-b"])
        self.seed_dependency("cycle-b",dependencies=["cycle-a"])
        for dependencies in (["missing"],["pending"],["pending","pending"],["cycle-a"],["candidate"]):
            with self.subTest(dependencies=dependencies),self.assertRaises(ValueError):
                contract.preflight(self.store,"candidate",["scripts/a.py"],["make verify"],dependencies)

    def test_completed_dependency_requires_real_current_dev_ancestry(self):
        self.seed_dependency("ready")
        prepared = contract.preflight(self.store,"dependent",["scripts/a.py"],["make verify"],["ready"])
        self.assertEqual(prepared["dependencies"],["ready"])
        self.store.mutate("ready",lambda value:{**value,"dev_ci":{"conclusion":"success","head_sha":"0"*40}},"bad-proof-fixture")
        with self.assertRaises(ValueError):
            contract.preflight(self.store,"dependent",["scripts/a.py"],["make verify"],["ready"])

    def test_deep_dependency_graph_is_iterative(self):
        records = [{"task_id":"n"+str(index),"dependencies":["n"+str(index+1)] if index<1499 else [],
            "delivery":{"merged":True,"head_sha":self.base},"dev_ci":{"conclusion":"success","head_sha":self.base}} for index in range(1500)]
        with mock.patch.object(contract,"_ancestor",return_value=True):
            self.assertEqual(contract.dependency_errors(records,"candidate",["n0"],self.root,self.base),[])

    def test_input_hashes_read_scope_and_contract_immutability_are_recorded(self):
        root,value = self.start("input",["scripts/a.py"],read_scope=["scripts/*"],inputs=["scripts/a.py"],outputs=["scripts/a.py"])
        entry = value["input_contract"]["inputs"][0]
        self.assertEqual(entry["sha256"],hashlib.sha256((root/"scripts/a.py").read_bytes()).hexdigest())
        with self.assertRaises(ValueError):
            contract.preflight(self.store,"bad-input",["scripts/b.py"],["make verify"],[],read_scope=["docs/*"],inputs=["scripts/b.py"])
        changed = self.store.mutate("input",lambda row:{**row,"allowed_paths":["scripts/*"]},"tamper-scope-fixture")
        self.assertTrue(task.scope_errors(changed,root))
        with self.assertRaises(ValueError):
            contract.set_lease_state(self.store,"input","active",owner_id=value["owner_id"])

    def test_integration_requires_fresh_base_recorded_head_and_active_write_authority(self):
        root,value = self.start("candidate",["scripts/a.py"])
        self.assertEqual(contract.integration_errors(self.store,value,current_dev=self.base),[])
        (self.root/"scripts/c.py").write_text("# advanced dev fixture\n",encoding="utf-8")
        self.run_git("add","scripts/c.py")
        self.run_git("commit","--quiet","-m","advanced dev fixture")
        self.run_git("update-ref","refs/remotes/origin/dev","HEAD")
        self.assertTrue(contract.integration_errors(self.store,value,current_dev=self.base))
        subprocess.run(["git","-C",str(root),"merge","--no-edit","origin/dev"],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        value = self.store.mutate("candidate", lambda row: {**row, "base_sha": self.run_git("rev-parse", "origin/dev").decode().strip()})
        self.assertEqual(contract.integration_errors(self.store,value),[])
        paused = contract.set_lease_state(self.store,"candidate","paused",owner_id=value["owner_id"])
        self.assertTrue(contract.integration_errors(self.store,paused))
        observed = {**value,"head_sha":"0"*40}
        self.assertTrue(any("head differs" in error for error in contract.integration_errors(self.store,observed)))

    def test_writer_handoff_records_outputs_and_releases_slot_after_source_matched_evidence(self):
        root,value = self.start("handoff",["scripts/a.py"],inputs=["scripts/a.py"],outputs=["scripts/a.py"])
        (root/"scripts/a.py").write_text("# scoped result\n",encoding="utf-8")
        evidence = self.evidence(root)
        result = task.handoff("handoff",[evidence],[],None,None,root)
        self.assertEqual(result["status"],"READY")
        self.assertEqual(result["execution_lease"]["state"],"completed")
        self.assertEqual(result["output_contract"]["qualification"],"candidate-verification")
        self.assertEqual(result["output_contract"]["changed_files"],["scripts/a.py"])
        self.assertTrue(result["output_contract"]["outputs"][0]["sha256"])
        self.assertEqual(result["input_contract"],value["input_contract"])

    def test_read_only_handoff_is_analysis_qualified_and_cannot_gain_write_capability(self):
        root,value = self.start("analysis",[],role="coordinator",outputs=["build/review.json"])
        (root/"build").mkdir(exist_ok=True)
        (root/"build/review.json").write_text('{"finding":"fixture"}',encoding="utf-8")
        report = self.evidence(root,full=False)
        result = task.handoff("analysis",[report],[],None,None,root)
        self.assertEqual(result["status"],"READY")
        self.assertEqual(result["output_contract"]["qualification"],"analysis-only")
        with self.assertRaises(ValueError):
            contract.set_lease_state(self.store,"analysis","active",owner_id=value["owner_id"],role="writer")
        self.assertTrue(contract.integration_errors(self.store,result))

    def test_missing_output_or_stale_evidence_preserves_active_writer(self):
        root,value = self.start("missing-output",["scripts/a.py"],outputs=["build/missing.json"])
        report = self.evidence(root)
        blocked = task.handoff("missing-output",[report],[],None,None,root)
        self.assertEqual(blocked["status"],"BLOCKED")
        self.assertEqual(blocked["execution_lease"]["state"],"active")
        (root/"scripts/a.py").write_text("# invalidates earlier evidence\n",encoding="utf-8")
        blocked = task.handoff("missing-output",[report],[],None,None,root)
        self.assertEqual(blocked["status"],"BLOCKED")
        self.assertTrue(blocked["blockers"])

    def test_analysis_handoff_requires_its_declared_executed_command(self):
        root, _ = self.start("analysis-command", [], role="reviewer",
            checks=[[sys.executable, "-c", "print('a different declared command')"]])
        report = self.evidence(root, full=False)
        result = task.handoff("analysis-command", [report], [], None, None, root)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("no executed evidence" in error for error in result["blockers"]))

    def test_handoff_does_not_overwrite_concurrently_recorded_verification_history(self):
        root,_ = self.start("history",[],role="reviewer")
        self.store.mutate("history",lambda value:{**value,"verification_attempts":[]},"history-fixture")
        report = self.evidence(root,full=False)
        validate = task.validate_evidence
        def observing(path,target):
            result = validate(path,target)
            self.store.mutate("history",lambda value:{**value,"verification_attempts":[{"run_id":"late-attempt"}]},"late-attempt-fixture")
            return result
        with mock.patch.object(task,"validate_evidence",side_effect=observing):
            value = task.handoff("history",[report],[],None,None,root)
        self.assertEqual(value["status"],"READY")
        self.assertEqual(value["verification_attempts"],[{"run_id":"late-attempt"}])


class IntegratedEntryTests(ContractFixture):
    def entry(self, name, scope, **options):
        import tenon
        from test_entrypoint import Backend
        return tenon.start_workspace(self.root,name,"fixture","Entry contract fixture",scope,["make verify"],
            workspace_root=self.root/"build/entry-workspaces",backend=Backend(),**options)

    def test_stable_default_is_engineering_and_rejects_invalid_scope_before_git_resources(self):
        value = self.entry("entry-default",["scripts/a.py"])
        self.assertEqual(value["kind"],"engineering")
        before = self.run_git("for-each-ref")
        with self.assertRaises(ValueError):
            self.entry("invalid-design",["rtl/*"])
        self.assertEqual(before,self.run_git("for-each-ref"))
        self.assertFalse((self.store.root/"operations/workspace-setup/invalid-design.json").exists())

    def test_workspace_preview_reads_normalized_contract_without_creating_resources(self):
        from test_entrypoint import Backend
        before = {path.relative_to(self.store.root): path.read_bytes()
                  for path in self.store.root.rglob("*") if path.is_file()}
        refs = self.run_git("for-each-ref")
        registered = self.run_git("worktree", "list", "--porcelain")
        with (mock.patch.object(Backend, "refresh", side_effect=AssertionError("preview contacted origin")),
              mock.patch("host_profile.bind_verifier", side_effect=AssertionError("preview bound a verifier"))):
            result = self.entry("preview", ["./scripts//a.py"], dry_run=True, inputs=["scripts/a.py"])
        self.assertEqual(result["status"], "READY")
        self.assertIs(result["dry_run"], True)
        self.assertEqual(result["base_sha"], self.base)
        self.assertEqual(result["contract"]["allowed_paths"], ["scripts/a.py"])
        self.assertEqual(result["acceptance_argv"], [["make", "verify"]])
        self.assertEqual(result["capacity"], {"limit": 2, "active_writers": [], "free": 2})
        self.assertFalse(Path(result["worktree"]).exists())
        self.assertEqual(refs, self.run_git("for-each-ref"))
        self.assertEqual(registered, self.run_git("worktree", "list", "--porcelain"))
        self.assertEqual(before, {path.relative_to(self.store.root): path.read_bytes()
                                  for path in self.store.root.rglob("*") if path.is_file()})
        actual = self.entry("preview", ["scripts/a.py"], inputs=["scripts/a.py"])
        self.assertEqual(actual["worktree"], result["worktree"])
        self.assertTrue(Path(actual["worktree"]).is_dir())

    def test_workspace_preview_refuses_conflicting_scope_and_dependencies_without_resource_writes(self):
        from test_entrypoint import Backend
        self.entry("scope-owner", ["scripts/a.py"])
        before = {path.relative_to(self.store.root): path.read_bytes()
                  for path in self.store.root.rglob("*") if path.is_file()}
        refs = self.run_git("for-each-ref")
        registered = self.run_git("worktree", "list", "--porcelain")
        with mock.patch.object(Backend, "refresh", side_effect=AssertionError("preview contacted origin")):
            with self.assertRaisesRegex(contract.AllocationBusy, "scope is reserved"):
                self.entry("scope-preview", ["scripts/a.py"], dry_run=True)
            with self.assertRaisesRegex(ValueError, "[Dd]ependenc"):
                self.entry("dependency-preview", ["scripts/b.py"], dry_run=True, dependencies=["missing-dependency"])
        self.assertEqual(refs, self.run_git("for-each-ref"))
        self.assertEqual(registered, self.run_git("worktree", "list", "--porcelain"))
        self.assertEqual(before, {path.relative_to(self.store.root): path.read_bytes()
                                  for path in self.store.root.rglob("*") if path.is_file()})

    def test_explicit_general_preserves_content_interfaces(self):
        value = self.entry("general",["rtl/*"],kind="general")
        self.assertEqual(value["kind"],"general")
        self.assertNotIn("execution_lease",value)
        self.assertEqual(value["allowed_paths"],["rtl/*"])

    def test_old_untagged_setup_intent_is_not_retroactively_classified(self):
        import tenon
        import json
        with mock.patch.object(TaskStore,"create",side_effect=OSError("fixture registration interruption")):
            with self.assertRaises(OSError):
                self.entry("old-setup",["scripts/*"],kind="general")
        path = self.store.root/"operations/workspace-setup/old-setup.json"
        value = json.loads(path.read_text())
        del value["contract"]["kind"]
        common.write_json(path,value)
        with self.assertRaises(ValueError):
            self.entry("old-setup",["scripts/*"],kind="engineering")
        resumed = self.entry("old-setup",["scripts/*"])
        self.assertNotIn("kind",resumed)
        self.assertNotIn("input_contract",resumed)

    def test_busy_capacity_creates_no_branch_workspace_or_setup_intent(self):
        self.start("busy-a",["scripts/a.py"])
        self.start("busy-b",["scripts/b.py"])
        before = self.run_git("for-each-ref")
        with self.assertRaises(contract.AllocationBusy):
            self.entry("busy-c",["scripts/c.py"])
        self.assertEqual(before,self.run_git("for-each-ref"))
        self.assertFalse((self.root/"build/entry-workspaces/busy-c").exists())
        self.assertFalse((self.store.root/"operations/workspace-setup/busy-c.json").exists())

    def test_reader_start_creates_no_git_resources_or_intent(self):
        before = self.run_git("for-each-ref"), self.run_git("worktree", "list", "--porcelain")
        for role in ("reviewer", "coordinator"):
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, "existing frozen source"):
                self.entry("analysis-" + role, [], role=role)
        self.assertEqual(before, (self.run_git("for-each-ref"), self.run_git("worktree", "list", "--porcelain")))

    def interrupted_start(self, name):
        real = task.start
        def advance(*args, **kwargs):
            (self.root / "scripts/c.py").write_text("# dev advanced during workspace creation\n")
            self.run_git("add", "scripts/c.py")
            self.run_git("commit", "--quiet", "-m", "advance startup base")
            self.run_git("update-ref", "refs/remotes/origin/dev", "HEAD")
            return real(*args, **kwargs)
        with mock.patch.object(task, "start", side_effect=advance), self.assertRaises(subprocess.CalledProcessError):
            self.entry(name, ["scripts/a.py"])
        return self.root / "build/entry-workspaces" / name

    def test_untouched_setup_fast_forwards_after_dev_advances_during_creation(self):
        import json
        root = self.interrupted_start("moving-base")
        value = self.entry("moving-base", ["scripts/a.py"])
        tip = self.run_git("rev-parse", "origin/dev").decode().strip()
        self.assertEqual(common.git(root, "rev-parse", "HEAD").decode().strip(), tip)
        self.assertEqual(value["base_sha"], tip)
        intent = json.loads((self.store.root / "operations/workspace-setup/moving-base.json").read_text())
        self.assertEqual(intent["base_history"], [{"from": self.base, "to": tip}])
        self.assertEqual(intent["state"], "REGISTERED")

    def test_advanced_setup_preserves_new_source_instead_of_fast_forwarding(self):
        root = self.interrupted_start("moving-with-edits")
        (root / "scripts/a.py").write_text("# preserve new user work\n")
        with self.assertRaisesRegex(ValueError, "workspace has changed"):
            self.entry("moving-with-edits", ["scripts/a.py"])
        self.assertEqual((root / "scripts/a.py").read_text(), "# preserve new user work\n")

    def test_analysis_on_existing_writer_snapshot_creates_no_resources_and_archives_output(self):
        from task_archive import archive_task, verify_archive, ownership_errors
        root, writer = self.start("source-owner", ["scripts/a.py"])
        before = self.run_git("for-each-ref"), self.run_git("worktree", "list", "--porcelain")
        analysis = task.start("source-review", "Inspect frozen source", [], [[sys.executable, "-c", "print('isolated evidence fixture')"]], [], root,
            kind="engineering", role="reviewer", outputs=["build/review.json"])
        self.assertIsNone(analysis["branch"])
        self.assertFalse(analysis["owns_workspace"])
        (root / "build").mkdir(exist_ok=True)
        (root / "build/review.json").write_text('{"result":"reviewed"}')
        evidence = self.evidence(root, full=False)
        closed = task.handoff("source-review", [evidence], [], None, None, root)
        self.assertEqual(closed["status"], "READY")
        self.assertEqual(closed["execution_lease"]["state"], "completed")
        self.assertEqual(closed["output_contract"]["qualification"], "analysis-only")
        writer = self.store.mutate("source-owner", lambda row: {**row, "head_sha": self.base})
        self.assertEqual(ownership_errors(self.store, writer), [])
        result = archive_task(self.store, writer)
        manifest = verify_archive(Path(result["path"]), result["sha256"])
        self.assertIn("worktree/build/review.json", {item["path"] for item in manifest["files"]})
        self.assertEqual(before, (self.run_git("for-each-ref"), self.run_git("worktree", "list", "--porcelain")))


class PendingBackend:
    name = "fixture/repo"
    def __init__(self, repo):
        self.repo,self.value,self.remote = repo,None,None
        self.pushes = 0
    def refresh(self): pass
    def find_pull(self, branch): return self.value
    def remote_head(self, branch): return self.remote
    def push(self, branch, head): self.remote=head; self.pushes+=1
    def create_pull(self, value):
        self.value={"number":7,"html_url":"https://example.invalid/fixture/pull/7","merged":False,
            "head":{"sha":value["head_sha"],"ref":value["branch"],"repo":{"full_name":self.name}},
            "base":{"ref":"dev","sha":common.git(self.repo,"rev-parse","origin/dev").decode().strip(),"repo":{"full_name":self.name}}}
        return self.value
    def latest_run(self,*args): return None


class IntegratedDeliveryTests(ContractFixture):
    def candidate(self, name="candidate", **options):
        from task_lifecycle import Lifecycle
        root,value = self.start(name,["scripts/a.py"], **options)
        (root/"scripts/a.py").write_text("# submitted fixture candidate\n",encoding="utf-8")
        common.git(root,"add","scripts/a.py")
        common.git(root,"commit","--quiet","-m","submitted fixture")
        head = common.git(root,"rev-parse","HEAD").decode().strip()
        report = self.evidence(root)
        self.store.mutate(name,lambda row:{**row,"head_sha":head,"verified_head":head,"verification_report":str(report)},"verified-fixture")
        backend = PendingBackend(self.root)
        return root,self.store.load(name),Lifecycle(self.root,name,backend),backend

    def test_ci_wait_releases_writer_capacity_but_reserves_the_interface(self):
        root,value,controller,_ = self.candidate()
        self.assertEqual(controller.deliver()["status"],"WAITING_CI")
        self.assertEqual(self.store.load("candidate")["execution_lease"]["state"],"paused")
        self.start("other-b",["scripts/b.py"])
        self.start("other-c",["scripts/c.py"])
        before = common.source_state(root)
        with self.assertRaises(contract.AllocationBusy):
            controller.deliver(allow_commit=False,expected_candidate=value["head_sha"])
        self.assertEqual(common.source_state(root),before)

    def test_actor_capacity_is_a_retryable_background_condition(self):
        import task_queue
        root,value,controller,_ = self.candidate()
        controller.deliver()
        task_queue.submit(self.root,"candidate")
        self.start("busy-b",["scripts/b.py"])
        self.start("busy-c",["scripts/c.py"])
        before = common.source_state(root)
        result = task_queue.reconcile(self.root,apply=True,factory=lambda *_:controller)
        self.assertEqual(result["tasks"][0]["state"],"WAITING")
        self.assertEqual(task_queue.read_queue(self.store,"candidate")["error_category"],"TRANSIENT")
        self.assertEqual(common.source_state(root),before)

    def test_background_never_commits_new_unsubmitted_changes(self):
        root,value,controller,backend = self.candidate()
        (root/"scripts/a.py").write_text("# preserve unsubmitted work\n",encoding="utf-8")
        with self.assertRaises(ValueError):
            controller.deliver(allow_commit=False,expected_candidate=value["head_sha"])
        self.assertEqual(common.git(root,"rev-parse","HEAD").decode().strip(),value["head_sha"])
        self.assertEqual(backend.pushes,0)
        self.assertIn("unsubmitted",(root/"scripts/a.py").read_text())

    def test_confirmed_merge_completes_actor_before_waiting_for_dev_ci(self):
        from test_lifecycle_v2 import green
        root,value,controller,backend = self.candidate()
        head=value["head_sha"]
        self.run_git("update-ref","refs/remotes/origin/dev",head)
        pull={"number":7,"merged":True,"base":{"ref":"dev"},"head":{"sha":head}}
        backend.pull=lambda *_:pull
        backend.latest_run=lambda sha,event,*_:green(sha,event) if event=="pull_request" else None
        self.store.mutate("candidate",lambda row:{**row,"delivery":{"merged":True,"head_sha":head,"number":7}},"confirmed-merge-fixture")
        self.assertEqual(controller.after_merge()["status"],"WAITING_DEV_CI")
        self.assertEqual(self.store.load("candidate")["execution_lease"]["state"],"completed")

    def test_advanced_dev_is_merged_then_checked_and_verified_as_a_new_candidate(self):
        root,value,controller,_ = self.candidate()
        (self.root/"scripts/b.py").write_text("# advanced dev\n",encoding="utf-8")
        self.run_git("add","scripts/b.py")
        self.run_git("commit","--quiet","-m","advance dev fixture")
        self.run_git("update-ref","refs/remotes/origin/dev","HEAD")
        observed=[]
        def verify(current,target,head):
            self.assertEqual(contract.integration_errors(self.store,current),[])
            observed.append(head)
            report=self.evidence(target)
            controller.update("verified-fixture",verified_head=head,verification_report=str(report))
        with mock.patch.object(controller,"verification",side_effect=verify):
            result=controller.deliver(allow_commit=False,expected_candidate=value["head_sha"])
        self.assertEqual(result["status"],"WAITING_CI")
        self.assertNotEqual(observed,[value["head_sha"]])
        self.assertEqual(self.store.load("candidate")["execution_lease"]["state"],"paused")

    def test_missing_declared_output_blocks_actual_delivery_before_push(self):
        root, _, controller, backend = self.candidate(outputs=["build/missing.json"])
        with self.assertRaisesRegex(ValueError, "Expected output is missing"):
            controller.deliver()
        self.assertEqual(backend.pushes, 0)
        self.assertTrue(root.exists())

    def test_declared_argv_executes_once_and_output_is_archived_not_discarded(self):
        from task_archive import archive_task, verify_archive, ownership_errors
        command = [sys.executable, "-c", "from pathlib import Path; Path('build/review.json').write_text('qualified output')"]
        root, _, controller, backend = self.candidate(outputs=["build/review.json"], checks=["make verify", command])
        self.assertEqual(controller.deliver()["status"], "WAITING_CI")
        value = self.store.load("candidate")
        self.assertEqual(value["acceptance"]["outputs"][0]["sha256"], common.digest(root / "build/review.json"))
        report = value["acceptance"]["report"]["path"]
        self.assertEqual(controller.deliver()["status"], "WAITING_CI")
        self.assertEqual(self.store.load("candidate")["acceptance"]["report"]["path"], report)
        self.assertEqual(backend.pushes, 1)
        archived = archive_task(self.store, self.store.load("candidate"))
        manifest = verify_archive(Path(archived["path"]), archived["sha256"])
        self.assertIn("worktree/build/review.json", {item["path"] for item in manifest["files"]})
        (root / "build/unclassified.json").write_text("preserve unknown data")
        self.assertTrue(any("Unclassified ignored file" in error for error in ownership_errors(self.store, self.store.load("candidate"))))
        self.assertTrue((root / "build/unclassified.json").exists())

    def test_changed_qualified_output_fails_the_premerge_contract_gate(self):
        command = [sys.executable, "-c", "from pathlib import Path; Path('build/review.json').write_text('qualified output')"]
        root, _, controller, _ = self.candidate(outputs=["build/review.json"], checks=["make verify", command])
        controller.deliver()
        (root / "build/review.json").write_text("changed after acceptance")
        errors = contract.integration_errors(self.store, self.store.load("candidate"), require_evidence=True)
        self.assertTrue(any("output changed" in error for error in errors))

    def test_failed_declared_acceptance_is_not_subsumed_by_full_verification(self):
        _, _, controller, backend = self.candidate(checks=["make verify", [sys.executable, "-c", "raise SystemExit(7)"]])
        with self.assertRaisesRegex(ValueError, "Declared acceptance command failed"):
            controller.deliver()
        self.assertEqual(backend.pushes, 0)
        self.assertEqual(self.store.load("candidate")["status"], "NEEDS_ATTENTION")

    def test_product_change_after_initial_scope_check_is_never_published(self):
        root, _, controller, backend = self.candidate()
        original = task.scope_errors
        def after_check(value, target):
            result = original(value, target)
            (root / "rtl").mkdir(exist_ok=True)
            (root / "rtl/forbidden.sv").write_bytes(b"// preserve injected out-of-scope fixture\n")
            return result
        with mock.patch.object(task, "scope_errors", side_effect=after_check), self.assertRaisesRegex(ValueError, "Out-of-scope candidate"):
            controller.deliver()
        self.assertEqual(backend.pushes, 0)
        self.assertTrue((root / "rtl/forbidden.sv").is_file())

    def test_acceptance_is_explicit_argv_and_not_a_shell_recipe(self):
        before = self.run_git("for-each-ref")
        for checks in (["make verify && echo unsafe"], ['["python", "-c", "print(1)"]', "echo x | tool"]):
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                contract.preflight(self.store, "bad-check", ["scripts/a.py"], checks, [])
        self.assertEqual(self.run_git("for-each-ref"), before)


if __name__=="__main__":
    unittest.main()
