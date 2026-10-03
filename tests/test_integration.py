"""Real PostgreSQL integration: run inside Compose with RUN_DB_TESTS=1."""
import os
import time
import uuid
import json

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Requires Compose PostgreSQL")


class OfflineAuth:
    def status(self):
        return {"state": "disconnected", "inference_verified": False}
    def review(self, messages):
        assert [message["role"] for message in messages] == ["developer", "user"]
        return {"state": "error", "reason": "reauthorization_required"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from da_agent.app import create_app
    from da_agent.config import Settings
    settings = Settings()
    app = create_app(settings, OfflineAuth())
    app.state.test_attempts = []
    with TestClient(app) as value:
        yield value
        with app.state.store.connect() as conn:
            for attempt in app.state.test_attempts:
                conn.execute("DELETE FROM report_evidence WHERE report_id IN (SELECT record_id FROM reports WHERE attempt_id=%s)", (attempt,))
                for table in ("reviews", "hint_history", "reports", "saved_executions", "draft_revisions", "attempts"):
                    conn.execute(f"DELETE FROM {table} WHERE attempt_id=%s", (attempt,))


def begin(client, version="v1"):
    response = client.post("/api/attempts", json={"package_id": "training-001", "release_version": version, "problem_id": "problem-001"})
    assert response.status_code == 200, response.text
    value = response.json()
    client.app.state.test_attempts.append(value["attempt_id"])
    return value


def execute(client, attempt, text):
    return client.post(f"/api/attempts/{attempt}/execute", json={"sql": text}).json()


def test_selective_save_resume_reports_and_ai_failure(client):
    attempt = begin(client)["attempt_id"]
    prefix = f"/api/attempts/{attempt}"
    marker = "unsaved_probe_" + uuid.uuid4().hex
    a = execute(client, attempt, "SELECT count(*) AS n FROM users")
    b = execute(client, attempt, f"SELECT '{marker}' AS marker")
    assert a["status"] == b["status"] == "success"
    assert a["rows"] == [[200]]
    request_id = str(uuid.uuid4())
    saved = client.post(prefix + "/executions/save", json={"execution_id": a["execution_id"], "request_id": request_id}).json()
    again = client.post(prefix + "/executions/save", json={"execution_id": a["execution_id"], "request_id": request_id}).json()
    assert again == saved
    draft = client.put(prefix + "/draft", json={"revision": 0, "sections": {"problem_definition": "가입 코호트", "report_text": "공개 유저는 200명"}})
    assert draft.status_code == 200
    assert client.put(prefix + "/draft", json={"revision": 0, "sections": {}}).status_code == 409
    report_data = {"revision": 1, "request_id": str(uuid.uuid4()), "content": {"problem_definition": "가입 코호트"}, "claims": [{"claim_id": "c1", "text": "공개 유저 200명", "evidence_refs": [{"saved_execution_id": saved["saved_execution_id"]}]}]}
    report = client.post(prefix + "/reports", json=report_data).json()
    assert report["report_version"] == 1
    assert client.post(prefix + "/reports", json=report_data).json() == report
    review_path = prefix + f"/reports/{report['report_id']}/review"
    review = client.post(review_path, json={"request_id": str(uuid.uuid4())}).json()
    assert review["status"] == "failed"
    report_data.update(request_id=str(uuid.uuid4()), previous_report_id=report["report_id"])
    second = client.post(prefix + "/reports", json=report_data).json()
    assert second["report_version"] == 2
    resumed = client.get(prefix).json()
    assert len(resumed["saved_executions"]) == 1
    assert len(resumed["reports"]) == 2
    assert len(resumed["reviews"]) == 1
    assert marker not in json.dumps(resumed)
    # Inspect every persistent record table, including failure paths.
    with client.app.state.store.connect() as conn:
        for table in ("attempts", "draft_revisions", "saved_executions", "reports", "reviews", "hint_history"):
            rows = conn.execute(f"SELECT row_to_json(t) AS r FROM {table} t WHERE attempt_id=%s", (attempt,)).fetchall()
            assert marker not in json.dumps(rows)


def test_access_bounds_expiration_and_cross_attempt(client):
    a, b = begin(client), begin(client, "v2")
    assert client.get(f"/api/attempts/{a['attempt_id']}").json()["release_version"] == "v1"
    assert b["release_version"] == "v2"
    for query in ("DELETE FROM users", "SELECT * FROM pg_authid", "SELECT pg_read_file('/etc/passwd')", "SELECT set_config('search_path','public',false)", "SELECT * FROM public.users"):
        assert execute(client, a["attempt_id"], query)["status"] == "blocked"
    zero = execute(client, a["attempt_id"], "SELECT * FROM users WHERE 1=0")
    assert zero["status"] == "success" and zero["rows"] == [] and zero["total_row_count"] == 0
    assert execute(client, a["attempt_id"], "SELECT no_such_column FROM users")["status"] == "error"
    result = execute(client, a["attempt_id"], "SELECT * FROM users")
    saved = client.post(f"/api/attempts/{a['attempt_id']}/executions/save", json={"execution_id": result["execution_id"], "request_id": str(uuid.uuid4())}).json()
    wrong = {"revision": 0, "request_id": str(uuid.uuid4()), "content": {}, "claims": [{"claim_id": "c", "text": "x", "evidence_refs": [{"saved_execution_id": saved["saved_execution_id"]}]}]}
    assert client.post(f"/api/attempts/{b['attempt_id']}/reports", json=wrong).status_code == 400
    client.app.state.runner.settings.execution_ttl = 0
    assert client.post(f"/api/attempts/{a['attempt_id']}/executions/save", json={"execution_id": result["execution_id"], "request_id": str(uuid.uuid4())}).status_code == 410


def test_reference_and_result_caps(client):
    from da_agent.packages import PackageCatalog
    package = PackageCatalog("packages").load("training-001", "v2")
    attempt = begin(client, "v2")["attempt_id"]
    query = package.reference("problem-001")["sql"]
    result = execute(client, attempt, query)
    assert result["status"] == "success", result
    actual = dict(zip([col["name"] for col in result["columns"]], result["rows"][0]))
    assert actual == package.reference("problem-001")["expected"]
    cap = execute(client, attempt, "SELECT a.user_id FROM users a CROSS JOIN users b")
    assert cap["preview_row_count"] == 200 and cap["total_row_count"] is None
    assert not cap["result_complete"] and cap["truncated"]
    saved = client.post(f"/api/attempts/{attempt}/executions/save", json={"execution_id": cap["execution_id"], "request_id": str(uuid.uuid4())}).json()
    assert len(saved["result"]["rows"]) == saved["result"]["preview_row_count"] == 1000


def test_static_errors_and_local_boundary(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/packages/training-001/v1/private/manifest.json").status_code == 404
    assert client.post("/api/attempts", json={"package_id": "training-001", "release_version": "v1", "problem_id": "missing"}).status_code == 404
    assert client.post("/api/attempts", json={}, headers={"Origin": "https://other.test"}).status_code == 403
    assert client.post("/api/attempts", data="sql_secret").status_code == 415


def test_timeout_and_failure_selected_save(client):
    attempt = begin(client)["attempt_id"]
    client.app.state.runner.settings.query_timeout_ms = 1
    result = execute(client, attempt, "SELECT count(*) FROM users a CROSS JOIN users b CROSS JOIN users c CROSS JOIN users d")
    assert result["status"] == "timeout", result
    saved = client.post(f"/api/attempts/{attempt}/executions/save", json={"execution_id": result["execution_id"], "request_id": str(uuid.uuid4())})
    assert saved.status_code == 200 and saved.json()["result"]["status"] == "timeout"
