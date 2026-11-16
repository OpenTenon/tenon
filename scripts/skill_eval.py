#!/usr/bin/env python3
"""Explicit, isolated Codex Skill evaluations; default is a no-model-call execution plan."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import re
import fnmatch
import time
import math
from pathlib import Path
from engineering_common import ROOT, digest, execute, git, run_id, source_state, stop_process, write_json
from task_store import TaskStore, resource_lease, identifier


def score(case: dict, events: list[dict], exit_code: int | None) -> dict:
    items = [event.get("item", {}) for event in events if isinstance(event.get("item"), dict)]
    inputs = [item.get("command", "") if item.get("type")=="command_execution" else
              item.get("arguments",item.get("input",{})) if item.get("type")=="mcp_tool_call" else
              item.get("name","") for item in items if item.get("type") in {"command_execution","mcp_tool_call","skill"}]
    calls = "\n".join(json.dumps(value,ensure_ascii=False).replace("\\\\","/").replace("\\","/") for value in inputs)
    messages = "\n".join(str(item.get("text", "")) for item in items if item.get("type") == "agent_message")
    expected = case.get("skill")
    explicit = {item.get("name") for item in items if item.get("type")=="skill"}
    triggered = (f"/{expected}/SKILL.md" in calls or expected in explicit) if expected else ".agents/skills/" not in calls and not any(str(name).startswith("tenon-") for name in explicit)
    changed = [item for item in items if item.get("type") == "file_change"]
    unsafe = any(not str(change.get("path", "")).replace("\\", "/").startswith("build/")
                 for item in changed for change in item.get("changes", []))
    outcomes = all(token.lower() in messages.lower() or token.lower() in calls.lower() for token in case.get("tokens", []))
    completed = any(event.get("type") == "turn.completed" for event in events)
    failed = any(event.get("type") in {"turn.failed", "error"} for event in events)
    # Keywords remain a diagnostic signal; fixture assertions decide actual outcomes.
    if case.get("mode") == "workspace-write":
        def changed_path(change):
            path = Path(str(change.get("path", "")))
            root = Path(case["fixture_root"]).resolve() if case.get("fixture_root") else None
            if path.is_absolute() and root and path.resolve().is_relative_to(root):
                return path.resolve().relative_to(root).as_posix()
            return str(path).replace("\\", "/")
        unsafe = any(not any(fnmatch.fnmatchcase(changed_path(change), pattern)
                             for pattern in case.get("allowed_paths", []))
                     for item in changed for change in item.get("changes", []))
    passed = exit_code == 0 and completed and not failed and triggered and not unsafe
    usage = [event["usage"] for event in events if isinstance(event.get("usage"), dict)]
    return {"status": "PASS" if passed else "FAIL", "evidence_level":"trace-signals-only", "skill_trigger_matched": triggered,
            "outcome_signals_matched": outcomes, "source_mutation_detected": unsafe,
            "turn_completed": completed, "usage": usage or None,
            "note": "Deterministic trace signals require human review; they do not prove semantic correctness."}


def assess_outcome(trace: dict, before: dict, after: dict, assertions: list[dict], *,
                   writable: bool = False, changed_paths: list[str] | None = None,
                   allowed_paths: list[str] | None = None) -> dict:
    source_unchanged = before == after
    bounded = bool(changed_paths) and all(any(fnmatch.fnmatchcase(path, pattern)
        for pattern in allowed_paths or []) for path in changed_paths or [])
    source_valid = bounded if writable else source_unchanged
    checked = bool(assertions) and all(item.get("passed") is True for item in assertions)
    return {"status":"PASS" if trace["status"]=="PASS" and source_valid and checked else "FAIL",
            "source_unchanged":source_unchanged,"source_scope_matched":source_valid,"changed_paths":changed_paths or [],"assertions":assertions,
            "trace":trace,"evidence_level":"fixture-and-trace"}


def configured_mcp_overrides(fixture: Path | None = None, cli: list[str] | None = None,
                             profile: str | None = None) -> list[str]:
    """Resolve exact-CWD base/profile/project layers, then verify every server is disabled.

    mcp/list is a config-only operation. Inventory payloads can contain credentials:
    keep them in memory and never write/print them. Empty-table overrides do not
    erase servers merged from lower layers. Provider/auth configuration is intact.
    """
    fixture, cli = (fixture or ROOT).resolve(), cli or codex_command()
    prefix = [*cli, *(["--profile", profile] if profile else [])]
    def inventory(overrides):
        result = subprocess.run([*prefix, *overrides, "mcp", "list", "--json"], cwd=fixture,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
        if result.returncode:
            raise ValueError("Cannot resolve effective fixture MCP configuration; no model call is allowed")
        try:
            values = json.loads(result.stdout)
            if not isinstance(values, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                    or not item["name"] or type(item.get("enabled")) is not bool for item in values):
                raise ValueError("Invalid MCP inventory")
            names = [item["name"] for item in values]
            if len(names) != len(set(names)):
                raise ValueError("Ambiguous MCP inventory")
            return values
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("Effective MCP inventory is invalid; no model call is allowed") from error
    names = [item["name"] for item in inventory([])]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+",name) for name in names):
        raise ValueError("An MCP server name cannot be safely addressed by the installed CLI override format")
    args = [argument for name in names for argument in ("-c", "mcp_servers." + name + ".enabled=false")]
    if any(item["enabled"] for item in inventory(args)):
        raise ValueError("Effective MCP configuration still contains an enabled server")
    return args


def decision_assertion(case: dict, events: list[dict], expected: str, fixture: Path) -> dict:
    messages = [event.get("item",{}).get("text","") for event in events if event.get("item",{}).get("type")=="agent_message"]
    try:
        text = messages[-1].strip()
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        result = json.loads(text)
        references = result.get("references",[])
        valid = isinstance(references,list) and (bool(references) or expected=="ANSWER") and all(isinstance(item,str) and not Path(item).is_absolute() and (fixture/item).resolve().is_relative_to(fixture.resolve()) and (fixture/item).is_file() for item in references)
        return {"kind":"grounded-decision","expected":expected,"observed":result.get("decision"),
                "passed":result.get("decision")==expected and valid}
    except (IndexError,ValueError,TypeError,KeyError):
        return {"kind":"grounded-decision","passed":False,"reason":"Missing structured decision/references"}


def read_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except json.JSONDecodeError:
            continue  # stderr diagnostics are retained in the same raw execution log.
    return events


def isolate(destination: Path, *, writable: bool = False) -> Path:
    # Source files, including the current uncommitted Skills, are copied; ignored runs/PDKs are never copied.
    destination.mkdir(parents=True, exist_ok=False)
    names = git(ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    for raw in names:
        if not raw:
            continue
        source = ROOT / raw.decode("utf-8")
        target = destination / source.relative_to(ROOT)
        if source.is_file() and not source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    subprocess.run(["git", "init", "--quiet", str(destination)], check=True)
    # Ref-dependent scenarios can inspect the real revisions without copying private run artifacts.
    subprocess.run(["git", "-C", str(destination), "fetch", "--quiet", str(ROOT),
                    "HEAD:refs/remotes/origin/dev", "origin/main:refs/remotes/origin/main"], check=True)
    subprocess.run(["git", "-C", str(destination), "reset", "--mixed", "--quiet", "origin/dev"], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--quiet", "-b", "dev-eval-fixture"], check=True)
    git(destination,"config","user.name","Tenon isolated evaluation")
    git(destination,"config","user.email","fixture@example.invalid")
    git(destination,"config","core.autocrlf","false")
    git(destination,"config","credential.helper","")
    if writable:
        git(destination,"add","--all")
        if git(destination,"diff","--cached","--name-only").strip():
            git(destination,"commit","--quiet","-m","isolated current evaluation inputs")
        git(destination,"update-ref","refs/remotes/origin/dev","HEAD")
    remote = destination.with_name(destination.name+".remote.git")
    subprocess.run(["git","init","--bare","--quiet",str(remote)],check=True)
    git(destination,"remote","add","origin",str(remote))
    git(destination,"push","--quiet","origin","HEAD:refs/heads/dev")
    git(destination,"config","protocol.allow","never")
    git(destination,"config","protocol.file.allow","always")
    return remote


IMPLEMENTATION_CASES = {"implementation-verify", "implementation-scope-boundary"}
EXPECTED_CODES = [(0,"PASS"),(2,"BLOCKED"),(1,"FAIL"),(7,"FAIL"),(124,"FAIL"),(-1,"FAIL")]


def load_cases(root: Path = ROOT) -> list[dict]:
    cases, seen = [], set()
    for path in sorted((root/"tests/skill-evals").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version")!=1 or not isinstance(value.get("cases"),list):
            raise ValueError("Unsupported evaluation suite: "+str(path))
        for case in value["cases"]:
            name = identifier(case["id"])
            if name in seen or not isinstance(case.get("prompt"),str) or not case["prompt"].strip():
                raise ValueError("Duplicate or invalid evaluation case: "+name)
            if case.get("skill") not in {None,"tenon-task","tenon-verify","tenon-generated","tenon-physical","tenon-release"}:
                raise ValueError("Unknown evaluation Skill: "+name)
            if case.get("mode","read-only") not in {"read-only","workspace-write"}:
                raise ValueError("Unsupported fixture mode: "+name)
            if case.get("mode")=="workspace-write" and name not in IMPLEMENTATION_CASES:
                raise ValueError("Writable cases require a concrete fixture oracle: "+name)
            if case.get("decision") is not None and case["decision"] not in {"PLAN","READY","BLOCKED","ANSWER"}:
                raise ValueError("Unknown expected decision: "+name)
            allowed_paths = case.get("allowed_paths",[])
            if not isinstance(allowed_paths,list) or (case.get("mode")=="workspace-write" and not allowed_paths):
                raise ValueError("Writable evaluation cases require a file scope")
            for allowed in allowed_paths:
                if not isinstance(allowed,str) or Path(allowed).is_absolute() or ".." in allowed.replace("\\","/").split("/"):
                    raise ValueError("Evaluation scope must stay inside the fixture")
            seen.add(name)
            cases.append(case)
    if not cases:
        raise ValueError("No Skill evaluation cases found")
    return cases


def observed_usage(events: list[dict]) -> dict | None:
    values = [event.get("usage") for event in events if event.get("type")=="turn.completed"]
    if not values or any(not isinstance(value,dict) or any(type(value.get(key)) is not int or value[key]<0
        for key in ("input_tokens","output_tokens")) for value in values):
        return None
    result = {key:sum(value[key] for value in values) for key in ("input_tokens","output_tokens")}
    result["total_tokens"] = result["input_tokens"]+result["output_tokens"]
    return result


class EvaluationBudget:
    """Cap CLI invocations and account observed tokens; never imply a provider spending cap."""
    def __init__(self, max_calls: int, token_budget: int):
        if any(type(value) is not int or value<=0 for value in (max_calls,token_budget)):
            raise ValueError("Call and token budgets must be positive integers")
        self.max_calls,self.token_budget = max_calls,token_budget
        self.calls = self.tokens = 0
        self.unknown_usage_calls = 0
        self.blocker = None

    def reserve(self):
        self.ensure_available()
        self.calls += 1

    def ensure_available(self):
        if self.blocker or self.calls>=self.max_calls or self.tokens>=self.token_budget:
            raise ValueError(self.blocker or "Evaluation budget exhausted")

    def record(self, usage: dict | None):
        if usage is None:
            self.unknown_usage_calls += 1
            self.blocker = "Provider usage unknown; no further evaluation calls"
        else:
            self.tokens += usage["total_tokens"]
            if self.tokens>self.token_budget:
                self.blocker = "Observed token budget exceeded; no further evaluation calls"

    def snapshot(self):
        return {"max_calls":self.max_calls,"calls":self.calls,"token_budget":self.token_budget,
                "observed_tokens":self.tokens if not self.unknown_usage_calls else None,
                "known_tokens":self.tokens,"unknown_usage_calls":self.unknown_usage_calls,"blocker":self.blocker,
                "enforcement":"CLI invocation cap and observed usage; in-flight overshoot possible; no monetary guarantee"}


def codex_command(executable: str | None = None) -> list[str]:
    """Use an argv-safe native/Node entry; never pass model prompts through a batch shell."""
    resolved = shutil.which(executable or "codex")
    if not resolved:
        raise ValueError("An existing Codex CLI is required; nothing is installed automatically")
    path = Path(resolved)
    if os.name!="nt" or path.suffix.lower()==".exe":
        return [str(path)]
    node = shutil.which("node")
    for relative in ("node_modules/@openai/codex/bin/codex.js","global/5/node_modules/@openai/codex/bin/codex.js"):
        script = path.parent/relative
        if node and Path(node).suffix.lower()==".exe" and script.is_file():
            return [node,str(script)]
    raise ValueError("A Windows batch shim cannot safely receive prompts; provide --codex-bin with a native executable")


def codex_version(cli: list[str]) -> str:
    """A PATH shim alone is not a working CLI (notably Windows shims in WSL)."""
    try:
        version = subprocess.check_output([*cli,"--version"],stderr=subprocess.PIPE,timeout=20).decode("utf-8",errors="replace").strip()
    except (OSError,subprocess.SubprocessError) as error:
        raise ValueError("Codex CLI is not runnable in this environment; select an existing native executable") from error
    if not version:
        raise ValueError("Codex CLI returned no version identity")
    return version


def run_bounded(argv: list[str], fixture: Path, log: Path, timeout: float, max_bytes: int) -> dict:
    if not math.isfinite(timeout) or timeout<=0 or type(max_bytes) is not int or max_bytes<=0:
        raise ValueError("Finite positive timeout and positive output limit are required")
    started = time.monotonic()
    result = {"command":argv,"cwd":str(fixture),"status":"NOT_RUN","exit_code":None}
    log.parent.mkdir(parents=True,exist_ok=True)
    try:
        options = {"creationflags":subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {"start_new_session":True}
        with log.open("wb") as output, subprocess.Popen(argv,cwd=fixture,stdout=output,stderr=subprocess.STDOUT,**options) as process:
            try:
                while process.poll() is None:
                    reason = "output-limit" if log.stat().st_size>max_bytes else "timeout" if time.monotonic()-started>=timeout else None
                    if reason:
                        stop_process(process)
                        result.update(status="BLOCKED",exit_code=124,reason=reason)
                        break
                    time.sleep(0.05)
                else:
                    result.update(status="PASS" if process.returncode==0 else "FAIL",exit_code=process.returncode)
            except BaseException:
                stop_process(process)
                raise
    except OSError as error:
        result.update(status="BLOCKED",reason=str(error))
    if log.exists() and log.stat().st_size>max_bytes:
        with log.open("r+b") as output:
            output.truncate(max_bytes)
        result.update(status="BLOCKED",reason="output-limit")
    result.update(duration_seconds=round(time.monotonic()-started,3),log=log.name,
                  log_sha256=digest(log) if log.exists() else None,max_log_bytes=max_bytes)
    return result


def prepare_implementation(fixture: Path) -> None:
    (fixture/"scripts/eval_target.py").write_text("def exit_status(code):\n    return 'PASS'\n",encoding="utf-8")
    folder = fixture/"build/skill-eval"
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"test_target.py").write_text(
        "import sys,unittest\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))\nfrom eval_target import exit_status\n"
        "class ExitTests(unittest.TestCase):\n    def test_codes(self):\n        for code,expected in "+repr(EXPECTED_CODES)+":\n            with self.subTest(code=code): self.assertEqual(exit_status(code),expected)\n"
        "if __name__=='__main__': unittest.main()\n",encoding="utf-8")
    git(fixture,"add","scripts/eval_target.py")
    git(fixture,"commit","--quiet","-m","isolated faulty engineering fixture")
    git(fixture,"push","--quiet","origin","HEAD:refs/heads/dev")
    git(fixture,"update-ref","refs/remotes/origin/dev","HEAD")


def implementation_assertion(fixture: Path) -> dict:
    """Execute only an AST-vetted pure fixture function; never run arbitrary model code on the host."""
    import ast
    try:
        source = (fixture/"scripts/eval_target.py").read_text(encoding="utf-8")
        if len(source.encode())>16384:
            raise ValueError("Fixture helper is too large")
        tree = ast.parse(source)
        body = [node for node in tree.body if not (isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str))]
        if len(body)!=1 or not isinstance(body[0],ast.FunctionDef) or body[0].name!="exit_status":
            raise ValueError("Expected the single pure exit_status function")
        function = body[0]
        if function.decorator_list or function.args.defaults or function.args.kw_defaults or function.args.vararg or function.args.kwarg or function.args.kwonlyargs or function.args.posonlyargs or [arg.arg for arg in function.args.args]!=["code"]:
            raise ValueError("Unsupported function signature")
        allowed = (ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Return,ast.If,ast.IfExp,ast.Compare,
                   ast.Eq,ast.NotEq,ast.In,ast.NotIn,ast.Constant,ast.Name,ast.Load,ast.Dict,ast.Subscript,
                   ast.Tuple,ast.List,ast.Call,ast.Attribute,ast.Expr,ast.UnaryOp,ast.USub)
        for node in ast.walk(tree):
            if not isinstance(node,allowed):
                raise ValueError("Fixture function is not a supported pure expression")
            if isinstance(node,ast.Name) and node.id not in {"code","int","str"}:
                raise ValueError("Unexpected name in fixture function")
            if isinstance(node,ast.Attribute) and (node.attr!="get" or not isinstance(node.value,ast.Dict)):
                raise ValueError("Only a literal dictionary lookup is allowed")
            if isinstance(node,ast.Call) and (not isinstance(node.func,ast.Attribute) or node.keywords):
                raise ValueError("Arbitrary function calls are forbidden")
        namespace = {"__builtins__":{},"int":int,"str":str}
        exec(compile(tree,"<isolated-pure-evaluation>","exec"),namespace)
        observations = []
        for code,expected in EXPECTED_CODES:
            value = namespace["exit_status"](code)
            observations.append({"code":code,"expected":expected,"observed":value if type(value) in {str,int,float,bool,type(None)} else repr(value)[:256]})
        return {"kind":"engineering-behavior","passed":all(item["expected"]==item["observed"] for item in observations),"observations":observations}
    except (OSError,ValueError,SyntaxError,LookupError,TypeError,NameError,RecursionError) as error:
        return {"kind":"engineering-behavior","passed":False,"reason":str(error)}


def ref_snapshot(repo: Path, *, bare: bool = False) -> dict:
    argv = ["git","--git-dir",str(repo)] if bare else ["git","-C",str(repo)]
    rows = subprocess.check_output([*argv,"for-each-ref","--format=%(refname) %(objectname)"]).decode().splitlines()
    return dict(row.split(" ",1) for row in rows)


def fixture_assertions(case: dict, fixture: Path, remote: Path, before: dict, before_refs: dict) -> tuple[list[dict],list[str]]:
    paths = set(git(fixture,"diff","--name-only",before["sha"],"--").decode().splitlines())
    paths.update(value.decode() for value in git(fixture,"ls-files","--others","--exclude-standard","-z").split(b"\0") if value)
    safe_source = (all(any(fnmatch.fnmatchcase(path,pattern) for pattern in case.get("allowed_paths",[])) for path in paths)
                   if case.get("mode")=="workspace-write" or case["id"] in IMPLEMENTATION_CASES else source_state(fixture)==before)
    if safe_source:
        generated = subprocess.run([sys.executable,"tools/generate_tier0.py","--check"],cwd=fixture,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        assertions = [{"kind":"generated-inputs","passed":generated.returncode==(1 if case["id"]=="generated-drift" else 0)}]
    else:
        assertions = [{"kind":"source-boundary-before-oracle","passed":False}]
    expected_remote = {"refs/heads/dev":before["sha"]}
    expected_local = before_refs.copy()
    if case["id"] in IMPLEMENTATION_CASES and safe_source:
        assertions.append(implementation_assertion(fixture))
    assertions.extend([
        {"kind":"local-remote-contract","passed":ref_snapshot(remote,bare=True)==expected_remote},
        {"kind":"local-ref-contract","passed":ref_snapshot(fixture)==expected_local},
        {"kind":"local-origin-contract","passed":git(fixture,"remote","get-url","origin").decode().strip()==str(remote)}])
    return assertions,sorted(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="explicitly allow model calls in isolated fixtures")
    parser.add_argument("--model", help="explicit model identifier; recorded unchanged")
    parser.add_argument("--codex-bin", help="existing native Codex executable, if normal discovery is unavailable")
    parser.add_argument("--profile", help="existing Codex profile; provider/auth remain in their original configuration")
    parser.add_argument("--max-calls", type=int, help="maximum CLI invocations, not underlying provider requests")
    parser.add_argument("--token-budget", type=int, help="input plus output token budget")
    parser.add_argument("--budget-mode", choices=("strict","observed"), default="strict")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-log-bytes", type=int, default=1048576)
    args = parser.parse_args()
    if args.repeats < 1 or not math.isfinite(args.timeout) or args.timeout <= 0 or args.max_log_bytes <= 0:
        parser.error("repeats/timeout/output limit must be positive")
    cases = load_cases(ROOT)
    if not args.execute:
        print(json.dumps({"status":"NOT_RUN", "model_calls":0, "cases":len(cases),
                          "planned_runs":len(cases) * args.repeats, "model":args.model,
                          "budget_mode":args.budget_mode}, indent=2))
        return 0
    if not args.model or args.max_calls is None or args.token_budget is None:
        parser.error("--execute requires --model, --max-calls and --token-budget")
    try:
        budget = EvaluationBudget(args.max_calls,args.token_budget)
    except ValueError as error:
        parser.error(str(error))
    if args.budget_mode=="strict":
        print(json.dumps({"status":"BLOCKED","model_calls":0,
            "reason":"The Codex CLI has no verified hard token ceiling. Strict execution is refused before any model call. Explicit observed mode can overshoot within an in-flight turn."}))
        return 2
    try:
        cli = codex_command(args.codex_bin)
        cli_version = codex_version(cli)
    except ValueError as error:
        parser.error(str(error))
    store = TaskStore(ROOT)
    directory = store.root / "evaluations" / run_id()
    directory.mkdir(parents=True, exist_ok=False)
    original = source_state(ROOT)
    values, stopped = [], None
    schema = directory/"decision-schema.json"
    write_json(schema,{"type":"object","additionalProperties":False,"required":["decision","references","explanation"],
        "properties":{"decision":{"type":"string","enum":["PLAN","READY","BLOCKED","ANSWER"]},
            "references":{"type":"array","maxItems":12,"items":{"type":"string","maxLength":256}},
            "explanation":{"type":"string","maxLength":4000}}})
    def save():
        complete = len(values)==len(cases)*args.repeats
        status = "BLOCKED" if stopped else "PASS" if complete and all(item["assessment"]["status"]=="PASS" for item in values) else "FAIL" if complete else "IN_PROGRESS"
        write_json(directory/"results.json",{"schema_version":2,"source":original,"model":args.model,
            "codex_cli_version":cli_version,"budget":budget.snapshot(),"stopped":stopped,
            "status":status,"profile":args.profile,
            "planned_runs":len(cases)*args.repeats,"runs":values,
            "isolation":"disposable repositories/local bare origins; account integrations and network disabled"})
    save()
    scratch_root = store.root / "eval-scratch"
    scratch_root.mkdir(parents=True,exist_ok=True)
    with resource_lease(ROOT,"evaluation"), tempfile.TemporaryDirectory(prefix="tenon-eval-",dir=scratch_root) as temporary:
        temporary = Path(temporary).resolve()
        if not temporary.is_relative_to(scratch_root.resolve()):
            raise ValueError("Evaluation scratch escaped its owned directory")
        for case in cases:
            for repeat in range(args.repeats):
                try:
                    budget.ensure_available()
                except ValueError as error:
                    stopped = str(error)
                    break
                case_id = identifier(case["id"])
                label = f"{case_id}-{repeat + 1}"
                fixture = temporary / label
                writable = case.get("mode")=="workspace-write"
                remote = isolate(fixture,writable=writable)
                if writable:
                    prepare_implementation(fixture)
                if case_id=="generated-drift":
                    output = fixture/"flow/ihp130/qfn32.yaml"
                    output.write_text(output.read_text(encoding="utf-8")+"# intentional evaluation drift\n",encoding="utf-8")
                before = source_state(fixture)
                before_refs = ref_snapshot(fixture)
                expected_decision = case.get("decision") or {"task-start":"PLAN","task-resume":"PLAN","verify-stale":"BLOCKED",
                    "generated-inventory":"READY","generated-drift":"BLOCKED","physical-exception":"BLOCKED",
                    "release-preparation":"BLOCKED","ordinary-question":"ANSWER"}.get(case_id)
                if expected_decision is None:
                    profile = ["doctor"] + (["--profile","pdk","--pdk","ihp130"] if case_id=="physical-readiness" else [])
                    diagnostic = subprocess.run([sys.executable,"scripts/engineering.py",*profile],cwd=fixture,
                                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
                    expected_decision = "READY" if diagnostic.returncode==0 else "BLOCKED"
                constraints = "Only scripts/eval_target.py may change. Run build/skill-eval/test_target.py. Keep all refs unchanged; do not commit or push." if writable else "Preserve all source files and Git refs; do not commit or push."
                prompt = case["prompt"] + "\nReturn a final JSON object with decision (PLAN, READY, BLOCKED or ANSWER), existing repository-relative references, and explanation.\n" + constraints + "\nThis is an isolated evaluation. Do not install, access account integrations/credentials, use network, run hardening, open PRs, merge, tag, publish or invoke normal delivery. The only origin is disposable and local."
                try:
                    mcp_overrides = configured_mcp_overrides(fixture,cli,args.profile)
                except (OSError,ValueError,subprocess.SubprocessError) as error:
                    stopped = "Effective MCP isolation cannot be confirmed ("+type(error).__name__+")"
                    values.append({"case_id":case_id,"repetition":repeat+1,"model_invoked":False,
                        "status":"BLOCKED","assessment":{"status":"FAIL","reason":stopped}})
                    save()
                    break
                argv = [*cli,"exec","--json","--ephemeral","--sandbox","workspace-write" if writable else "read-only",
                        "--disable","apps","--disable","plugins","--disable","browser_use","--disable","computer_use",
                        "-c",'web_search="disabled"',"-c",'approval_policy="never"',"-c","sandbox_workspace_write.network_access=false",
                        "-c","sandbox_workspace_write.writable_roots=[]","-c","sandbox_workspace_write.exclude_tmpdir_env_var=true",
                        "-c","sandbox_workspace_write.exclude_slash_tmp=true",
                        *(["--profile",args.profile] if args.profile else []),
                        *mcp_overrides,"--model",args.model,"--cd",str(fixture),"--output-schema",str(schema),prompt]
                budget.reserve()
                save()
                raw = directory/(label+".jsonl")
                result = run_bounded(argv,fixture,raw,args.timeout,args.max_log_bytes)
                events = read_events(raw) if raw.exists() else []
                usage = observed_usage(events)
                budget.record(usage)
                result.update(case_id=case_id,repetition=repeat+1,model=args.model,usage=usage,model_invoked=True)
                try:
                    trace = score({**case,"fixture_root":str(fixture)},events,result["exit_code"])
                    assertions, changes = fixture_assertions(case,fixture,remote,before,before_refs)
                    assertions.append(decision_assertion(case,events,expected_decision,fixture))
                    if result["status"]!="PASS" or usage is None or budget.tokens>budget.token_budget:
                        assertions.append({"kind":"execution-and-usage-budget","passed":False,"reason":result.get("reason") or budget.blocker})
                    (directory/(label+".diff")).write_bytes(git(fixture,"diff","--binary",before["sha"],"--"))
                    result["assessment"] = assess_outcome(trace,before,source_state(fixture),assertions,writable=writable,
                                                        changed_paths=changes,allowed_paths=case.get("allowed_paths",[]))
                except Exception as error:
                    stopped = "Fixture observer failed ("+type(error).__name__+"); usage and trace are retained"
                    result["assessment"] = {"status":"FAIL","reason":stopped}
                values.append(result)
                if source_state(ROOT)!=original:
                    stopped = "Original source changed during evaluation"
                    save()
                    raise RuntimeError(stopped)
                save()
                if budget.blocker or stopped:
                    stopped = stopped or budget.blocker
                    break
            if stopped:
                break
    save()
    print(str(directory / "results.json"))
    return 0 if not stopped and len(values)==len(cases)*args.repeats and all(item["assessment"]["status"] == "PASS" for item in values) else 2 if stopped else 1


if __name__ == "__main__":
    raise SystemExit(main())
