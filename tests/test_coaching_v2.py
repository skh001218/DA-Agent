import copy
import json
import unittest
from da_agent.coaching import build_context, normalize_coaching, should_coach
from da_agent.evaluation import freeze_evaluation, normalize_evaluation, verify_evidence, verify_comparison, verify_for_contract, exposure_detected
from da_agent.quality_fixtures import run_repeated, summarize_repeated

class CoachingV2Tests(unittest.TestCase):
    def setUp(self):
        self.public = {'task_kind': 'calculation', 'title': '과제', 'weights': {'sql_accuracy': 100}, 'expected': {'n': 987}, 'private': {'seed': 123}}
        self.attempt = {'attempt_id': 'a', 'draft': {'sections': {'hypothesis': '확인 필요'}}}
        self.evidence = {'saved_execution_id': 'e', 'attempt_id': 'a', 'result': {'status': 'success', 'result_complete': True, 'columns': [{'name': 'n'}], 'rows': [[42]]}}
        self.report = {'attempt_id': 'a', 'claims': [{'claim_id': 'c', 'evidence_refs': [{'saved_execution_id': 'e'}]}]}
        self.frozen = freeze_evaluation(self.public, {'weights': {'sql_accuracy': 100}, 'expected': {'n': 42}})
    def response(self, **kw):
        value = dict(action_type='check', reason='집계를 확인', next_action='조건을 확인하세요', evidence_ids=['e'], evidence_state='saved', uncertainty='')
        value.update(kw)
        return {'status': 'completed', 'feedback': json.dumps(value, ensure_ascii=False)}
    def review(self, **kw):
        value = {'criteria': [{'key': 'sql_accuracy', 'level': 4, 'reason': '실행 근거 확인', 'claim_ids': ['c'], 'saved_execution_ids': ['e']}], 'strengths': [], 'improvements': [], 'next_steps': [], 'uncertainty': ''}
        value.update(kw)
        return {'status': 'completed', 'feedback': json.dumps(value)}
    def test_public_context_and_sources(self):
        ctx = build_context(self.public, self.attempt, [{'action_id': 'x', 'message': '답한 질문', 'response': {}}, {'message': '임시 원문', 'transient': True}], '질문', self.evidence)
        self.assertNotIn('expected', json.dumps(ctx))
        self.assertNotIn('임시 원문', json.dumps(ctx, ensure_ascii=False))
        self.assertEqual(normalize_coaching(self.response(), ctx)['status'], 'completed')
        self.assertEqual(normalize_coaching(self.response(evidence_ids=['fake']), ctx)['status'], 'failed')
        self.assertEqual(normalize_coaching(self.response(evidence_state='public'), ctx)['status'], 'failed')
    def test_temporary_state_and_no_intervention(self):
        evidence = {'execution_id': 'temp', 'rows': [[1]]}
        ctx = build_context(self.public, self.attempt, [], '질문', evidence)
        self.assertTrue(ctx['transient'])
        result = normalize_coaching(self.response(action_type='none', next_action='', evidence_ids=[], evidence_state='unverified'), ctx)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(should_coach('sql'))
        self.assertTrue(should_coach('sql', True))
    def test_full_saved_rows_only(self):
        self.assertEqual(verify_evidence(self.evidence, {'n': 42})['status'], 'verified')
        altered = copy.deepcopy(self.evidence)
        altered['result']['rows'] = [[43]]
        self.assertEqual(verify_evidence(altered, {'n': 42})['status'], 'mismatch')
        altered['result']['result_complete'] = False
        self.assertEqual(verify_evidence(altered, {'n': 42})['status'], 'unverified')
        self.assertEqual(verify_evidence(self.evidence, {'missing': 42})['status'], 'unverified')
    def test_fixed_weights_review_and_false_approval(self):
        result = normalize_evaluation(self.review(), self.report, [self.evidence], self.frozen)
        self.assertEqual(result['feedback']['total_score'], 100)
        bad = copy.deepcopy(self.evidence)
        bad['result']['rows'] = [[43]]
        self.assertEqual(normalize_evaluation(self.review(), self.report, [bad], self.frozen)['error']['code'], 'unverified_calculation')
        bad['attempt_id'] = 'other'
        self.assertEqual(normalize_evaluation(self.review(), self.report, [bad], self.frozen)['status'], 'failed')
        altered = copy.deepcopy(self.frozen)
        altered['weights']['sql_accuracy'] = 99
        self.assertEqual(normalize_evaluation(self.review(), self.report, [self.evidence], altered)['status'], 'failed')
        with self.assertRaises(ValueError):
            freeze_evaluation({'task_kind': 'design'}, {'weights': {'sql_accuracy': 100}})
    def test_leak_checks_and_visible_number(self):
        self.assertTrue(exposure_detected({'text': 'SELECT hidden FROM secrets'}, {'sql': 'SELECT hidden FROM secrets'}, {}))
        self.assertTrue(exposure_detected({'text': '사건X가 원인'}, {'event_name': '사건X'}, {}))
        self.assertFalse(exposure_detected({'text': '42'}, {'expected': {'n': 42}}, self.evidence))
        self.assertTrue(exposure_detected({'text': '42'}, {'expected': {'n': 42}}, {'report': {'claim': '42'}}))
        ctx = build_context(self.public, self.attempt, [], '질문', self.evidence)
        self.assertEqual(normalize_coaching(self.response(reason='사건X가 원인'), ctx, {'event_name': '사건X'})['error']['code'], 'answer_exposure')
    def test_repetitions_keep_failed_calls_and_human_pending(self):
        ctx = build_context(self.public, self.attempt, [], '질문', self.evidence)
        owner = self
        class Provider:
            calls = 0
            def review(self, messages):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError('private error')
                return owner.response()
        provider = Provider()
        run = run_repeated(provider, None, [{'id': 'one', 'operation': 'coaching', 'context': ctx, 'expected_action': 'check'}])
        self.assertEqual(provider.calls, 3)
        self.assertEqual(len(run['samples'][0]['results']), 3)
        self.assertEqual(run['verdict'], 'fail')
        run['samples'][0]['results'].pop(1)
        self.assertEqual(summarize_repeated(run)['verdict'], 'pending')

    def test_fenced_json_preserves_strict_coaching_and_review_validation(self):
        ctx = build_context(self.public, self.attempt, [], '질문', self.evidence)
        fenced = self.response()
        fenced['feedback'] = '```json\n' + fenced['feedback'] + '\n```'
        self.assertEqual(normalize_coaching(fenced, ctx)['status'], 'completed')
        invalid = self.response(extra='forbidden')
        invalid['feedback'] = '```json\n' + invalid['feedback'] + '\n```'
        self.assertEqual(normalize_coaching(invalid, ctx)['status'], 'failed')
        review = self.review()
        review['feedback'] = '```json\n' + review['feedback'] + '\n```'
        self.assertEqual(normalize_evaluation(review, self.report, [self.evidence], self.frozen)['status'], 'completed')
        bad = self.review(total_score=100)
        bad['feedback'] = '```json\n' + bad['feedback'] + '\n```'
        self.assertEqual(normalize_evaluation(bad, self.report, [self.evidence], self.frozen)['status'], 'failed')

    def test_comparison_full_rows_without_sql_string_matching(self):
        expected = {'columns': ['group', 'n'], 'rows': [['a', 20], ['b', 22]]}
        proof = copy.deepcopy(self.evidence)
        proof['result'].update(columns=[{'name': 'n'}, {'name': 'group'}], rows=[[22, 'b'], [20, 'a']])
        self.assertEqual(verify_comparison(proof, expected)['status'], 'verified')
        frozen = freeze_evaluation(self.public, {'weights': {'sql_accuracy': 100}, 'comparison_expected': expected})
        self.assertEqual(verify_for_contract(proof, frozen)['status'], 'verified')
        self.assertEqual(normalize_evaluation(self.review(), self.report, [proof], frozen)['feedback']['total_score'], 100)
        proof['result']['rows'] = [[42, 'a']]
        self.assertEqual(verify_comparison(proof, expected)['status'], 'mismatch')
        self.assertEqual(normalize_evaluation(self.review(), self.report, [proof], frozen)['error']['code'], 'unverified_calculation')
        proof['result']['result_complete'] = False
        self.assertEqual(verify_comparison(proof, expected)['status'], 'unverified')

    def test_criterion_reference_and_weight_cannot_be_overridden(self):
        for change in ({'weight': 100}, {'saved_execution_ids': ['other']}, {'claim_ids': ['unknown']}):
            result = self.review()
            value = json.loads(result['feedback'])
            value['criteria'][0].update(change)
            result['feedback'] = json.dumps(value)
            self.assertEqual(normalize_evaluation(result, self.report, [self.evidence], self.frozen)['status'], 'failed')

if __name__ == '__main__':
    unittest.main()
