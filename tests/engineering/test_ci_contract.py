"""Contract drift must fail without modifying workflows or remote settings."""
from __future__ import annotations
import copy
import importlib.util
import json
import sys
import hashlib
import shutil
import os
import subprocess
import contextlib
import io
from unittest import mock
import unittest
from pathlib import Path
from test_engineering import ROOT, RepositoryFixture
import host_profile
import ci_contract
import github_rules
import tenon
from ci_gate import REQUIRED_JOBS


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.rules = {branch: json.loads((ROOT / '.github/branch-protection' / (branch + '.json')).read_text())
                      for branch in ('dev', 'main')}
        self.pins = json.loads((ROOT / 'tools/toolchain.json').read_text())
        self.workflow = {'permissions': {'contents': 'read'},
                         'on': {'push': {'branches': ['dev','main']}, 'pull_request': {'branches': ['dev','main']}, 'workflow_dispatch': None},
                         'jobs': {name: {'runs-on': 'windows-2022' if name in ('tools-windows','tools-windows-extra') else self.pins['linux_runner'],
                                         'timeout-minutes': 15, 'steps': [{'uses': 'actions/checkout@' + '1' * 40}]}
                                  for name in REQUIRED_JOBS | {'required'}}}
        self.workflow['jobs']['required'].update(name='ci-required', needs=sorted(REQUIRED_JOBS), **{'if':'always()'})
        for name, job in self.workflow['jobs'].items():
            command = 'python3 scripts/ci_gate.py' if name == 'required' else 'python scripts/engineering.py verify --group tools' if name in ('tools-windows','tools-windows-extra') else 'python3 scripts/engineering.py verify --group ' + name
            job['steps'].append({'run': command})
        self.workflow['jobs']['check']['steps'].append({'run':'python3 scripts/ci_contract.py --root .'})
        for index, name in enumerate(('tools-windows','tools-windows-extra')):
            self.workflow['jobs'][name]['steps'][-1]['run'] += ' --shard-index ' + str(index) + ' --shard-count 2'
            self.workflow['jobs'][name]['steps'].append({'uses':'actions/upload-artifact@'+'2'*40,'if':'always()',
                'with':{'name':ci_contract.windows_artifact(index),'path':'build/verification/'}})
        gate = self.workflow['jobs']['required']
        gate['env'] = {'CI_NEEDS':'$'+'{{ toJSON(needs) }}'}
        for index in (0,1):
            gate['steps'].append({'uses':'actions/download-artifact@'+'3'*40,
                'with':{'name':ci_contract.windows_artifact(index),'path':'build/ci-windows/'+str(index)}})
        gate['steps'].append({'run':'python3 scripts/ci_gate.py --windows-shards build/ci-windows'})


    def test_current_contract_is_valid_and_missing_or_extra_gate_members_fail(self):
        self.assertEqual(ci_contract.check_data(self.workflow, self.rules, self.pins), [])
        for mode in ('missing-job', 'missing-need', 'extra-job', 'conditional-gate'):
            value = copy.deepcopy(self.workflow)
            if mode == 'missing-job':
                del value['jobs']['sim']
            elif mode == 'missing-need':
                value['jobs']['required']['needs'].remove('sim')
            elif mode == 'extra-job':
                value['jobs']['unaggregated'] = value['jobs']['sim']
            else:
                value['jobs']['required']['if'] = 'success()'
            with self.subTest(mode=mode):
                self.assertTrue(ci_contract.check_data(value, self.rules, self.pins))

    def test_mutable_actions_path_filters_tokens_and_branch_bypasses_fail(self):
        for mode in ('tag', 'paths', 'permissions', 'job-permissions', 'branch', 'noop', 'conditional-step', 'false-step', 'false-job'):
            workflow, rules = copy.deepcopy(self.workflow), copy.deepcopy(self.rules)
            if mode == 'tag':
                workflow['jobs']['sim']['steps'][0]['uses'] = 'actions/checkout@v4'
            elif mode == 'paths':
                workflow['on']['pull_request']['paths'] = ['scripts/**']
            elif mode == 'permissions':
                workflow['permissions']['contents'] = 'write'
            elif mode == 'job-permissions':
                workflow['jobs']['sim']['permissions'] = {'contents':'write'}
            elif mode == 'noop':
                workflow['jobs']['sim']['steps'] = workflow['jobs']['sim']['steps'][:1]
            elif mode == 'conditional-step':
                workflow['jobs']['sim']['steps'][-1]['if'] = 'false'
            elif mode == 'false-step':
                workflow['jobs']['sim']['steps'][-1]['if'] = False
            elif mode == 'false-job':
                workflow['jobs']['sim']['if'] = False
            else:
                rules['dev']['allow_force_pushes'] = True
            with self.subTest(mode=mode):
                self.assertTrue(ci_contract.check_data(workflow, rules, self.pins))

    def test_shard_bypasses_softening_order_and_stale_artifact_names_fail(self):
        for mode in ('full-in-windows','wrong-index','wrong-count','wrong-runner','old-upload',
                     'old-download','soft-download','conditional-download','soft-report',
                     'missing-report-gate','gate-before-download','external-run','missing-needs-context'):
            workflow = copy.deepcopy(self.workflow)
            gate = workflow['jobs']['required']
            if mode == 'full-in-windows':
                workflow['jobs']['tools-windows']['steps'][1]['run'] = 'python scripts/engineering.py verify --group tools'
            elif mode == 'wrong-index':
                workflow['jobs']['tools-windows-extra']['steps'][1]['run'] = 'python scripts/engineering.py verify --group tools --shard-index 0 --shard-count 2'
            elif mode == 'wrong-count':
                workflow['jobs']['tools-windows']['steps'][1]['run'] = 'python scripts/engineering.py verify --group tools --shard-index 0 --shard-count 3'
            elif mode == 'wrong-runner':
                workflow['jobs']['tools-windows-extra']['runs-on'] = 'ubuntu-22.04'
            elif mode == 'old-upload':
                workflow['jobs']['tools-windows']['steps'][-1]['with']['name'] = 'verification-windows'
            elif mode == 'old-download':
                gate['steps'][2]['with']['name'] = 'verification-windows-shard-0-attempt-1'
            elif mode == 'soft-download':
                gate['steps'][2]['continue-on-error'] = True
            elif mode == 'conditional-download':
                gate['steps'][2]['if'] = 'false'
            elif mode == 'soft-report':
                gate['steps'][-1]['continue-on-error'] = True
            elif mode == 'missing-report-gate':
                gate['steps'].pop()
            elif mode == 'gate-before-download':
                gate['steps'][1],gate['steps'][-1] = gate['steps'][-1],gate['steps'][1]
            elif mode == 'external-run':
                gate['steps'][2]['with']['run-id'] = 'previous'
            else:
                gate['env'].pop('CI_NEEDS')
            with self.subTest(mode=mode):
                self.assertTrue(ci_contract.check_data(workflow,self.rules,self.pins))

    def test_controller_inventory_is_read_without_executing_target_code(self):
        text = "raise RuntimeError('must not execute')\nREQUIRED_JOBS = {'new-job'}\n"
        self.assertEqual(ci_contract.literal(text, 'REQUIRED_JOBS'), {'new-job'})
        self.assertTrue(ci_contract.check_data(self.workflow, self.rules, self.pins, ci_contract.literal(text, 'REQUIRED_JOBS')))

    def test_noop_group_targets_and_missing_independent_audit_are_rejected(self):
        noop = {group:['help'] for group in ci_contract.MINIMUM_TARGETS}
        self.assertTrue(ci_contract.check_data(self.workflow,self.rules,self.pins,verification_groups=noop))
        workflow = copy.deepcopy(self.workflow)
        workflow['jobs']['check']['steps'] = [step for step in workflow['jobs']['check']['steps']
                                             if step.get('run') != 'python3 scripts/ci_contract.py --root .']
        self.assertTrue(ci_contract.check_data(workflow,self.rules,self.pins))

    def test_live_audit_compares_semantics_and_never_calls_a_write(self):
        calls = []
        def query(repo, endpoint):
            calls.append(endpoint)
            if not endpoint:
                return {'delete_branch_on_merge': False, 'allow_merge_commit': True}
            branch = endpoint.split('/')[1]
            data = copy.deepcopy(self.rules[branch])
            for name in ('enforce_admins','allow_force_pushes','allow_deletions','required_conversation_resolution'):
                data[name] = {'enabled': data[name], 'url': 'ignored API decoration'}
            if branch == 'main':
                data['restrictions']['users'] = [{'login': name} for name in data['restrictions']['users']]
            return data
        result = github_rules.audit('fixture/repo', ROOT, query=query)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(calls, ['branches/dev/protection','branches/main/protection',''])
        original = query
        def drift(repo, endpoint):
            value = original(repo, endpoint)
            if endpoint == 'branches/dev/protection':
                value['required_status_checks']['strict'] = False
            return value
        self.assertEqual(github_rules.audit('fixture/repo', ROOT, query=drift)['status'], 'FAIL')

    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'Linux workflow parser is an existing CI dependency')
    def test_actual_workflow_is_checked(self):
        self.assertEqual(ci_contract.audit_source(ROOT)['status'], 'PASS')


class HostProfileTests(RepositoryFixture):
    def test_registered_argv_is_reused_and_changed_inputs_block_it(self):
        helper = self.root / 'build/existing-verifier.py'
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("print('existing host verifier')\n", encoding='utf-8')
        argv = [sys.executable, str(helper), '{worktree}']
        host_profile.configure(self.root, argv, [helper])
        value = host_profile.load(self.root)
        self.assertEqual(value['verify_command'], argv)
        self.assertEqual(len(value['profile_sha256']), 64)
        helper.write_text("print('changed helper')\n", encoding='utf-8')
        with self.assertRaises(ValueError):
            host_profile.load(self.root)

    def test_absent_profile_preserves_legacy_defaults(self):
        self.assertIsNone(host_profile.load(self.root))

    def test_profile_digest_and_command_come_from_the_same_atomic_snapshot(self):
        from task_store import TaskStore
        host_profile.configure(self.root, ['original-verifier'], [])
        path = TaskStore(self.root).root/'host.json'
        original = Path.read_bytes
        payload = original(path)
        replacement = json.loads(payload)
        replacement['verify_command'] = ['replacement-verifier']
        def replaced(candidate):
            data = original(candidate)
            if candidate == path:
                path.write_text(json.dumps(replacement), encoding='utf-8')
            return data
        with mock.patch.object(Path, 'read_bytes', replaced):
            value = host_profile.load(self.root)
        self.assertEqual(value['verify_command'], ['original-verifier'])
        self.assertEqual(value['profile_sha256'], hashlib.sha256(payload).hexdigest())

    def helper(self):
        path=self.root/'build/existing-helper.py'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text("print('host helper')\n",encoding='utf-8')
        return path

    def entry(self,name,command=None):
        from test_entrypoint import Backend
        return tenon.start_workspace(self.root,name,'profile','Host profile fixture',['scripts/'+name+'.py'],['make verify'],
            verify_command=command,workspace_root=self.root/'build/workspaces',backend=Backend())

    def test_new_task_and_intent_freeze_profile_but_explicit_command_is_authoritative(self):
        from task_store import TaskStore
        helper=self.helper(); command=[sys.executable,str(helper),'{worktree}']
        host_profile.configure(self.root,command,[helper])
        profile=host_profile.load(self.root)
        value=self.entry('profile-default')
        intent=json.loads((TaskStore(self.root).root/'operations/workspace-setup/profile-default.json').read_text())
        self.assertEqual(value['verify_command'],command)
        self.assertEqual(value['host_profile_sha256'],profile['profile_sha256'])
        self.assertEqual(value['verification_inputs'],intent['contract']['verification_inputs'])
        helper.write_text('changed\n',encoding='utf-8')
        explicit=self.entry('explicit-command',['explicit-existing-verifier','{worktree}'])
        self.assertEqual(explicit['verify_command'],['explicit-existing-verifier','{worktree}'])
        self.assertIsNone(explicit['host_profile_sha256'])

    def test_frozen_helper_is_checked_before_execution_and_evidence_reuse(self):
        from task_lifecycle import Lifecycle
        from test_entrypoint import Backend
        helper=self.helper()
        host_profile.configure(self.root,[sys.executable,str(helper)],[helper])
        value=self.entry('frozen-helper')
        root=Path(value['worktree'])
        task={**value,'verified_head':'same-head','verification_report':'unused-report.json'}
        helper.write_text('modified\n',encoding='utf-8')
        with self.assertRaises(ValueError):
            tenon.verification_argv(task,root)
        controller=Lifecycle(self.root,'frozen-helper',Backend())
        with mock.patch('task_lifecycle.validate_evidence') as reuse, mock.patch('task_lifecycle.execute') as execute:
            with self.assertRaises(ValueError):
                controller.verification(task,root,'same-head')
            reuse.assert_not_called();execute.assert_not_called()

    def test_legacy_interrupted_choice_does_not_adopt_later_profile(self):
        helper=self.helper()
        host_profile.configure(self.root,[sys.executable,str(helper)],[helper])
        self.assertEqual(host_profile.bind_verifier(self.root,None,{'verify_command':None}),{'verify_command':None})

    def test_python_paths_are_existing_and_scoped_to_audit_context(self):
        tools=self.root/'build/python-tools';tools.mkdir(parents=True)
        host_profile.configure(self.root,['existing-verifier'],[],[tools])
        before=sys.path[:];environment=os.environ.copy()
        with host_profile.python_context(self.root):
            self.assertIn(str(tools.resolve()),sys.path)
        self.assertEqual(sys.path,before)
        self.assertEqual(dict(os.environ),environment)
        with self.assertRaises(ValueError):
            host_profile.configure(self.root,['existing-verifier'],[],[self.root/'missing-tools'])


class StableAuditTests(RepositoryFixture):
    def test_source_and_published_rules_receive_the_same_resolved_commit(self):
        original=self.run_git('rev-parse','HEAD').decode().strip()
        self.run_git('update-ref','refs/heads/audit-target','HEAD')
        (self.root/'input.txt').write_text('next commit\n',encoding='utf-8')
        self.run_git('add','input.txt');self.run_git('commit','--quiet','-m','later source')
        latest=self.run_git('rev-parse','HEAD').decode().strip()
        def source(repo,ref):
            self.run_git('update-ref','refs/heads/audit-target',latest)
            self.assertEqual(ref,original)
            return {'status':'PASS','commit':ref}
        with (mock.patch.object(ci_contract,'audit_source',side_effect=source),
              mock.patch.object(github_rules,'audit',return_value={'status':'FAIL'}) as published,
              mock.patch('task_backends.repository_name',return_value='fixture/repo')):
            value=tenon.audit(self.root,'audit-target',True)
        self.assertEqual(value['commit'],original)
        self.assertEqual(value['status'],'FAIL')
        published.assert_called_once_with('fixture/repo',self.root,original)

    def test_stable_audit_failure_exit_code_is_not_success(self):
        with mock.patch.object(tenon,'audit',return_value={'status':'FAIL'}),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tenon.main(['--repo',str(self.root),'audit','--ref','HEAD']),2)


class GitAliasTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.config_environment=mock.patch.dict(os.environ,{'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_SYSTEM':os.devnull})
        self.config_environment.start();self.addCleanup(self.config_environment.stop)
        self.root=self.root/'repository with spaces'
        self.root.mkdir()
        self.run_git('init','--quiet');self.run_git('config','user.name','Tenon alias fixture')
        self.run_git('config','user.email','fixture@example.invalid')
        shutil.copytree(ROOT/'scripts',self.root/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        from controller_leases import EARLY_GUARD
        import ast
        tree=ast.parse((self.root/'scripts/tenon.py').read_text(encoding='utf-8'))
        install=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='install')
        literal=next(node.value.value for node in ast.walk(install) if isinstance(node,ast.Assign)
                     and any(isinstance(name,ast.Name) and name.id=='launcher' for name in node.targets) and isinstance(node.value,ast.Constant))
        (self.root/'scripts/tenon.py').write_text("from __future__ import annotations\n"+EARLY_GUARD+"\ndef install():\n    launcher = "+repr(literal)+"\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\nraise SystemExit(7 if 'fail' in sys.argv else 0)\n",encoding='utf-8')
        self.run_git('add','.');self.run_git('commit','--quiet','-m','source-pinned alias fixture')
        self.run_git('update-ref','refs/remotes/origin/dev','HEAD')

    def install(self,requested=True):
        from test_entrypoint import Backend
        return tenon.install(self.root,Backend(),git_alias=requested)

    def test_real_git_alias_preserves_argv_and_failure_status(self):
        value=self.install()
        self.assertEqual(value['git_alias'],'INSTALLED')
        marker=self.root/'must-not-be-created'
        arguments=['alpha beta','literal; echo','$(touch '+str(marker)+')','"quoted"','中文']
        result=subprocess.run(['git','-C',str(self.root),'tenon',*arguments],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual(json.loads(result.stdout)[2:],arguments)
        self.assertFalse(marker.exists())
        failed=subprocess.run(['git','-C',str(self.root),'tenon','fail'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(failed.returncode,7)
        self.install(False)
        from task_store import TaskStore
        metadata=json.loads((TaskStore(self.root).root/'bin/installation.json').read_text())
        self.assertEqual(metadata['git_alias']['scope'],'local')

    def test_unrelated_local_alias_is_preserved(self):
        self.run_git('config','--local','alias.tenon','!echo existing-local')
        self.assertEqual(self.install()['git_alias'],'PRESERVED_EXISTING')
        self.assertEqual(self.run_git('config','--local','--get','alias.tenon').decode().strip(),'!echo existing-local')

    def test_unrelated_global_alias_is_not_shadowed_or_changed(self):
        config=self.root.parent/'global-config'
        config.write_text('[alias]\n tenon = !echo existing-global\n',encoding='utf-8')
        before=config.read_bytes()
        with mock.patch.dict(os.environ,{'GIT_CONFIG_GLOBAL':str(config)}):
            self.assertEqual(self.install()['git_alias'],'PRESERVED_EXISTING')
            local=subprocess.run(['git','-C',str(self.root),'config','--local','--get','alias.tenon'],stdout=subprocess.PIPE)
            self.assertEqual(local.returncode,1)
        self.assertEqual(config.read_bytes(),before)


class SourceBindingTests(RepositoryFixture):
    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'Linux workflow parser is an existing CI dependency')
    def test_audited_ref_uses_its_own_controller_inventory(self):
        shutil.copytree(ROOT/'.github', self.root/'.github')
        (self.root/'tools').mkdir()
        (self.root/'scripts').mkdir()
        shutil.copy2(ROOT/'tools/toolchain.json', self.root/'tools/toolchain.json')
        for name in ('ci_gate.py', 'engineering.py'):
            shutil.copy2(ROOT/'scripts'/name, self.root/'scripts'/name)
        self.run_git('add', '.')
        self.run_git('commit', '--quiet', '-m', 'consistent source contract')
        original = self.run_git('rev-parse', 'HEAD').decode().strip()
        names = sorted(REQUIRED_JOBS | {'missing-required-job'})
        (self.root/'scripts/ci_gate.py').write_text('REQUIRED_JOBS = ' + repr(set(names)) + '\n', encoding='utf-8')
        self.run_git('add', 'scripts/ci_gate.py')
        self.run_git('commit', '--quiet', '-m', 'target-only controller drift')
        self.assertEqual(ci_contract.audit_source(self.root, 'HEAD')['status'], 'FAIL')
        self.assertEqual(ci_contract.audit_source(self.root, original)['status'], 'PASS')
