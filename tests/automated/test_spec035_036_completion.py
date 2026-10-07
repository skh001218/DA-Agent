from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from da_agent.discord_api_budget import ApiBudget
from da_agent.discord_service import DiscordTrainingService
from da_agent.discord_sql_practice import make_task, verification_review
from da_agent.discord_education import representative_task
from da_agent.adaptive_tasks import Recipe
from da_agent.errors import DomainError
from da_agent.task_quality import check_quality
from test_adaptive_tasks import bot_recipe
from test_discord_generation import MemoryStore


@pytest.mark.parametrize('practice', [None,'','other'])
def test_missing_practice_cannot_create_records_or_call_model(practice):
    store, provider, factory = MemoryStore(), Mock(), Mock()
    service = DiscordTrainingService(store, NS(), provider, NS(), dataset_factory=factory)
    with pytest.raises(DomainError, match='SQL 연습'):
        service.start('u','g','c','e',text='계정 행동 비교',practice=practice)
    assert not store.docs and not store.events and not store.jobs
    provider.review.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize('text', [
    '튜토리얼 완료율 대신 D7 리텐션을 계산해줘',
    '튜토리얼 3단계 완료율과 주간 재방문을 계산해줘',
    '튜토리얼 완료율 말고 매출을 계산해줘',
    '튜토리얼 3단계와 D30 잔류율',
    '주간 재방문율',
])
@pytest.mark.parametrize('source', [None,'source-id'])
def test_unsupported_sql_never_creates_data_even_with_source(text, source):
    store, provider, factory = MemoryStore(), Mock(), Mock()
    service = DiscordTrainingService(store, NS(), provider, NS(), dataset_factory=factory)
    with pytest.raises(DomainError, match='지원'):
        service.start('u','g','c','e',text=text,practice='sql',source_session_id=source)
    assert not store.docs and not store.events
    factory.assert_not_called()


def advanced_recipe():
    value=bot_recipe(); value['difficulty']='advanced'
    for competency in ('alternatives','confounding'):
        req=deepcopy(value['business_case']['requirements'][0])
        req['competency']=competency
        value['business_case']['requirements'].append(req)
    return value


def test_cosmetic_advanced_cannot_pass():
    with pytest.raises(ValueError, match='judgment contract'):
        check_quality(Recipe.model_validate(advanced_recipe()))


def test_tutorial_funnel_dropout_is_not_automatically_retention():
    value=bot_recipe()
    value['goal']='튜토리얼 단계별 중도 이탈과 완료율의 차이를 비교합니다.'
    value['metrics'].append({'name':'tutorial_completion','table':'activity_daily','operation':'ratio',
        'purpose':'analysis','conditions':[{'column':'actions','operator':'gte','value':1000}]})
    assert check_quality(Recipe.model_validate(value))['structural_checks']=='pass'
    value['goal'] += ' D7 리텐션도 판정합니다.'
    with pytest.raises(ValueError,match='retention/churn'):
        check_quality(Recipe.model_validate(value))


def test_explicit_non_identifiable_path_is_allowed_but_missing_controls_are_not():
    value=advanced_recipe()
    req=value['business_case']['requirements'][-1]
    req['judgment']={'method':'non_identifiable','control_columns':['activity_daily.actions'],
        'decision_rule':'관측 행동량과 간격만으로는 이용 목적의 차이를 식별할 수 없어 판단을 유보한다.',
        'accepted_limit':'통제에 필요한 이용 목적과 접속 시간이 없으므로 추가 관측을 요구하는 답도 인정한다.'}
    assert check_quality(Recipe.model_validate(value))['structural_checks']=='pass'
    req['judgment']['control_columns']=['activity_daily.nonexistent']
    with pytest.raises(ValueError, match='public evidence'):
        check_quality(Recipe.model_validate(value))


def test_conditional_claim_requires_real_stratified_metric():
    value=advanced_recipe(); req=value['business_case']['requirements'][-1]
    req['evidence'].append({'table':'accounts','columns':['platform','account_id']})
    req['judgment']={'method':'conditional_comparison','control_columns':['accounts.platform'],
        'decision_rule':'동일 플랫폼 안에서 다른 관측 그룹을 비교하여 구성 차이에 따른 가능성을 검토한다.',
        'accepted_limit':'관측 비교로는 부정행위나 인과관계를 단정할 수 없으므로 추가 자료를 요청한다.'}
    with pytest.raises(ValueError, match='control strata'):
        check_quality(Recipe.model_validate(value))
    next(m for m in value['metrics'] if m['name']=='platform_actions')['group_by'].append('interval_cv')
    assert check_quality(Recipe.model_validate(value))['structural_checks']=='pass'


@pytest.mark.parametrize('statuses,expected', [
    (['met']*3,'충족'),(['met','missing','met'],'보완 필요'),
    (['incorrect']*3,'보완 필요'),(['unverifiable']*3,'판정 보류')])
def test_sql_method_review_keeps_correctness_separate(statuses,expected):
    task=make_task(representative_task(difficulty='advanced'))
    attempt={'execution_id':'e','sql':'SELECT 1','full_result':{'status':'success'},
             'verification_notes':'중복·기간·분모에 대한 구체적인 검산 방법 설명'}
    checks=[{'id':key,'status':status,'reason':'공개 계약과 비교한 방법 검토'}
            for key,status in zip(('duplicates','period','denominator'),statuses)]
    provider=Mock(); provider.review.return_value={'state':'completed','text':json.dumps({'checks':checks}),'model':'gemma-test'}
    assert verification_review(task,attempt,provider)['status']==expected
    attempt['verification_notes']=''
    assert verification_review(task,attempt,provider)['status']=='보완 필요'
    assert provider.review.call_count==1


def test_sql_method_outage_and_malformed_review_are_held():
    task=make_task(representative_task(difficulty='advanced'))
    attempt={'execution_id':'e','sql':'SELECT 1','full_result':{},'verification_notes':'구체적인 설명'}
    for result in ({'state':'error'}, {'state':'completed','text':'{"checks":[]}'},
                   {'state':'completed','text':'not JSON'}):
        provider=Mock(); provider.review.return_value=result
        assert verification_review(task,attempt,provider)['status']=='판정 보류'


def test_persistent_budget_serializes_providers_and_reconciles_usage(tmp_path):
    path=tmp_path/'budget.sqlite'
    first, second=ApiBudget(path,limit=100,window=65),ApiBudget(path,limit=100,window=65)
    ident,delay=first.reserve('key-model',70,now=100)
    assert ident and delay==0
    assert second.reserve('key-model',40,now=100)==(None,65)
    first.actual(ident,50,now=100)
    assert second.reserve('key-model',40,now=100)[0]
    assert first.reserve('key-model',101,now=100)==(None,None)
    assert first.reserve('key-model',100,now=300)[0]


def test_budget_isolates_models_and_keys(tmp_path):
    budget=ApiBudget(tmp_path/'budget.sqlite',limit=100)
    assert budget.reserve('a',100,now=0)[0]
    assert budget.reserve('b',100,now=0)[0]


def test_retention_business_context_does_not_require_unasked_retention_metric():
    value=bot_recipe()
    value["business_case"]["background"] += " 튜토리얼 완료는 서비스 리텐션에도 영향을 줄 수 있습니다."
    check_quality(Recipe.model_validate(value))
    value["business_case"]["requirements"][0]["question"] += " D7 리텐션을 계산하세요."
    with pytest.raises(ValueError,match="retention/churn"):
        check_quality(Recipe.model_validate(value))


def test_completed_call_keeps_budget_for_window_after_slow_response(tmp_path):
    budget=ApiBudget(tmp_path/'budget.sqlite',limit=100,window=65)
    ident,_=budget.reserve('scope',70,now=0)
    budget.actual(ident,70,now=60)
    assert budget.reserve('scope',40,now=65)==(None,60)
    assert budget.reserve('scope',40,now=125)[0]


def test_pending_request_is_not_pruned_while_model_is_still_responding(tmp_path):
    budget=ApiBudget(tmp_path/'budget.sqlite',limit=100,window=65)
    ident,_=budget.reserve('scope',70,now=0)
    assert budget.reserve('scope',40,now=90)==(None,65)
    budget.actual(ident,70,now=100)
    assert budget.reserve('scope',40,now=165)[0]


def test_budget_migrates_existing_volume_without_losing_usage(tmp_path):
    import sqlite3
    path=tmp_path/'existing.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE calls (id TEXT PRIMARY KEY,scope TEXT,at REAL,tokens INTEGER)')
        conn.execute('INSERT INTO calls VALUES (?,?,?,?)',('old','scope',100,70))
    budget=ApiBudget(path,limit=100,window=65)
    assert budget.reserve('scope',40,now=100)==(None,65)
