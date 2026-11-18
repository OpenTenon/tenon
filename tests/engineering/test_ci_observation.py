"""Accepted-attempt observations preserve ownership and independently unknown clocks."""
import copy
import json
import unittest
from unittest import mock

from test_engineering import ROOT
from ci_gate import REQUIRED_JOBS
from ci_observation import accepted_record, phase_metrics, TIMINGS
from task_backends import GitHub
from task_lifecycle import ci_green

HEAD, DEV, MERGE = 'a'*40, 'b'*40, 'c'*40
REPO = 'fixture/repo'
REQUIRED = REQUIRED_JOBS | {'ci-required'}


def snapshot(event='pull_request', attempt=1):
    provider = HEAD if event=='pull_request' else DEV
    run = {'id':42,'run_attempt':attempt,'head_sha':provider,'event':event,'path':'.github/workflows/ci.yml',
           'status':'completed','conclusion':'success','pull_requests':[{'number':7}],
           'created_at':'2026-11-16T10:00:02Z','run_started_at':'2026-11-16T10:00:00Z','updated_at':'2035-01-01T00:00:00Z'}
    jobs = [{'name':name,'run_id':42,'run_attempt':attempt,'head_sha':MERGE if event=='pull_request' else DEV,
             'status':'completed','conclusion':'success','started_at':f'2026-11-16T10:00:0{index}Z',
             'completed_at':f'2026-11-16T10:00:1{index}Z',
             'labels':['windows-2022'] if 'windows' in name else ['ubuntu-22.04']}
            for index,name in enumerate(sorted(REQUIRED))]
    return {'workflow_run':run,'jobs':jobs,'pull_request':{'number':7,'merge_commit_sha':MERGE}}


def record(value=None, **options):
    value = value or snapshot()
    event = value['workflow_run']['event']
    return accepted_record(value,task_id='owned',repository=REPO,candidate_head=HEAD,
        accepted_head=HEAD if event=='pull_request' else DEV,event=event,observed_at=options.pop('observed_at','2026-11-16T10:00:20Z'),**options)


def timing(value, event='pull_request', **options):
    return phase_metrics([value],task_id='owned',repository=REPO,candidate_head=HEAD,
        accepted_head=HEAD if event=='pull_request' else DEV,event=event,required_jobs=REQUIRED,
        cleaned_at=options.pop('cleaned_at','2026-11-16T10:01:00Z'),**options)


class ObservationTests(unittest.TestCase):
    def test_allowlist_and_distinct_candidate_provider_heads_keep_raw_clocks_without_mutation(self):
        value=snapshot(); value['secret']='must-not-retain'; value['jobs'][0]['steps']=[{'env':'must-not-retain'}]
        value['workflow_run']['head_sha']=MERGE
        original=copy.deepcopy(value)
        stored=record(value)
        self.assertEqual(value,original)
        self.assertNotIn('must-not-retain',json.dumps(stored))
        self.assertNotIn('updated_at',json.dumps(stored))
        self.assertNotIn('run_started_at',json.dumps(stored))
        self.assertEqual(stored['observation']['candidate_head_sha'],HEAD)
        self.assertEqual(stored['observation']['provider_head_sha'],MERGE)
        result=timing(stored)
        self.assertEqual([result[name] for name in TIMINGS],[15,60,20,40,5,40])

    def test_same_attempt_preserves_first_observation_but_new_attempt_has_its_own_clock(self):
        first=record()
        repeated=record(previous=first,observed_at='2026-11-16T12:00:00Z')
        self.assertEqual(repeated,first)
        newer=record(snapshot(attempt=2),previous=first,observed_at='2026-11-16T10:00:30Z')
        self.assertEqual(newer['observation']['run_attempt'],2)
        self.assertEqual(newer['observation']['observed_at'],'2026-11-16T10:00:30Z')
        legacy={key:value for key,value in first.items() if key!='observation'}
        self.assertNotIn('observation',record(previous=legacy))
        self.assertTrue(all(timing(legacy)[name] is None for name in TIMINGS))

    def test_missing_and_negative_clocks_remain_individually_unknown_without_updated_at_fallback(self):
        value=snapshot(); value['jobs'][0]['completed_at']=None
        result=timing(record(value))
        self.assertIsNone(result['hosted_span_seconds']); self.assertIsNone(result['all_job_wall_seconds'])
        self.assertIsNone(result['ci_complete_to_confirmation_seconds'])
        self.assertEqual(result['confirmation_to_cleaned_seconds'],40)
        value['jobs'][0]['completed_at']='2026-11-16T09:00:00Z'
        self.assertIsNone(timing(record(value))['hosted_span_seconds'])
        missing=timing(record(observed_at=None))
        self.assertEqual(missing['hosted_span_seconds'],15)
        self.assertIsNone(missing['ci_complete_to_confirmation_seconds'])
        self.assertIsNone(missing['confirmation_to_cleaned_seconds'])
        negative=timing(record(observed_at='2026-11-16T10:00:01Z'),cleaned_at='2026-11-16T10:00:00Z')
        self.assertIsNone(negative['ci_complete_to_confirmation_seconds'])
        self.assertIsNone(negative['confirmation_to_cleaned_seconds'])

    def test_foreign_invalid_incomplete_and_conflicting_observations_never_supply_timing(self):
        changes=[lambda row:row.update(task_id='foreign'),lambda row:row.update(repository='foreign/repo'),
            lambda row:row.update(candidate_head_sha=DEV),lambda row:row.update(provider_head_sha=DEV),
            lambda row:row.update(schema_version=True),lambda row:row.update(event='push'),
            lambda row:row['jobs'][0].update(run_attempt=2),lambda row:row['jobs'][0].update(head_sha=DEV),
            lambda row:row['jobs'][0].update(status='in_progress'),lambda row:row['jobs'].pop(),
            lambda row:row['jobs'].append(copy.deepcopy(row['jobs'][0]))]
        for change in changes:
            stored=record(); change(stored['observation'])
            with self.subTest(change=change):self.assertTrue(all(timing(stored)[name] is None for name in TIMINGS))
        first,second=record(),record(observed_at='2026-11-16T10:00:21Z')
        result=phase_metrics([first,second],task_id='owned',repository=REPO,candidate_head=HEAD,accepted_head=HEAD,
                             event='pull_request',required_jobs=REQUIRED)
        self.assertTrue(all(result[name] is None for name in TIMINGS))

    def test_push_head_and_unknown_platform_are_not_guessed(self):
        stored=record(snapshot('push'))
        result=timing(stored,'push')
        self.assertEqual(result['candidate_head_sha'],HEAD); self.assertEqual(result['provider_head_sha'],DEV)
        stored['observation']['jobs'][0]['labels']=None
        result=timing(stored,'push')
        self.assertEqual(result['all_job_wall_seconds'],60)
        self.assertIsNone(result['windows_job_wall_seconds']); self.assertIsNone(result['linux_job_wall_seconds'])

    def test_nonstring_head_data_remains_unknown_without_building_unhashable_sets(self):
        stored=record()
        stored['observation']['pr_merge_head_sha']=[]
        self.assertTrue(all(timing(stored)[name] is None for name in TIMINGS))
        for field,value in (('candidate_head',[]),('accepted_head',{})):
            options={'candidate_head':HEAD,'accepted_head':HEAD}
            options[field]=value
            result=phase_metrics([record()],task_id='owned',repository=REPO,event='pull_request',required_jobs=REQUIRED,**options)
            self.assertTrue(all(result[name] is None for name in TIMINGS))

    def test_parseable_clock_outside_utc_range_is_unknown(self):
        for clock in ('0001-01-01T00:00:01+23:59','9999-12-31T23:59:59-23:59'):
            with self.subTest(clock=clock):
                result=timing(record(observed_at=clock))
                self.assertIsNone(result['controller_observed_at'])
                self.assertIsNone(result['ci_complete_to_confirmation_seconds'])
                self.assertIsNone(result['confirmation_to_cleaned_seconds'])
                value=snapshot(); value['jobs'][0]['started_at']=clock
                self.assertIsNone(timing(record(value))['hosted_span_seconds'])


class AttemptJobsTests(unittest.TestCase):
    def backend(self, initial, current=None, latest=None, jobs=None):
        backend=object.__new__(GitHub); backend.name=REPO
        selections=[initial,latest or initial]
        count=[]
        def api(endpoint):
            if endpoint.startswith('actions/runs?'):
                value=selections[min(len(count),1)]; count.append(True)
                return {'workflow_runs':copy.deepcopy(value)}
            if '/attempts/' in endpoint:return {'jobs':copy.deepcopy(jobs if jobs is not None else snapshot(attempt=initial[-1]['run_attempt'])['jobs'])}
            if endpoint.startswith('actions/runs/'):return copy.deepcopy(current or initial[-1])
            raise AssertionError(endpoint)
        backend.api=mock.Mock(side_effect=api)
        backend.pull=mock.Mock(side_effect=AssertionError('explicit association must not be replaced'))
        return backend

    def test_exact_attempt_endpoint_and_current_success_remain_qualified(self):
        run=snapshot(attempt=2)['workflow_run']; backend=self.backend([run])
        result=backend.latest_run(HEAD,'pull_request',7)
        self.assertTrue(ci_green(result,HEAD,'pull_request'))
        endpoints=[call.args[0] for call in backend.api.call_args_list]
        self.assertIn('actions/runs/42/attempts/2/jobs?per_page=100&page=1',endpoints)
        self.assertIn('actions/runs/42',endpoints)
        self.assertFalse(any('filter=latest' in value for value in endpoints))

    def test_rerun_starting_between_list_and_exact_old_jobs_never_authorizes_old_success(self):
        old=snapshot()['workflow_run']
        current={**old,'run_attempt':2,'status':'in_progress','conclusion':None}
        backend=self.backend([old],current=current)
        self.assertIsNone(backend.latest_run(HEAD,'pull_request',7))
        backend.api.assert_any_call('actions/runs/42/attempts/1/jobs?per_page=100&page=1')

    def test_newer_run_discovered_after_jobs_never_returns_earlier_success(self):
        old=snapshot()['workflow_run']; newer={**old,'id':43,'status':'in_progress','conclusion':None}
        backend=self.backend([old],latest=[old,newer])
        self.assertIsNone(backend.latest_run(HEAD,'pull_request',7))

    def test_latest_pending_attempt_and_five_jobs_are_not_replaced_by_old_success(self):
        old=snapshot()['workflow_run']; current={**old,'run_attempt':2,'status':'in_progress','conclusion':None}
        jobs=snapshot(attempt=2)['jobs'][:-1]
        backend=self.backend([old,current],jobs=jobs)
        result=backend.latest_run(HEAD,'pull_request',7)
        self.assertEqual(result['workflow_run']['run_attempt'],2); self.assertEqual(len(result['jobs']),5)
        self.assertFalse(ci_green(result,HEAD,'pull_request'))
        self.assertEqual(len(backend.api.call_args_list),2)

    def test_jobs_with_wrong_attempt_do_not_become_current_proof(self):
        run=snapshot(attempt=2)['workflow_run']; backend=self.backend([run],jobs=snapshot()['jobs'])
        self.assertFalse(ci_green(backend.latest_run(HEAD,'pull_request',7),HEAD,'pull_request'))
