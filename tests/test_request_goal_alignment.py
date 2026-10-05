"""Prevent topic substitution even when a planner chooses a valid task format."""
import json

import pytest

from da_agent.errors import DomainError
from da_agent.task_contracts import RequestV2
from da_agent.task_planner import assemble_plan, parse_interpretation, unsupported_request
from da_agent.training import interpret
from da_agent.training_contracts import TrainingRequest


def request(**changes):
    return RequestV2.model_validate(dict(contract_version='request-v2', request_id='alignment',
        message='접속 현상 조사', data_mode='existing', **changes))


def selection(**changes):
    return dict(dict(analysis_topic='return_observation', capability_id='access-investigation',
        difficulty='intermediate', task_kind='investigation', goal='접속 관측 조건 비교',
        reason='요청의 분석 목표 확인', unsupported=False), **changes)


def parse(value, data=None):
    return parse_interpretation({'state': 'completed', 'text': json.dumps(value, ensure_ascii=False)}, data or request())


@pytest.mark.parametrize('text', ['게임 비정상 이용자 탐지 및 현상 조사', '접속 로그로 봇 탐지',
    '작업장 이용자 분석', '부정행위 조사', '자동 사냥 패턴 분석', 'detect bots'])
def test_unsupported_topic_cannot_be_disguised_as_access_investigation(text):
    data = request(); data.message = text
    assert unsupported_request(data)
    assert parse(selection(), data).unsupported
    assert interpret(TrainingRequest(request_id='v1', message=text), [])[0] is None


def test_goal_field_and_model_goal_are_checked_independently():
    assert unsupported_request(request(goal='비정상 이용자 탐지'))
    value = selection(goal='비정상 이용자 탐지')
    assert parse(value).unsupported
    with pytest.raises(DomainError) as exc:
        assemble_plan(None, value, 'plan', 0, 'group_difference', False)
    assert exc.value.code == 'unsupported_scope'


def test_semantic_scope_can_reject_topic_without_keyword_match():
    assert parse(selection(analysis_topic='unsupported', goal='이용약관 위반 계정 식별')).unsupported


def test_unclear_topic_requires_clarification_and_missing_scope_fails_closed():
    assert parse(selection(analysis_topic='unclear')).questions
    value = selection(); value.pop('analysis_topic')
    with pytest.raises(DomainError) as exc:
        parse(value)
    assert exc.value.code == 'plan_invalid'


def test_supported_request_still_selects_investigation():
    value = parse(selection())
    assert not value.unsupported and not value.questions
    assert value.task_kind == 'investigation'
