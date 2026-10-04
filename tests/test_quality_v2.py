"""Quality-v2 fixture, call accounting, failure retention, and human gates."""
import copy
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

# Allows isolated worktree tests to exercise the newest parent dependencies.
_path = Path(__file__).resolve().parents[1] / 'src/da_agent/quality_v2.py'
_spec = importlib.util.spec_from_file_location('da_agent.quality_v2', _path)
q = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(q)


def test_summary_never_drops_failed_repetitions_or_approves_pending_humans():
    sample = {'id': 'correct', 'results': [{'repetition': i, 'status': 'completed', 'automatic_verdict': 'pass', 'feedback': {'total_score': score}} for i, score in enumerate((90, 92, 95), 1)]}
    run = {'run_id': 'run', 'evaluation_version': 'request-review-v2', 'status': 'completed', 'samples': [sample]}
    assert q.summarize(run)['verdict'] == 'pending'
    assessments = [{'target_id': 'run', 'target_version': 'request-review-v2', 'sample_id': 'correct', 'repetition': i, 'result': 'pass', 'reviewer': 'operator'} for i in range(1, 4)]
    assert q.summarize(run, assessments)['verdict'] == 'pass'
    broken = copy.deepcopy(run)
    broken['samples'][0]['results'][1].update(status='failed', feedback=None)
    summary = q.summarize(broken, assessments)
    assert summary['verdict'] == 'fail' and summary['completed_calls'] == 3
    assert len(summary['samples'][0]['results']) == 3
    spread = copy.deepcopy(run)
    spread['samples'][0]['results'][0]['feedback']['total_score'] = 80
    assert q.summarize(spread, assessments)['verdict'] == 'fail'
    assert q.summarize(run, [dict(a, target_version='wrong') for a in assessments])['verdict'] == 'pending'


def test_fixed_expectations_keep_alternative_and_low_error_levels():
    sample = {'operation': 'review', 'expected_levels': {'sql_accuracy': [3, 4]}}
    assert q.automatic_verdict(sample, {'status': 'completed', 'feedback': {'criteria': [{'key': 'sql_accuracy', 'level': 4}]}}) == 'pass'
    assert q.automatic_verdict(sample, {'status': 'completed', 'feedback': {'criteria': [{'key': 'sql_accuracy', 'level': 2}]}}) == 'fail'
    assert q.automatic_verdict({'operation': 'coaching', 'expected_action': 'hint'}, {'status': 'completed', 'feedback': {'action_type': 'submit'}}) == 'fail'


def test_prepare_six_cases_for_each_fixed_task_and_coaching_mode(tmp_path):
    from da_agent.data import generate_package
    from da_agent.task_planner import assemble_plan
    from da_agent.evaluation import freeze_evaluation
    from da_agent.capabilities import CAPABILITIES
    generated = generate_package(tmp_path)
    for capability in CAPABILITIES:
        kind = capability['task_kind']
        public, private = assemble_plan(generated, {'capability_id': capability['capability_id'], 'task_kind': kind, 'difficulty': 'advanced', 'goal': capability['goal'], 'reason': 'test'}, 'plan', 0, capability['scenarios'][0], False)
        private['frozen_evaluation'] = freeze_evaluation(public, private)
        class Package:
            schema_name = generated.schema_name
            def problem(self, _): return public
            def reference(self, _): return private
        class Runner:
            lock = threading.Lock()
            pending = {}
            calls = []
            def execute(self, scope, schema, sql):
                self.calls.append(sql)
                if 'joined_rows' in sql:
                    columns, rows = ['joined_rows'], [[300]]
                elif private.get('comparison_expected'):
                    columns, rows = private['comparison_expected']['columns'], private['comparison_expected']['rows']
                else:
                    columns, rows = list(private['expected']), [list(private['expected'].values())]
                return {'execution_id': str(len(self.calls)), 'status': 'success', 'result_complete': True, 'columns': [{'name': c} for c in columns], 'rows': rows}
        runner = Runner()
        training = SimpleNamespace(runner=runner)
        attempt = {'attempt_id': 'attempt', 'contract_version': 'request-v2', 'problem_id': 'problem-001', 'dataset_id': generated.public['dataset_id'], 'release_version': 'v1', 'draft': {'sections': {}}}
        for mode in ('review', 'coaching'):
            samples = q.prepare_samples(training, attempt, Package(), mode, 'run')
            assert len(samples) == 6 and all(s['results'] == [] for s in samples)
            assert all(s['source_contract_hash'] == private['frozen_evaluation']['contract_hash'] for s in samples)
            assert tuple(s['id'] for s in samples) == (q.REVIEW_CASES if mode == 'review' else q.COACHING_CASES)
            alternative = next(s for s in samples if s['id'] == 'valid_alternative')
            if mode == 'review' and kind != 'design':
                assert 'alternate_path' in alternative['evidence'][0]['sql']
            if mode == 'coaching':
                assert all('expected' not in json.dumps(s['context']) for s in samples)
        assert len(runner.calls) == 6  # three independent DB preparations per mode


def test_evaluate_exactly_eighteen_metered_calls_retains_provider_failures(monkeypatch):
    started, finished, saved = [], [], []
    def begin(store, kind, **metadata):
        assert metadata['call_limit'] == 18 and metadata['request_id'] == 'quality-v2:run'
        assert 'attempt_id' not in metadata
        started.append(metadata)
        return 'op-' + str(len(started))
    monkeypatch.setattr(q.telemetry, 'begin', begin)
    monkeypatch.setattr(q.telemetry, 'finish', lambda store, op, status, **metadata: finished.append((op, status, metadata)))
    class Provider:
        calls = 0
        def review(self, messages):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError('SECRET_PROVIDER_ERROR')
            return {'state': 'completed', 'model': 'fixture', 'usage': {'input_tokens': 2, 'output_tokens': 3}, 'text': json.dumps({'action_type': 'none', 'reason': '개입 불필요', 'next_action': '', 'evidence_ids': [], 'evidence_state': 'unverified', 'uncertainty': ''})}
    class Package:
        def reference(self, _): return {}
    training = SimpleNamespace(store=object(), auth=Provider(), ai_lock=threading.Lock())
    samples = [{'id': str(i), 'operation': 'coaching', 'expected_action': None, 'context': {'contract_version': 'coaching-v2', 'sources': [], 'message': '질문'}, 'results': []} for i in range(6)]
    run = {'run_id': 'run', 'mode': 'coaching', 'task_kind': 'design', 'difficulty': 'advanced', 'evaluation_version': 'request-review-v2', 'problem_id': 'problem-001', 'samples': samples}
    q.evaluate(training, Package(), run, lambda sample, result: saved.append((sample, result)))
    assert len(started) == len(finished) == len(saved) == training.auth.calls == 18
    assert saved[1][1]['status'] == 'failed'
    assert saved[1][1]['repetition'] == 2
    assert 'SECRET_PROVIDER_ERROR' not in json.dumps(saved)
    assert all(result['human']['critical_error'] == 'pending' for _, result in saved)
    assert all('message' not in metadata and 'sql' not in metadata for metadata in started)

# Reuse the isolated recorder schema and temporary package-root fixture.
from test_task_generation_v2 import v2_db_client as quality_db_client, begin_v2


def test_db_operator_suite_actual_evidence_eighteen_calls_and_idempotency(quality_db_client):
    client, provider = quality_db_client
    _, ready = begin_v2(client, 'design')
    attempt_id = ready['attempt_id']
    for mode in ('review', 'coaching'):
        body = {'request_id': 'quality-' + __import__('uuid').uuid4().hex, 'attempt_id': attempt_id, 'mode': mode}
        before = provider.calls
        response = client.post('/api/quality/v2/runs', json=body)
        assert response.status_code == 200, response.text
        run_id = response.json()['run_id']
        run = client.get('/api/quality/v2/runs/' + run_id).json()
        assert run['status'] == 'completed' and run['completed_calls'] == 18
        assert len(run['samples']) == 6 and all(len(s['results']) == 3 for s in run['samples'])
        assert provider.calls - before == 18
        assert not run['semantic_approval']  # Fixture provider is not model/human quality approval.
        assert client.post('/api/quality/v2/runs', json=body).json()['run_id'] == run_id
        assert provider.calls - before == 18
        assert client.get('/api/quality/v2/runs/' + run_id + '/export').status_code == 200
        with client.app.state.store.connect() as conn:
            rows = conn.execute("SELECT payload FROM quality_operations WHERE payload->>'request_id'=%s AND payload->>'kind'='ai'", ('quality-v2:' + run_id,)).fetchall()
            assert len(rows) == 18
            assert all('sql' not in r['payload'] and 'message' not in r['payload'] and 'attempt_id' not in r['payload'] for r in rows)
