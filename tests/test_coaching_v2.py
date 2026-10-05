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
        value = dict(action_type='check', reason='집계를 확인', next_action='조건을 확인하세요', evidence_ids=['e'], uncertainty='')
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
    def test_explicit_conflict_blocks_submission_but_resolution_does_not(self):
        for message, expected in [('가설과 관측이 충돌하면 어떤 비교를 해야 하나요?', 'suggest_analysis'),
                                  ('가설과 관측이 충돌합니다. 가설을 철회해야 하나요?', 'suggest_analysis'),
                                  ('가설과 관측의 충돌을 해결했습니다.', 'submit'),
                                  ('가설을 철회했고 관측 차이만 보고합니다.', 'submit'),
                                  ('비교 결과를 정리했습니다.', 'submit')]:
            ctx=build_context(self.public,self.attempt,[],message,self.evidence)
            result=normalize_coaching(self.response(action_type='submit',next_action='보고서를 제출하세요.'),ctx)
            self.assertEqual(result['feedback']['action_type'],expected)
            self.assertEqual(result['status'],'completed')
    def test_temporary_state_and_no_intervention(self):
        evidence = {'execution_id': 'temp', 'rows': [[1]]}
        ctx = build_context(self.public, self.attempt, [], '질문', evidence)
        self.assertTrue(ctx['transient'])
        result = normalize_coaching(self.response(action_type='none', next_action='', evidence_ids=[]), ctx)
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

    def test_server_derives_states_and_classifies_failures(self):
        ctx = build_context(self.public, self.attempt, [], '질문', self.evidence)
        for ids, state in [(['e'], 'saved'), (['public-task'], 'public'), (['e', 'public-task'], 'mixed'), ([], 'unverified')]:
            self.assertEqual(normalize_coaching(self.response(evidence_ids=ids), ctx)['feedback']['evidence_state'], state)
        temporary = build_context(self.public, self.attempt, [], '질문', {'execution_id': 'temp'})
        self.assertEqual(normalize_coaching(self.response(evidence_ids=['temp']), temporary)['feedback']['evidence_state'], 'temporary')
        for response, code in [(self.response(evidence_ids=['fake']), 'coaching_source'), (self.response(evidence_ids=['e', 'e']), 'coaching_source'), (self.response(action_type='none'), 'coaching_action'), (self.response(next_action=' '), 'coaching_action'), (self.response(reason=None), 'coaching_schema'), (self.response(evidence_state='saved'), 'coaching_schema'), ({'status': 'completed', 'feedback': 'private raw text'}, 'coaching_json')]:
            result = normalize_coaching(response, ctx)
            self.assertEqual(result['error']['code'], code)
            self.assertIsNone(result['feedback'])
            self.assertNotIn('private raw text', json.dumps(result))
        missing = self.response()
        value = json.loads(missing['feedback'])
        del value['uncertainty']
        missing['feedback'] = json.dumps(value)
        self.assertEqual(normalize_coaching(missing, ctx)['error']['fields'], ['uncertainty'])
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
