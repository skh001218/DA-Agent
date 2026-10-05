"""Quality tools exercised against real PostgreSQL with controlled AI responses."""
import json
import os
import uuid

import pytest

from da_agent.quality import summarize
from test_integration import client, begin

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires Compose PostgreSQL")


@pytest.fixture(autouse=True)
def clean_runs(client):
    yield
    with client.app.state.store.connect() as conn:
        conn.execute("DELETE FROM quality_runs WHERE payload->>'request_id' LIKE 'quality-test-%'")


def mock_review(messages):
    payload = json.loads(messages[1]["content"])
    evidence = payload["evidence"][0]
    assert evidence["result"]["status"] == "success"
    assert "seed" not in payload and "reference_sql" not in payload
    return {"state": "completed", "model": "controlled-test-model", "text": json.dumps({
        "criteria": [{"key": key, "level": 3, "reason": "동작 검증용 응답입니다. 실제 평가 품질을 입증하지 않습니다.",
                      "claim_ids": ["quality-claim"], "saved_execution_ids": [evidence["saved_execution_id"]]}
                     for key in ("problem_definition", "analysis_approach", "sql_accuracy", "interpretation", "next_actions")],
        "strengths": [], "improvements": [], "next_steps": ["사람이 판정 기준을 확인하세요."]})}


def start_run(client):
    payload = {"package_id": "training-001", "release_version": "v1", "problem_id": "problem-001", "request_id": "quality-test-" + uuid.uuid4().hex}
    response = client.post("/api/quality/runs", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["run_id"], payload


def test_real_sql_fixtures_repetitions_human_judgments_and_replay(client):
    client.app.state.auth.review = mock_review
    existing = client.get("/api/attempts").json()
    run_id, payload = start_run(client)
    run = client.get(f"/api/quality/runs/{run_id}").json()
    assert run["status"] == "completed" and run["completed_calls"] == 15
    assert run["verdict"] == "pending"
    assert client.get("/api/attempts").json() == existing
    assert len(run["samples"]) == 5
    baseline = run["samples"][0]["evidence"][0]["result"]["rows"][0]
    for sample in run["samples"]:
        assert len(sample["results"]) == 3 and sample["score_range"] == 0
        assert sample["evidence"][0]["result"]["result_complete"]
        if sample["id"] == "alternative":
            assert sample["evidence"][0]["result"]["rows"][0][0:2] == baseline[0:2]
        if sample["id"] in {"duplicate", "incomplete"}:
            assert sample["evidence"][0]["result"]["rows"][0][0] > baseline[0]
        for repetition in range(1, 4):
            response = client.put(f"/api/quality/runs/{run_id}/human", json={"sample_id": sample["id"], "repetition": repetition,
                "checks": ["pass", "pass"], "critical_error": "no", "reviewer": "test-reviewer", "note": "모의 응답 저장 동작만 검증"})
            assert response.status_code == 200
    assert response.json()["verdict"] == "pass"
    exported = client.get(f"/api/quality/runs/{run_id}/export")
    assert 'attachment; filename="quality-' in exported.headers["content-disposition"]
    assert exported.json()["verdict"] == "pass"
    assert exported.json()["samples"][0]["results"][0]["human"]["reviewer"] == "test-reviewer"
    replay = client.post("/api/quality/runs", json=payload).json()
    assert replay["run_id"] == run_id and replay["completed_calls"] == 15
    payload["release_version"] = "v2"
    assert client.post("/api/quality/runs", json=payload).status_code == 409
    assert client.put(f"/api/quality/runs/{run_id}/human", json={"sample_id": "missing", "repetition": 1, "checks": ["pass", "pass"], "critical_error": "no", "reviewer": "x", "note": ""}).status_code == 400


def test_failed_ai_never_passes_and_cannot_be_human_approved(client):
    run_id, _ = start_run(client)
    run = client.get(f"/api/quality/runs/{run_id}").json()
    assert run["status"] == "completed" and run["verdict"] == "fail"
    assert all(r["status"] == "failed" for s in run["samples"] for r in s["results"])
    response = client.put(f"/api/quality/runs/{run_id}/human", json={"sample_id": "correct", "repetition": 1, "checks": ["pass", "pass"], "critical_error": "no", "reviewer": "x", "note": ""})
    assert response.status_code == 400


def test_score_variation_and_critical_error_gate(client):
    run = {"status": "completed", "samples": [{"results": [{"status": "completed", "feedback": {"total_score": score}, "human": {"checks": ["pass", "pass"], "critical_error": "no"}} for score in (70, 75, 85)]}]}
    assert summarize(run)["samples"][0]["score_range"] == 15
    assert summarize(run)["verdict"] == "pending"
    run["samples"][0]["results"][0]["human"]["critical_error"] = "yes"
    assert summarize(run)["verdict"] == "fail"


def test_pilot_persistence_duplicate_code_and_unsaved_sql_boundary(client):
    attempt = begin(client)["attempt_id"]
    other = begin(client)["attempt_id"]
    code = "TEST_" + uuid.uuid4().hex[:12]
    saved = client.put(f"/api/quality/pilots/{attempt}", json={"participant_code": code}).json()
    assert saved["events"] == []
    assert client.get(f"/api/attempts/{attempt}").json()["pilot_linked"]
    assert client.put(f"/api/quality/pilots/{other}", json={"participant_code": code}).status_code == 409
    marker = "UNSAVED_" + uuid.uuid4().hex
    client.post(f"/api/attempts/{attempt}/execute", json={"sql": f"SELECT '{marker}' AS marker"})
    client.post(f"/api/attempts/{attempt}/execute", json={"sql": "DELETE FROM users"})
    client.post(f"/api/quality/pilots/{attempt}/stage", json={"stage": "report"})
    data = client.get("/api/quality/pilots").json()
    assert marker not in json.dumps(data)
    pilot = next(p for p in data["pilots"] if p["attempt_id"] == attempt)
    assert [event["status"] for event in pilot["events"]] == ["success", "blocked", "visited"]
    assert pilot["attempt"]["saved_executions"] == [] and not pilot["independent"]
    response = client.put(f"/api/quality/pilots/{attempt}", json={"participant_code": code, "assistance": "no", "next_action": "관측 조건을 수정한다", "actionable": "yes"})
    assert response.status_code == 200 and len(response.json()["events"]) == 3
    assert client.put(f"/api/quality/pilots/{attempt}", json={"participant_code": code, "actionable": "yes"}).status_code == 400
    assert client.put(f"/api/quality/pilots/{attempt}", json={"participant_code": code, "improved": "yes"}).status_code == 400
    exported = client.get(f"/api/quality/pilots/{attempt}/export")
    assert exported.status_code == 200 and 'attachment; filename="pilot-' in exported.headers["content-disposition"]
    assert marker not in exported.text and len(exported.json()["events"]) == 3
    assert client.post(f"/api/quality/pilots/{attempt}/stage", json={"stage": "sql", "sql": marker}).status_code == 422


def test_busy_interrupted_and_missing_runs(client):
    with client.app.state.store.connect() as conn:
        from psycopg.types.json import Jsonb
        conn.execute("INSERT INTO quality_runs VALUES(%s,%s)", ("quality-test-running", Jsonb({"status": "running", "request_id": "quality-test-running"})))
    response = client.post("/api/quality/runs", json={"package_id": "training-001", "release_version": "v1", "problem_id": "problem-001", "request_id": "quality-test-other"})
    assert response.status_code == 409
    from da_agent.quality import initialize
    initialize(client.app.state.store)
    with client.app.state.store.connect() as conn:
        assert conn.execute("SELECT payload->>'status' AS status FROM quality_runs WHERE run_id='quality-test-running'").fetchone()["status"] == "interrupted"
    assert client.get("/api/quality/runs/missing").status_code == 404


def test_interrupted_run_preserves_completed_calls_without_exception_leak(client):
    calls = []
    def interrupted(messages):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("private-provider-diagnostic")
        return mock_review(messages)
    client.app.state.auth.review = interrupted
    run_id, _ = start_run(client)
    run = client.get(f"/api/quality/runs/{run_id}").json()
    assert run["status"] == "interrupted" and run["completed_calls"] == 1
    assert run["verdict"] == "pending" and "private-provider-diagnostic" not in json.dumps(run)


def test_second_package_fixture_and_invalid_judgment(client):
    client.app.state.auth.review = mock_review
    response = client.post("/api/quality/runs", json={"package_id": "training-001", "release_version": "v2", "problem_id": "problem-001", "request_id": "quality-test-" + uuid.uuid4().hex})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    run = client.get(f"/api/quality/runs/{run_id}").json()
    assert run["completed_calls"] == 15 and run["fixture_version"] == "churn-quality-v2"
    assert all(sample["expected_levels"]["next_actions"] == [3, 4] for sample in run["samples"])
    assert client.put(f"/api/quality/runs/{run_id}/human", json={"sample_id": "correct", "repetition": 1, "checks": ["fail", "pass"], "critical_error": "no", "reviewer": "x", "note": ""}).status_code == 400
