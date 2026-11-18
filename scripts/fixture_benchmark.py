#!/usr/bin/env python3
"""Explicit local module/fixture profiling; reports are partial observations, never full evidence."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import unittest

DEFAULT_MODULES=("test_health_drills","test_task_contracts","test_controller_leases","test_lifecycle_v2")

def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2)+"\n",encoding="utf-8",newline="\n")

def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item

def profile(root, modules, report):
    sys.path[:0]=[str(root/"tests/engineering"),str(root/"scripts")]
    from engineering_common import source_state
    started_at=datetime.now(timezone.utc).isoformat()
    before=source_state(root)
    started=time.perf_counter()
    loader=unittest.TestLoader()
    suite=loader.loadTestsFromNames(modules)
    if loader.errors:
        raise ValueError("Module discovery failed: "+"; ".join(loader.errors))
    ids=[case.id() for case in flatten(suite)]
    if not ids or len(ids)!=len(set(ids)):
        raise ValueError("Empty or duplicate selected inventory")
    discovery_seconds=time.perf_counter()-started
    rows={name:{"id":name,"status":"NOT_RUN","reason":None,"phases":{},"duration_seconds":None} for name in ids}
    fixture_events=[]; git_calls=[]; current={"case":None,"phase":None}; starts={}
    original_run=subprocess.run
    def observed(*args,**kwargs):
        argv=args[0] if args else kwargs.get("args")
        tokens=[str(value) for value in argv] if isinstance(argv,(list,tuple)) else []
        began=time.perf_counter(); result=None; failure=None
        try:
            result=original_run(*args,**kwargs)
            return result
        except BaseException as error:
            failure=error
            raise
        finally:
            if tokens and Path(tokens[0]).name.lower() in {"git","git.exe"}:
                commands={"init","config","clone","remote","add","commit","update-ref","checkout","worktree","rev-parse","show","cat-file","status","for-each-ref","branch"}
                git_calls.append({"case":current["case"],"phase":current["phase"] or "suite_unattributed",
                    "category":next((value for value in tokens[1:] if value in commands),"other"),
                    "seconds":time.perf_counter()-began,
                    "returncode":getattr(result,"returncode",getattr(failure,"returncode",None)),
                    "error_class":type(failure).__name__ if failure else None})
    originals={}
    for method,phase in (("_callSetUp","setup"),("_callTestMethod","actual_method"),("_callTearDown","teardown"),("doCleanups","cleanups")):
        old=getattr(unittest.TestCase,method); originals[method]=old
        def wrapped(case,*args,_old=old,_phase=phase,**kwargs):
            previous=dict(current); current.update(case=case.id(),phase=_phase)
            began=time.perf_counter()
            try:
                return _old(case,*args,**kwargs)
            finally:
                if case.id() in rows:
                    phases=rows[case.id()]["phases"]
                    phases[_phase]=phases.get(_phase,0)+time.perf_counter()-began
                current.clear(); current.update(previous)
        setattr(unittest.TestCase,method,wrapped)
    class Result(unittest.TextTestResult):
        def mark(self,test,status,reason=None):
            if test.id() in rows:
                rows[test.id()].update(status=status,reason=reason)
            else:
                fixture_events.append({"id":test.id(),"status":status,"reason":reason})
        def startTest(self,test):
            starts[test.id()]=time.perf_counter()
            super().startTest(test)
        def stopTest(self,test):
            if test.id() in rows and test.id() in starts:
                rows[test.id()]["duration_seconds"]=time.perf_counter()-starts.pop(test.id())
            super().stopTest(test)
        def addSuccess(self,test):
            self.mark(test,"PASS"); super().addSuccess(test)
        def addFailure(self,test,error):
            self.mark(test,"FAIL",self._exc_info_to_string(error,test)); super().addFailure(test,error)
        def addError(self,test,error):
            self.mark(test,"ERROR",self._exc_info_to_string(error,test)); super().addError(test,error)
        def addSkip(self,test,reason):
            self.mark(test,"SKIP",reason); super().addSkip(test,reason)
        def addExpectedFailure(self,test,error):
            self.mark(test,"EXPECTED_FAILURE"); super().addExpectedFailure(test,error)
        def addUnexpectedSuccess(self,test):
            self.mark(test,"UNEXPECTED_SUCCESS"); super().addUnexpectedSuccess(test)
        def addSubTest(self,test,subtest,error):
            if error is not None:
                self.mark(test,"FAIL" if issubclass(error[0],test.failureException) else "ERROR",str(error[1]))
            super().addSubTest(test,subtest,error)
    stream=io.StringIO(); result=None; exception=None
    subprocess.run=observed
    began=time.perf_counter()
    try:
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
    except BaseException as error:
        exception={"class":type(error).__name__,"reason":str(error)[:1000]}
    finally:
        group_seconds=time.perf_counter()-began
        subprocess.run=original_run
        for method,old in originals.items():
            setattr(unittest.TestCase,method,old)
    after=source_state(root)
    passed=bool(result and result.wasSuccessful() and before==after and not exception and all(row["status"]!="NOT_RUN" for row in rows.values()))
    payload={"schema_version":1,"kind":"fixture-profile-observation","partial":True,
        "status":"PASS" if passed else "FAIL","source":before,"source_after":after,"source_unchanged":before==after,
        "started_at":started_at,"completed_at":datetime.now(timezone.utc).isoformat(),"modules":modules,"discovered":len(ids),
        "outcomes":dict(Counter(row["status"] for row in rows.values())),"cases":list(rows.values()),
        "fixture_events":fixture_events,"exception":exception,"discovery_seconds":discovery_seconds,
        "group_wall_seconds":group_seconds,"observed_direct_git":git_calls,"observed_direct_git_count":len(git_calls),
        "observed_direct_git_seconds":sum(row["seconds"] for row in git_calls),
        "descendant_internal_git_count":None,"descendant_internal_git_seconds":None,
        "python":sys.version,"platform":platform.platform(),
        "host_fingerprint":hashlib.sha256(platform.node().encode()).hexdigest(),
        "qualification":"Direct forwarding instrumentation only; phase intervals can nest. Inclusive case duration avoids double-counting. Worker-internal Git, CPU and billing are unknown. Partial local observation, not full verification, hosted CI or signoff."}
    write(report,payload)
    report.with_suffix(".log").write_text(stream.getvalue(),encoding="utf-8",newline="\n")
    return 0 if passed else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true",help="Explicitly run the selected local modules once in a fresh child")
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--modules",nargs="+",default=list(DEFAULT_MODULES))
    parser.add_argument("--report",type=Path)
    parser.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    args=parser.parse_args()
    if not args.run and not args.worker:
        print(json.dumps({"status":"NOT_RUN","partial":True,"modules":args.modules,"ci_triggers":0}))
        return 0
    root=args.root.resolve()
    if any(name in os.environ for name in ("GIT_DIR","GIT_COMMON_DIR","GIT_WORK_TREE","GIT_INDEX_FILE","GIT_OBJECT_DIRECTORY","GIT_ALTERNATE_OBJECT_DIRECTORIES","GIT_CONFIG_PARAMETERS","GIT_EXEC_PATH","GIT_TEMPLATE_DIR","GIT_NAMESPACE")) or os.environ.get("GIT_CONFIG_COUNT") not in {None,"0"}:
        parser.error("Use an unredirected Git environment")
    for name in args.modules:
        if not re.fullmatch(r"test_[A-Za-z0-9_]+",name) or not (root/"tests/engineering"/(name+".py")).is_file():
            parser.error("Select existing repository unittest modules")
    if args.report is None:
        parser.error("--report is required for actual profiling")
    raw_report=args.report if args.report.is_absolute() else root/args.report
    raw_log=raw_report.with_suffix(".log")
    # Check the original leaf paths before resolving, including dangling links.
    if raw_report.suffix!=".json" or os.path.lexists(raw_report) or os.path.lexists(raw_log):
        parser.error("JSON report/log cannot replace existing paths or links")
    from engineering_tests import report_destination
    try:
        report=report_destination(root,raw_report)
        log=report_destination(root,raw_log)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        parser.error(str(error))
    if log!=report.with_suffix(".log") or os.path.lexists(report) or os.path.lexists(log):
        parser.error("Report/log canonical paths differ or already exist")
    # Both canonical targets have passed the shared literal tracked-file guard.
    report.parent.mkdir(parents=True,exist_ok=True)
    if args.worker:
        try:
            return profile(root,args.modules,report)
        except BaseException as error:
            write(report,{"schema_version":1,"kind":"fixture-profile-observation","partial":True,"status":"FAIL",
                          "reason":type(error).__name__+": "+str(error)[:1000]})
            return 1
    env=os.environ.copy(); env["PYTHONDONTWRITEBYTECODE"]="1"
    began=time.perf_counter()
    process=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),"--worker","--root",str(root),
                            "--report",str(report),"--modules",*args.modules],cwd=root,env=env,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=1800)
    elapsed=time.perf_counter()-began
    if report.exists():
        value=json.loads(report.read_text(encoding="utf-8")); value["child_process_wall_seconds"]=elapsed
        write(report,value)
    print(json.dumps({"status":"PASS" if process.returncode==0 else "FAIL","report":str(report),"child_process_wall_seconds":elapsed}))
    return process.returncode

if __name__=="__main__":
    raise SystemExit(main())
