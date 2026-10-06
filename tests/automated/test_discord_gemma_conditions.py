import json
from types import SimpleNamespace

from da_agent.discord_education import representative_task
from da_agent.discord_query import DiscordQueryEngine


def test_equivalent_step_filter_is_canonicalized_without_changing_intent():
    conditions = {'metric':'attempts_count','start':'2026-09-01','end':'2026-09-15','timezone':'UTC',
        'period_basis':'explicit_dates','unit':'attempt','group_by':None,'filters':{'step':3}}
    provider=SimpleNamespace(review=lambda messages: {'state':'completed','text':json.dumps({'state':'ready','conditions':conditions})})
    result=DiscordQueryEngine(provider,None,None).resolve('3단계 도전 수',representative_task())
    assert result['state']=='ready' and result['conditions']['step']==3 and result['conditions']['filters']=={}


def test_conflicting_step_is_not_silently_chosen():
    conditions = {'metric':'attempts_count','start':'2026-09-01','end':'2026-09-15','timezone':'UTC',
        'period_basis':'explicit_dates','unit':'attempt','group_by':None,'filters':{'step':3},'step':2}
    provider=SimpleNamespace(review=lambda messages: {'state':'completed','text':json.dumps({'state':'ready','conditions':conditions})})
    result=DiscordQueryEngine(provider,None,None).resolve('3단계 도전 수',representative_task())
    assert result['state']=='clarification'
