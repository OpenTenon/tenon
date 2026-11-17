"""Real isolated partition execution plus strict, explicitly synthetic CI report fixtures."""
from __future__ import annotations
from collections import Counter
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import ci_gate
import engineering
import engineering_common as common
import engineering_tests as runner


class ShardFixture(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="tenon-shards-")
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        (self.root/"tests/engineering").mkdir(parents=True)
        (self.root/".gitignore").write_text("build/\n__pycache__/\n",encoding="utf-8")
        for args in [("init","--quiet"),("config","user.name","Tenon fixture"),
                     ("config","user.email","fixture@example.invalid"),("config","core.autocrlf","false"),
                     ("add","."),("commit","--quiet","-m","fixture")]:
            subprocess.run(["git",*args],cwd=self.root,capture_output=True,check=True)
        self.allocation=json.loads((ROOT/"tests/engineering/windows-shards.json").read_text(encoding="utf-8"))

    def save_allocation(self):
        path=self.root/"tests/engineering/windows-shards.json"
        path.write_text(json.dumps(self.allocation,indent=2)+"\n",encoding="utf-8")
        return runner.load_allocation(self.root)

    def modules(self):
        names=["test_left_"+uuid.uuid4().hex,"test_right_"+uuid.uuid4().hex,"test_future_"+uuid.uuid4().hex]
        for name in names:
            body="""import unittest
from pathlib import Path
class Case(unittest.TestCase):
    def test_body(self):
        marker=Path(__file__).resolve().parents[2]/'build/executed'/MODULE
        marker.parent.mkdir(parents=True,exist_ok=True)
        with marker.open('x') as output: output.write('executed')
""".replace("MODULE",repr(name))
            (self.root/"tests/engineering"/(name+".py")).write_text(body,encoding="utf-8")
        self.allocation["module_assignments"]={names[0]:0,names[1]:1}
        self.save_allocation()
        return names


class ShardRunnerTests(ShardFixture):
    def test_both_real_shards_cover_inventory_once_and_report_unknown_module_hash(self):
        names=self.modules()
        reports=[]
        for index in (0,1):
            _,report=runner.run_tests(self.root,report_path=self.root/f"build/verification/{index}.json",
                shard_index=index,shard_count=2,stream=io.StringIO())
            self.assertEqual(report["status"],"PASS")
            self.assertEqual(report["kind"],"engineering-unittest-shard")
            self.assertTrue(report["partial"])
            self.assertEqual(report["counts"]["discovered"],3)
            self.assertEqual(report["counts"]["selected"],len(report["cases"]))
            future=next(item for item in report["shard"]["assignments"] if item["module"]==names[2])
            self.assertEqual(future["source"],"stable-hash")
            self.assertEqual(future["index"],int.from_bytes(hashlib.sha256(names[2].encode()).digest(),"big")%2)
            reports.append(report)
        self.assertEqual(reports[0]["discovery"],reports[1]["discovery"])
        left={row["id"] for row in reports[0]["cases"]}
        right={row["id"] for row in reports[1]["cases"]}
        self.assertFalse(left&right)
        self.assertEqual(left|right,set(reports[0]["discovery"]["case_ids"]))
        self.assertEqual({path.name for path in (self.root/"build/executed").iterdir()},set(names))

    def test_default_full_run_ignores_allocation_and_executes_every_case(self):
        names=self.modules()
        (self.root/"tests/engineering/windows-shards.json").write_text("invalid allocation ignored by full runs",encoding="utf-8")
        _,report=runner.run_tests(self.root,stream=io.StringIO())
        self.assertEqual(report["status"],"PASS")
        self.assertEqual(report["kind"],"engineering-unittest")
        self.assertFalse(report["partial"])
        self.assertNotIn("shard",report)
        self.assertEqual(len(report["cases"]),3)
        self.assertEqual({path.name for path in (self.root/"build/executed").iterdir()},set(names))

    def test_shard_options_cannot_select_other_groups_or_implicit_counts(self):
        for index,count in [(0,None),(None,2),(True,2),(0,3),(2,2)]:
            with self.subTest(index=index,count=count),self.assertRaises(ValueError):
                runner.run_tests(self.root,shard_index=index,shard_count=count,stream=io.StringIO())
        for group in ("all","format","check","sim"):
            with self.subTest(group=group),self.assertRaises(ValueError):
                engineering.verify(group=group,root=self.root,shard_index=0,shard_count=2)
        self.assertFalse((self.root/"build").exists())

    def test_ci_context_uses_checkout_sha_and_wrong_checkout_stops_execution(self):
        self.modules()
        sha=common.git(self.root,"rev-parse","HEAD").decode().strip()
        environment={"GITHUB_ACTIONS":"true","GITHUB_WORKSPACE":str(self.root),"GITHUB_SHA":sha,
                     "GITHUB_RUN_ID":"100","GITHUB_RUN_ATTEMPT":"3","GITHUB_REPOSITORY":"fixture/repo","GITHUB_JOB":"tools-windows"}
        with mock.patch.dict(os.environ,environment):
            _,report=runner.run_tests(self.root,shard_index=0,shard_count=2,stream=io.StringIO())
        self.assertEqual(report["status"],"PASS")
        self.assertEqual(report["ci"],{"checkout_sha":sha,"run_id":100,"run_attempt":3,"repository":"fixture/repo","job":"tools-windows"})
        environment["GITHUB_SHA"]="f"*40
        with mock.patch.dict(os.environ,environment):
            _,report=runner.run_tests(self.root,shard_index=1,shard_count=2,stream=io.StringIO())
        self.assertEqual(report["status"],"FAIL")
        self.assertEqual(report["counts"]["started"],0)

    def test_partial_observations_never_qualify_as_full_evidence(self):
        report=common.new_summary(self.root,"verification")
        path=self.root/"build/verification/evidence/summary.json"
        report["checks"].append(common.execute([sys.executable,"-c","print('fixture')"],self.root,path.parent/"case.log",check_id="fixture"))
        common.finish_summary(report,self.root,path)
        self.assertEqual(common.validate_evidence(path,self.root,{"fixture"})["status"],"PASS")
        for fields in [{"kind":"verification-partial"},{"kind":"engineering-unittest-shard"},{"partial":True},{"shard":{"index":0,"count":2}}]:
            value={**report,**fields}
            common.write_json(path,value)
            with self.subTest(fields=fields),self.assertRaisesRegex(ValueError,"Partial"):
                common.validate_evidence(path,self.root,{"fixture"})

    def test_native_partial_summary_cannot_be_reused_as_full_evidence(self):
        self.modules()
        import shutil
        scripts=self.root/"scripts"
        scripts.mkdir()
        for name in ("engineering_tests.py","engineering_common.py"):
            shutil.copy2(ROOT/"scripts"/name,scripts/name)
        (scripts/"repo_policy.py").write_text("print('fixture skill gate')\n",encoding="utf-8")
        with mock.patch.object(engineering,"doctor",return_value={"tools":{"git":{"status":"PASS"}}}):
            path,report=engineering.verify(group="tools",root=self.root,shard_index=0,shard_count=2)
        self.assertEqual(report["status"],"PASS")
        self.assertEqual(report["kind"],"verification-partial")
        self.assertTrue(report["partial"])
        check=next(item for item in report["checks"] if item["id"]=="tools-test")
        self.assertEqual(check["command"][-4:],["--shard-index","0","--shard-count","2"])
        cases=json.loads((path.parent/check["test_report"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(cases["kind"],"engineering-unittest-shard")
        self.assertTrue(cases["partial"])
        with self.assertRaisesRegex(ValueError,"Partial"):
            common.validate_evidence(path,self.root,engineering.required_ids("tools"))

    def test_nested_execution_of_an_unselected_case_is_observed_and_fails(self):
        names=self.modules()
        path=self.root/"tests/engineering"/(names[0]+".py")
        content=path.read_text(encoding="utf-8").replace("    def test_body(self):\n",
            "    def test_body(self):\n        import "+names[1]+"\n        "+names[1]+".Case('test_body').run(self._outcome.result)\n")
        path.write_text(content,encoding="utf-8")
        _,report=runner.run_tests(self.root,shard_index=0,shard_count=2,stream=io.StringIO())
        self.assertEqual(report["status"],"FAIL")
        self.assertTrue((self.root/"build/executed"/names[1]).is_file())
        extra=next(row for row in report["cases"] if row["module"]==names[1])
        self.assertFalse(extra["selected"])
        self.assertTrue(extra["discovered"])
        self.assertNotIn(extra["id"],report["shard"]["selected_ids"])

    def test_independent_discovery_collects_every_identity_without_running_bodies(self):
        names=self.modules()
        with mock.patch.object(runner,"source_state",wraps=common.source_state) as fingerprints:
            trusted=runner.discover_inventory(self.root)
        self.assertEqual(fingerprints.call_count,2)
        self.assertTrue(trusted["source_unchanged"])
        self.assertEqual(trusted["source"],trusted["source_after"])
        self.assertEqual([row["id"] for row in trusted["cases"]],sorted(name+".Case.test_body" for name in names))
        self.assertTrue(all(set(row)=={"id","module","class"} for row in trusted["cases"]))
        self.assertFalse((self.root/"build/executed").exists())

    def test_independent_discovery_rejects_empty_import_duplicate_and_exit_failures(self):
        with self.assertRaisesRegex(ValueError,"empty"):
            runner.discover_inventory(self.root)
        bad=self.root/"tests/engineering"/("test_bad_"+uuid.uuid4().hex+".py")
        bad.write_text("invalid syntax !!!\n",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"Import errors"):
            runner.discover_inventory(self.root)
        bad.unlink()
        case=unittest.FunctionTestCase(lambda: None)
        duplicates=mock.Mock(errors=[],discover=mock.Mock(return_value=unittest.TestSuite([case,case])))
        with self.assertRaisesRegex(ValueError,"duplicate"):
            runner.discover_inventory(self.root,loader=duplicates)
        for error in (RuntimeError("loader stopped"),SystemExit(0)):
            loader=mock.Mock(errors=[],discover=mock.Mock(side_effect=error))
            with self.subTest(error=type(error).__name__),self.assertRaisesRegex(ValueError,"Independent unittest discovery failed"):
                runner.discover_inventory(self.root,loader=loader)

    def test_independent_discovery_detects_import_source_mutation_without_repair(self):
        name="test_mutation_"+uuid.uuid4().hex
        path=self.root/"tests/engineering"/(name+".py")
        path.write_text("import unittest\nfrom pathlib import Path\n"
            "(Path(__file__).resolve().parents[2]/'unexpected-source.txt').write_text('preserve mutation evidence')\n"
            "class Body(unittest.TestCase):\n"
            "    def test_body(self): raise RuntimeError('body must never execute')\n",encoding="utf-8")
        with mock.patch.object(runner,"source_state",wraps=common.source_state) as fingerprints:
            with self.assertRaisesRegex(ValueError,"Source changed"):
                runner.discover_inventory(self.root)
        self.assertEqual(fingerprints.call_count,2)
        self.assertEqual((self.root/"unexpected-source.txt").read_text(),"preserve mutation evidence")
        self.assertFalse((self.root/"build").exists())

    def test_custom_suite_wrapper_is_refused_instead_of_silently_flattened(self):
        self.modules()
        class Wrapped(unittest.TestSuite):
            pass
        suite=unittest.TestLoader().discover(str(self.root/"tests/engineering"))
        wrapped=Wrapped(list(suite))
        loader=mock.Mock(errors=[],discover=mock.Mock(return_value=wrapped))
        _,report=runner.run_tests(self.root,loader=loader,shard_index=0,shard_count=2,stream=io.StringIO())
        self.assertEqual(report["status"],"FAIL")
        self.assertEqual(report["counts"]["started"],0)


class ShardGateTests(ShardFixture):
    def unallocated_module(self, label, index=None):
        for number in range(10000):
            name="test_unallocated_fixture_"+label+"_"+str(number)
            if name not in self.allocation["module_assignments"] and (index is None or runner.module_owner(name,self.allocation)[0]==index):
                return name
        raise AssertionError("Cannot allocate a synthetic future-module fixture")

    def trusted_inventory(self):
        # Keep both shortened positive-control shards nonempty independently of
        # where the measured manifest places the real native-case modules.
        identities=sorted([*ci_gate.CRITICAL_WINDOWS_CASES,self.unallocated_module("future")+".Case.test_future",
            *(self.unallocated_module("anchor"+str(index),index)+".Case.test_retained" for index in (0,1))])
        return [{"id":identity,"module":identity.split(".")[0],"class":identity.rsplit(".",1)[0]} for identity in identities]

    def reports(self):
        allocation,sha=self.save_allocation()
        expected={"checkout_sha":"a"*40,"run_id":991,"run_attempt":2,"repository":"fixture/repo"}
        inventory=self.trusted_inventory()
        identities=[row["id"] for row in inventory]
        reports=[]
        for index in (0,1):
            selected=[row for row in inventory if runner.module_owner(row["module"],allocation)[0]==index]
            rows=[{**row,"status":"PASS","started_at":"2026-11-17T10:00:00+00:00","completed_at":"2026-11-17T10:00:01+00:00",
                   "duration_seconds":1.0,"reason":None,"subtests":[]} for row in selected]
            source={"sha":expected["checkout_sha"],"branch":"","dirty":False,"worktree_sha256":"b"*64,"diff_sha256":"c"*64}
            reports.append({"schema_version":1,"kind":"engineering-unittest-shard","partial":True,"status":"PASS",
                "run_id":"fixture-local-"+str(index),"started_at":"2026-11-17T10:00:00+00:00","completed_at":"2026-11-17T10:00:02+00:00","errors":[],
                "source":source,"source_after":copy.deepcopy(source),"source_unchanged":True,
                "ci":{**expected,"job":ci_gate.WINDOWS_JOBS[index]},"python":{"version":"3.13.5 fixture","implementation":"CPython"},
                "platform":{"os":"nt","description":"Windows-fixture"},
                "discovery":{"complete":True,"errors":[],"duplicate_ids":[],"case_ids":identities,"cases":inventory},
                "shard":{"index":index,"count":2,"manifest_sha256":sha,"assignments":runner.module_assignments(inventory,allocation),"selected_ids":[row["id"] for row in selected]},
                "cases":rows,"fixture_events":[],"all_cases_accounted":True,"tests_run":len(rows),
                "counts":{"discovered":len(identities),"selected":len(rows),"started":len(rows),"outcomes":{state:len(rows) if state=="PASS" else 0 for state in runner.OUTCOMES}}})
        return copy.deepcopy(reports),expected,allocation,sha

    def test_complete_typed_current_windows_union_and_future_module_are_accepted(self):
        reports,expected,allocation,sha=self.reports()
        self.assertEqual(ci_gate.check_windows_reports(reports,expected,allocation,sha,self.trusted_inventory()),[])
        future_module=self.unallocated_module("future")
        self.assertNotIn(future_module,allocation["module_assignments"])
        for report in reports:
            future=next(row for row in report["shard"]["assignments"] if row["module"]==future_module)
            self.assertEqual(future["source"],"stable-hash")

    def test_missing_stale_duplicate_foreign_partial_and_hidden_failure_inputs_fail_closed(self):
        modes=("missing","third","swapped","index","job","old-sha","old-run","old-attempt","bool-attempt",
               "linux","python","python-mismatch","incomplete","duplicate-id","different-inventory","allocation",
               "omitted-case","foreign-case","hidden-subtest","failed-case","full-kind","failed-report","fixture-error","bool-count","bool-assignment","unknown-discovery","bool-timestamp","malformed")
        for mode in modes:
            reports,expected,allocation,sha=self.reports()
            report=reports[0]
            if mode=="missing": reports.pop()
            elif mode=="third": reports.append(copy.deepcopy(report))
            elif mode=="swapped": reports.reverse()
            elif mode=="index": reports[1]["shard"]["index"]=0
            elif mode=="job": report["ci"]["job"]="check"
            elif mode=="old-sha": report["source"]["sha"]="d"*40
            elif mode=="old-run": report["ci"]["run_id"]-=1
            elif mode=="old-attempt": report["ci"]["run_attempt"]-=1
            elif mode=="bool-attempt": report["ci"]["run_attempt"]=True
            elif mode=="linux": report["platform"]["os"]="posix"
            elif mode=="python": report["python"]["version"]="3.10.12 fixture"
            elif mode=="python-mismatch": reports[1]["python"]["version"]="3.13.6 fixture"
            elif mode=="incomplete": report["discovery"]["complete"]=False
            elif mode=="duplicate-id": report["discovery"]["case_ids"].append(report["discovery"]["case_ids"][0])
            elif mode=="different-inventory": reports[1]["discovery"]["case_ids"]=reports[1]["discovery"]["case_ids"][:-1]
            elif mode=="allocation": report["shard"]["manifest_sha256"]="e"*64
            elif mode=="omitted-case": report["cases"].pop()
            elif mode=="foreign-case": report["cases"].append(copy.deepcopy(reports[1]["cases"][0]))
            elif mode=="hidden-subtest": report["cases"][0]["subtests"]=[{"id":"sub","status":"FAIL"}]
            elif mode=="failed-case": report["cases"][0]["status"]="FAIL"
            elif mode=="full-kind": report["kind"]="verification"
            elif mode=="failed-report": report["status"]="FAIL"
            elif mode=="fixture-error": report["fixture_events"]=[{"id":"fixture","status":"ERROR","reason":"broken"}]
            elif mode=="bool-count": report["counts"]["outcomes"]["PASS"]=True
            elif mode=="bool-assignment": report["shard"]["assignments"][0]["index"]=bool(report["shard"]["assignments"][0]["index"])
            elif mode=="unknown-discovery": report["discovery"]["errors"]=None
            elif mode=="bool-timestamp": report["cases"][0]["started_at"]=True
            else: report["cases"]=None
            with self.subTest(mode=mode):
                self.assertTrue(ci_gate.check_windows_reports(reports,expected,allocation,sha,self.trusted_inventory()))

    def test_both_consistent_reports_omitting_the_same_case_fail_independent_inventory(self):
        reports,expected,allocation,sha=self.reports()
        trusted=self.trusted_inventory()
        shortened=[row for row in trusted if row["module"]!=self.unallocated_module("future")]
        retained={row["id"] for row in shortened}
        for report in reports:
            report["discovery"]["case_ids"]=[row["id"] for row in shortened]
            report["discovery"]["cases"]=copy.deepcopy(shortened)
            report["shard"]["assignments"]=runner.module_assignments(shortened,allocation)
            report["shard"]["selected_ids"]=[identity for identity in report["shard"]["selected_ids"] if identity in retained]
            report["cases"]=[row for row in report["cases"] if row["id"] in retained]
            counts=Counter(row["status"] for row in report["cases"])
            report["counts"]={"discovered":len(shortened),"selected":len(report["cases"]),"started":len(report["cases"]),
                              "outcomes":{state:counts[state] for state in runner.OUTCOMES}}
            report["tests_run"]=len(report["cases"])
        # Both shortened reports are otherwise self-consistent, including native PASS.
        self.assertEqual(ci_gate.check_windows_reports(reports,expected,allocation,sha,shortened),[])
        errors=ci_gate.check_windows_reports(reports,expected,allocation,sha,trusted)
        self.assertTrue(any("independent current-checkout inventory" in error for error in errors),errors)

    def test_empty_duplicate_or_malformed_trusted_inventory_fails_closed(self):
        reports,expected,allocation,sha=self.reports()
        for trusted in ([],[self.trusted_inventory()[0]]*2,[{"id":"bad","module":"bad","class":"unrelated"}]):
            with self.subTest(trusted=trusted):
                self.assertTrue(ci_gate.check_windows_reports(reports,expected,allocation,sha,trusted))

    def test_aggregation_propagates_checkout_discovery_failure_before_using_reports(self):
        reports,expected,allocation,sha=self.reports()
        directory=self.root/"build/ci-windows"
        for index,report in enumerate(reports):
            common.write_json(directory/str(index)/"run/tools-test.json",report)
        before={path:path.read_bytes() for path in directory.rglob("tools-test.json")}
        for reason in ("Independent import failure","Source changed during independent discovery"):
            context={**expected,"job":"required"}
            with mock.patch.object(runner,"github_context",return_value=context),mock.patch.object(ci_gate,"git",return_value=(expected["checkout_sha"]+"\n").encode()),mock.patch.object(runner,"discover_inventory",side_effect=ValueError(reason)) as discover,mock.patch.object(ci_gate,"check_windows_reports") as validate:
                errors=ci_gate.windows_report_errors(directory,self.root)
            discover.assert_called_once_with(self.root)
            validate.assert_not_called()
            self.assertTrue(errors)
        self.assertEqual({path:path.read_bytes() for path in before},before)

    def test_critical_windows_skip_fails_even_with_consistent_counts(self):
        for mode in ("case","subtest"):
            reports,expected,allocation,sha=self.reports()
            for report in reports:
                for row in report["cases"]:
                    if row["id"] in ci_gate.CRITICAL_WINDOWS_CASES:
                        if mode=="case":
                            row.update(status="SKIP",reason="deliberate prerequisite")
                        else:
                            row["subtests"]=[{"id":"native-sharing","status":"SKIP","reason":"native prerequisite"}]
                counts=Counter(row["status"] for row in report["cases"])
                report["counts"]["outcomes"]={state:counts[state] for state in runner.OUTCOMES}
            errors=ci_gate.check_windows_reports(reports,expected,allocation,sha,self.trusted_inventory())
            with self.subTest(mode=mode):
                self.assertTrue(any("Required native Windows" in error for error in errors))

    def test_cli_cannot_pass_without_current_run_report_context(self):
        environment={**os.environ,"CI_NEEDS":json.dumps({name:{"result":"success"} for name in ci_gate.REQUIRED_JOBS}),
                     "GITHUB_ACTIONS":"false"}
        result=subprocess.run([sys.executable,str(ROOT/"scripts/ci_gate.py"),"--windows-shards",
                               str(self.root/"build/missing-reports")],env=environment,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/"build/missing-reports").exists())

    def test_missing_duplicate_and_extra_downloaded_reports_fail(self):
        reports,expected,allocation,sha=self.reports()
        directory=self.root/"build/ci-windows"
        context={**expected,"job":"required"}
        trusted={"source":{"sha":expected["checkout_sha"],"dirty":False},"source_unchanged":True,"cases":self.trusted_inventory()}
        with mock.patch.object(runner,"github_context",return_value=context),mock.patch.object(ci_gate,"git",return_value=(expected["checkout_sha"]+"\n").encode()),mock.patch.object(runner,"discover_inventory",return_value=trusted):
            self.assertTrue(ci_gate.windows_report_errors(directory,self.root))
            for index,report in enumerate(reports):
                path=directory/str(index)/"run/tools-test.json"
                common.write_json(path,report)
            self.assertEqual(ci_gate.windows_report_errors(directory,self.root),[])
            common.write_json(directory/"0/extra/tools-test.json",reports[0])
            self.assertTrue(ci_gate.windows_report_errors(directory,self.root))
            (directory/"0/extra/tools-test.json").unlink()
            common.write_json(directory/"unclassified/tools-test.json",reports[0])
            self.assertTrue(ci_gate.windows_report_errors(directory,self.root))


if __name__=="__main__":
    unittest.main()
