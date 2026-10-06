"""Inspect current prompt/history boundaries without invoking a model."""
import copy
import json
from da_agent.adaptive_tasks import Recipe, planning_messages
from da_agent.config import Settings
from da_agent.store import Store
from da_agent.task_contracts import RequestV2
from da_agent.task_quality import check_quality

request = RequestV2(contract_version='request-v2', request_id='prompt-audit',
                    message='실무에서 일어날만한 문제를 분석하고싶어', difficulty='advanced')
with Store(Settings().records_dsn).connect() as conn:
    rows = conn.execute("SELECT p.public FROM training_plans p JOIN attempts a USING(attempt_id) ORDER BY a.payload->>'started_at' DESC LIMIT 5").fetchall()
    recent = [r['public'].get('semantic_signature', {}) for r in rows]
    stored = conn.execute('SELECT private FROM generation_jobs WHERE request_id=%s',
                          ('cf5ef4ca-2e14-4c6b-8d25-afa59caa4fba',)).fetchone()['private']
recipe = Recipe.model_validate(stored['adaptive_recipe'])
empty_messages = planning_messages(request, [])
history_messages = planning_messages(request, recent)
empty_envelope = json.loads(empty_messages[1]['content'])
history_envelope = json.loads(history_messages[1]['content'])
checks = {
    'developer_prompt_unchanged_by_history': empty_messages[0] == history_messages[0],
    'only_recent_envelope_changes': {k:v for k,v in empty_envelope.items() if k!='recent'} == {k:v for k,v in history_envelope.items() if k!='recent'},
    'recent_count': len(recent),
    'history_contains_only_signatures': all(set(p).issubset({'domain','goal','required_judgment','format','ambiguity','situation','structure','difficulty','evaluation','quality_version'}) for p in recent),
    'broad_request_requires_agent_expansion': '상세 상황 미입력만으로 clarify하지 마세요' in history_messages[0]['content'],
    'boss_content_bias_instruction_present': '광범위한 실무 요청에는 현재 지원하는 보스/스테이지' in history_messages[0]['content'],
}
def verdict(previous, repeat=False):
    try:
        check_quality(recipe, [previous], repeat)
        return 'pass'
    except ValueError as exc:
        return str(exc)
previous = recent[0]
checks['same_signature'] = verdict(previous)
checks['intentional_repeat'] = verdict(previous, True)
changed_level = copy.deepcopy(previous)
changed_level['difficulty'] = 'beginner'
checks['previous_other_difficulty'] = verdict(changed_level)
changed_goal = copy.deepcopy(previous)
changed_goal['goal'] += ' (표현 변경)'
checks['same_structure_goal_wording_changed'] = verdict(changed_goal)
assert checks['developer_prompt_unchanged_by_history'] and checks['only_recent_envelope_changes']
assert checks['broad_request_requires_agent_expansion']
assert 'repeated analysis' in checks['same_signature']
assert checks['intentional_repeat'] == checks['previous_other_difficulty'] == checks['same_structure_goal_wording_changed'] == 'pass'
print(json.dumps({'checks':checks, 'current_prompt':history_messages, 'prompt_without_history':empty_messages,
                  'note':'Current payload reconstruction; historical calls did not retain their exact recent snapshot. No live model request.'}, ensure_ascii=False))
