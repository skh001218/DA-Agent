"""Spec012 recommendation policy checks against the integrated implementation.

Run from an isolated checkout with:
python -m pytest -o pythonpath=D:/Codex/DA-Agent/src tests/test_recommendations_v2.py
Policy gaps found during implementation are fixed and asserted as normal regressions.
"""
import copy
import pytest
from da_agent import learning_state, recommendations


def capability(kind, status='approved', scenarios=None):
    return {'capability_id': 'access-' + kind, 'task_kind': kind, 'goal': kind + '-goal',
            'status': status, 'scenarios': scenarios or ['baseline']}


@pytest.fixture
def harness(monkeypatch):
    state = {'state_revision': 4, 'preferences': {'level': 'intermediate', 'goal': ''}, 'observations': [], 'overrides': {}}
    capabilities = [capability(kind) for kind in ('calculation', 'review', 'design', 'investigation')]
    class Store:
        history = []
        def list(self):
            return copy.deepcopy(self.history)
    store = Store()
    monkeypatch.setattr(learning_state, 'get_state', lambda _store: copy.deepcopy(state))
    monkeypatch.setattr(recommendations, 'list_capabilities', lambda _store: copy.deepcopy(capabilities))
    return store, state, capabilities


def observation(identifier='obs', competency='metrics_aggregation', level=1, **extra):
    return dict(observation_id=identifier, competency=competency, criterion_level=level,
                valid=True, confirmed=False, certainty='provisional', **extra)


def test_stored_preference_has_priority_over_observed_weakness(harness):
    store, state, _ = harness
    state['preferences'] = {'level': 'advanced', 'goal': 'design-goal'}
    state['observations'] = [observation()]
    result = recommendations.recommend(store)
    assert result['task_kind'] == 'design' and result['difficulty'] == 'advanced'
    assert result['evidence_ids'] == ['obs'] and result['provisional']


def test_explicit_level_and_goal_override_stored_preferences(harness):
    store, state, _ = harness
    state['preferences'] = {'level': 'beginner', 'goal': 'calculation-goal'}
    result = recommendations.recommend(store, {'difficulty': 'advanced', 'goal': 'review-goal'})
    assert result['difficulty'] == 'advanced' and result['task_kind'] == 'review'


def test_provisional_low_level_does_not_force_difficulty_change(harness):
    store, state, _ = harness
    state['preferences']['level'] = 'advanced'
    state['observations'] = [observation(level=0)]
    result = recommendations.recommend(store)
    assert result['difficulty'] == 'advanced' and result['provisional']
    assert '잠정' in result['reason'] and result['input_state_revision'] == 4


def test_invalid_and_disagreed_evidence_cannot_influence_candidate(harness):
    store, state, _ = harness
    state['observations'] = [dict(observation('invalid', 'analysis_design'), valid=False),
                             observation('disagreed', 'analysis_design')]
    state['overrides'] = {'disagreed': {'disagree': True, 'reason': '이 관측에 동의하지 않음'}}
    result = recommendations.recommend(store)
    assert result['evidence_ids'] == []
    assert not any(candidate['evidence_match'] for candidate in result['candidates'])
    assert '미관측' in result['reason']


def test_semantic_comparison_ignores_seed_title_numbers_and_format():
    signature = {'domain': 'access', 'goal': 'goal', 'required_judgment': 'compare', 'situation': 'composition',
                 'ambiguity': 'advanced', 'evaluation': 'unit-observation', 'format': 'review'}
    other = dict(signature, seed=888, title='다른 제목', eligible_count=999, format='investigation')
    assert recommendations.same_thinking(signature, other)
    assert not recommendations.same_thinking(signature, dict(other, required_judgment='define'))
    assert not recommendations.same_thinking(signature, dict(other, situation='period_change'))


def test_only_recent_five_count_and_duplicate_candidate_is_deprioritized(harness):
    store, _, capabilities = harness
    capabilities[:] = [capability('calculation', scenarios=['baseline', 'composition'])]
    first = recommendations.recommend(store)
    baseline = next(candidate for candidate in first['candidates'] if candidate['candidate_id'].endswith('/baseline'))
    store.history = [{'attempt_id': f'a{i}', 'started_at': f'2026-10-0{6-i}T00:00:00Z',
                      'semantic_signature': dict(baseline['semantic_signature'], seed=i, title='수치만 변경')} for i in range(6)]
    result = recommendations.recommend(store)
    counts = {candidate['candidate_id']: candidate['duplicate_count'] for candidate in result['candidates']}
    assert counts['access-calculation/baseline'] == 5
    assert result['recent_task_ids'] == ['a0', 'a1', 'a2', 'a3', 'a4']
    assert result['candidates'][0]['candidate_id'] == 'access-calculation/composition'
    assert result['repetition']['human_verdict'] is None


def test_stable_choice_and_id_across_capability_order_and_same_state(harness):
    store, _, capabilities = harness
    first = recommendations.recommend(store)
    capabilities.reverse()
    second = recommendations.recommend(store)
    assert first['recommendation_id'] == second['recommendation_id']
    assert first['candidates'] == second['candidates']
    assert first['task_kind'] == second['task_kind'] == 'calculation'


def test_equal_duplicate_counts_prefer_older_last_use(harness):
    store, _, capabilities = harness
    capabilities[:] = [capability('calculation', scenarios=['baseline', 'composition'])]
    candidates = {candidate['candidate_id']: candidate for candidate in recommendations.recommend(store)['candidates']}
    store.history = [
        {'attempt_id': 'recent', 'started_at': '2026-10-04T00:00:00Z', 'semantic_signature': candidates['access-calculation/baseline']['semantic_signature']},
        {'attempt_id': 'older', 'started_at': '2026-10-03T00:00:00Z', 'semantic_signature': candidates['access-calculation/composition']['semantic_signature']}]
    result = recommendations.recommend(store)
    assert all(candidate['duplicate_count'] == 1 for candidate in result['candidates'])
    assert result['candidates'][0]['candidate_id'] == 'access-calculation/composition'


def test_revision_and_removed_evidence_recompute_recommendation(harness):
    store, state, _ = harness
    state['observations'] = [observation('old', 'analysis_design')]
    first = recommendations.recommend(store)
    state.update(state_revision=5, observations=[])
    second = recommendations.recommend(store)
    assert first['recommendation_id'] != second['recommendation_id']
    assert second['evidence_ids'] == [] and second['input_state_revision'] == 5


def test_unapproved_generation_capability_is_never_recommended(harness):
    store, _, capabilities = harness
    capabilities[:] = [capability('calculation', 'draft'), capability('review', 'approved'), capability('design', 'rejected')]
    result = recommendations.recommend(store)
    assert result['task_kind'] == 'review'
    assert all(candidate['available_for_generation'] for candidate in result['candidates'])


def test_explicit_format_wins_over_conflicting_stored_goal(harness):
    store, state, _ = harness
    state['preferences']['goal'] = 'calculation-goal'
    result = recommendations.recommend(store, {'task_kind': 'review'})
    assert result['task_kind'] == 'review'


def test_explicit_repeat_intent_is_preserved_separately(harness):
    store, _, capabilities = harness
    capabilities[:] = [capability('calculation')]
    candidate = recommendations.recommend(store)['candidates'][0]
    store.history = [{'attempt_id': 'previous', 'started_at': '2026-10-04T00:00:00Z', 'semantic_signature': candidate['semantic_signature']}]
    result = recommendations.recommend(store, {'task_kind': 'calculation', 'intentional_repeat': True})
    assert result['task_kind'] == 'calculation'
    assert result['repetition']['duplicate_count'] == 1
    assert result['repetition'].get('intentional_repeat') is True


def test_unknown_criterion_level_does_not_crash_or_claim_weakness(harness):
    store, state, _ = harness
    state['observations'] = [observation(level=None)]
    result = recommendations.recommend(store)
    assert result['difficulty'] == 'intermediate'
    assert not any(candidate['evidence_match'] for candidate in result['candidates'])


def test_missing_semantic_fields_are_unobserved_not_duplicates():
    assert not recommendations.same_thinking({'seed': 1}, {'title': '다른 제목'})


def test_draft_rules_fall_back_to_existing_data_without_claiming_generation(harness):
    store, _, capabilities = harness
    capabilities[:] = [capability(kind, "draft") for kind in ("calculation", "review", "design", "investigation")]
    result = recommendations.recommend(store)
    assert len(result["candidates"]) == 3
    assert all(not c["available_for_generation"] for c in result["candidates"])
    assert all(c["candidate_id"].endswith("/existing-access") for c in result["candidates"])


def test_all_disabled_rules_return_actionable_unavailable(harness):
    from da_agent.errors import DomainError
    store, _, capabilities = harness
    capabilities[:] = [capability("calculation", "disabled")]
    with pytest.raises(DomainError) as exc:
        recommendations.recommend(store)
    assert exc.value.code == "capability_unavailable"
