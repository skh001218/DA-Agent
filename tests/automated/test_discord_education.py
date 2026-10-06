import json
from types import SimpleNamespace

import pytest

from da_agent.discord_education import (representative_task, fixture_dataset, prepare_dataset,
    help_response, evaluate_report, growth_observation, stakeholder_followup)


def verdict(grade=3):
    return {"criteria": [{"id": c["id"], "grade": grade, "evidence_refs": ["report:2", "execution:q1"],
                         "reason": "제출된 정의·관측 근거가 공개 조건을 충족", "improvement": "수집 상태 확인을 추가",
                         'deductions': [] if grade>=3 else [dict(issue_id=c['id'],kind='missing_required',
                             check_id=c['id']+':required',claim='',evidence_refs=['report:2'],reason='독립된 필수 조건 누락')]}
                         for c in representative_task()["rubric"]["criteria"]]}


def evaluate(provider, text="채널 차이는 관측 연관성. 재도전 중복을 제외하고 추가 로그 확인 필요."):
    return evaluate_report(provider, representative_task(), {"version": 2, "text": text},
                           [{"id": "m1", "text": "초기 가설을 기각"}], [{"id": "q1", "status": "success", "rows": [[1, 2]]}], [])


def test_public_contract_and_deterministic_fixture():
    task = representative_task()
    assert sum(c["weight"] for c in task["rubric"]["criteria"]) == 100
    assert "SQL 작성 역량" in task["rubric"]["non_scoring"]
    assert len(task["valid_paths"]) >= 2
    assert len(task["schema"]) == 3
    rows = fixture_dataset(task)
    assert rows == fixture_dataset(task)
    assert len(rows["users"]) == 40
    assert len(rows["tutorial_attempts"]) == 130
    assert len({r[0] for r in rows["users"]}) == 40
    assert fixture_dataset(representative_task(variant="followup")) != rows
    assert task["human_review"]["status"] == "pending"


def test_top_grade_without_defect_gets_attributed_next_practice():
    output=verdict(4)
    for row in output['criteria']: row['improvement']=''
    result=evaluate(lambda _:output)
    assert not result['held'] and result['total']==100
    assert all(row['improvement_source']=='public_rubric_next_practice' for row in result['criteria'])


def test_lower_grade_still_requires_specific_improvement():
    output=verdict(2)
    output['criteria'][0]['improvement']=''
    result=evaluate(lambda _:output)
    assert result['held'] and result['validation_reason']=='criterion_detail_missing'


def test_response_detail_repair_preserves_grades_once():
    bad=verdict(2)
    bad['criteria'][0]['improvement']=''
    outputs=iter([bad,verdict(2)])
    provider=SimpleNamespace(review=lambda messages:{'state':'completed','text':json.dumps(next(outputs))})
    result=evaluate(provider)
    assert not result['held'] and result['total']==50 and result['response_repair']['grades_preserved']


def test_response_repair_cannot_change_grades():
    bad=verdict(2)
    bad['criteria'][0]['improvement']=''
    outputs=iter([bad,verdict(4)])
    provider=SimpleNamespace(review=lambda messages:{'state':'completed','text':json.dumps(next(outputs))})
    result=evaluate(provider)
    assert result['held'] and result['response_repair']['status']=='failed'


def test_unsupported_task_does_not_fabricate_data():
    with pytest.raises(ValueError):
        representative_task(topic="economy")
    with pytest.raises(ValueError):
        representative_task(difficulty="unknown")


def test_help_is_recorded_separately_and_no_hidden_answers():
    task = representative_task(difficulty="beginner")
    neutral = help_response(task, "완료 비율", "clarification")
    hint = help_response(task, "완료 비율", "direction")
    assert not neutral["is_hint"] and not neutral["criteria"]
    assert hint["is_hint"] and hint["help_before"] == "완료 비율"
    assert hint["level"] == "guided"
    assert "SELECT" not in hint["text"]
    assert help_response(representative_task(), "설명", "concept")["level"] == "independent"
    assert stakeholder_followup(task, {"version": 2})["report_version"] == 2


def test_semantic_provider_context_public_only_and_weighted_score():
    task = representative_task()
    task["private_generation_recipe"] = "SECRET"
    task["answer_sql"] = "SECRET"
    captured = []
    def provider(context):
        captured.append(context)
        return verdict()
    result = evaluate_report(provider, task, {"version": 2, "text": "근거"}, [], [{"id": "q1", "status": "success"}], [])
    assert result["total"] == 75 and not result["held"]
    assert "SECRET" not in json.dumps(captured)
    assert result["human_review"] == "pending"


@pytest.mark.parametrize("text", ["판단 보류. 추가 로그 확인.", "가설 기각 후 채널 구성 대안 검토.", "같은 사실과 근거를 길게 서술. " * 100])
def test_no_length_or_query_count_score(text):
    assert evaluate(lambda _: verdict(), text)["total"] == 75


@pytest.mark.parametrize("mutation", ["unknown_ref", "invalid_grade", "missing_item", "missing_reason", "boolean_grade"])
def test_provider_contract_failure_is_held(mutation):
    data = verdict()
    if mutation == "unknown_ref": data["criteria"][0]["evidence_refs"] = ["execution:invented"]
    if mutation == "invalid_grade": data["criteria"][0]["grade"] = 5
    if mutation == "missing_item": data["criteria"].pop()
    if mutation == "missing_reason": data["criteria"][0]["reason"] = ""
    if mutation == "boolean_grade": data["criteria"][0]["grade"] = True
    result = evaluate(lambda _: data)
    assert result["held"] and result["total"] is None


def test_partial_system_failure_holds_total_not_zero():
    data = verdict()
    data["criteria"][0].update(grade=None, held_reason="실행 근거 유실", evidence_refs=[])
    result = evaluate(lambda _: data)
    assert result["held"] and result["total"] is None
    assert result["criteria"][1]["grade"] == 3


def test_failed_query_not_admitted_as_success_evidence():
    result = evaluate_report(lambda _: verdict(), representative_task(), {"version": 2}, [], [{"id": "q1", "status": "failed"}], [])
    assert result["held"]


def test_gemini_review_interface_and_failure_without_api_call():
    class Provider:
        def review(self, messages):
            assert messages[0]["role"] == "developer"
            assert json.loads(messages[1]["content"])["public_task"]["rubric"]["version"] == "discord-analysis-v1"
            return {"state": "completed", "text": json.dumps(verdict())}
    assert evaluate(Provider())["total"] == 75
    failed=evaluate(SimpleNamespace(review=lambda messages: {"state": "error", "reason": "api_rate_limited","quota_diagnostic":{"status":429}}))
    assert failed['total'] is None and failed['provider_failure']['quota_diagnostic']=={'status':429}


def test_growth_requires_reviewed_new_comparable_pre_help_evidence():
    base = {"task_id": "baseline", "difficulty": "intermediate", "rubric_version": "v1", "comparability_review": "approved", "before_help": {"metric_design": 2}, "after_help": {"metric_design": 4}}
    other = dict(base, task_id="followup", before_help={"metric_design": 3})
    assert growth_observation([base])["status"] == "성장 판단 자료 부족"
    result = growth_observation([base, other])
    assert result["observed_count"] == 1
    assert result["observations"]["metric_design"]["delta"] == 1
    assert len(result["assisted"]) == 2
    assert growth_observation([base, dict(other, difficulty="advanced")])["status"] == "성장 판단 자료 부족"
    assert growth_observation([base, dict(other, comparability_review="pending")])["status"] == "성장 판단 자료 부족"


def test_dataset_does_not_use_default_web_database(monkeypatch):
    monkeypatch.delenv("DISCORD_ADMIN_DSN", raising=False)
    monkeypatch.delenv("DISCORD_LEARNER_DSN", raising=False)
    with pytest.raises(ValueError):
        prepare_dataset(SimpleNamespace(), representative_task())
    with pytest.raises(ValueError, match="기존 웹 DB"):
        prepare_dataset(SimpleNamespace(admin_dsn="postgresql://admin@localhost/training", learner_dsn="postgresql://learner@localhost/training"), representative_task())


@pytest.mark.skipif(not __import__("os").getenv("DISCORD_TEST_ADMIN_DSN"), reason="격리 Discord DB 미설정")
def test_isolated_postgres_fixture_readonly():
    import os
    import psycopg
    settings = SimpleNamespace(admin_dsn=os.environ["DISCORD_TEST_ADMIN_DSN"], learner_dsn=os.environ["DISCORD_TEST_LEARNER_DSN"])
    info = prepare_dataset(settings, representative_task())
    assert info["counts"]["users"] == 40
    try:
        with psycopg.connect(settings.learner_dsn) as conn:
            count = conn.execute(f'SELECT count(*) FROM "{info["schema_name"]}".users').fetchone()[0]
            assert count == 40
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f'DELETE FROM "{info["schema_name"]}".users')
    finally:
        with psycopg.connect(settings.admin_dsn) as conn:
            conn.execute(f'DROP SCHEMA "{info["schema_name"]}" CASCADE')
