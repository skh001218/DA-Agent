"""Operator-only quality tools. Never collect unsaved learner SQL."""
import copy
import json
import uuid

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import Field
from typing import Literal

from .contracts import Contract, Start
from .errors import DomainError
from .reviews import RULES_VERSION, review_report
from .store import now

FIXTURE_VERSION = "churn-quality-v2"
CASES = [
    {"id": "correct", "title": "정확한 분석", "checks": ["관측 완료·고유 유저 계산을 인정한다", "원인 분석이나 세그먼트 누락을 부당하게 감점하지 않는다"]},
    {"id": "duplicate", "title": "중복 집계 오류", "checks": ["반복 세션으로 분모가 늘어난 오류를 지적한다", "유저 단위 중복 제거를 다음 행동으로 안내한다"]},
    {"id": "incomplete", "title": "관측 미완료 포함", "checks": ["관측 미완료 유저가 분모에 포함된 오류를 지적한다", "D8 수집 완료 경계로 대상자를 제한하도록 안내한다"]},
    {"id": "causal", "title": "근거 없는 원인 단정", "checks": ["이탈률 계산은 인정한다", "가입 버전이나 난이도의 인과 효과를 입증하지 못했다고 지적한다"]},
    {"id": "alternative", "title": "다른 방식의 올바른 SQL", "checks": ["기준 SQL과 달라도 같은 정의·결과이면 인정한다", "LEFT JOIN 집계라는 이유만으로 오류라고 단정하지 않는다"]},
]


def initialize(store):
    with store.connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS quality_runs (run_id text PRIMARY KEY, payload jsonb NOT NULL)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS quality_one_active ON quality_runs ((payload->>'status')) WHERE payload->>'status' = 'running'")
        conn.execute("UPDATE quality_runs SET payload = payload || %s WHERE payload->>'status' = 'running'", (Jsonb({"status": "interrupted", "error": "서버가 재시작되어 실행이 중단됐습니다. 새 묶음으로 다시 실행하세요."}),))
        conn.execute("""CREATE TABLE IF NOT EXISTS pilot_records (
            attempt_id text PRIMARY KEY REFERENCES attempts ON DELETE CASCADE,
            participant_code text UNIQUE NOT NULL, payload jsonb NOT NULL)""")


class QualityStart(Start):
    request_id: str = Field(min_length=1, max_length=100)


class HumanCheck(Contract):
    sample_id: str
    repetition: int = Field(ge=1, le=3)
    checks: list[Literal["pending", "pass", "fail"]] = Field(min_length=2, max_length=2)
    critical_error: Literal["pending", "yes", "no"]
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(max_length=4000)


class PilotInput(Contract):
    participant_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,30}$")
    assistance: Literal["pending", "yes", "no"] = "pending"
    assistance_note: str = Field(default="", max_length=4000)
    stopped_at: str = Field(default="", max_length=1000)
    next_action: str = Field(default="", max_length=4000)
    actionable: Literal["pending", "yes", "no"] = "pending"
    improved: Literal["pending", "yes", "no", "not_applicable"] = "pending"
    observer_note: str = Field(default="", max_length=4000)


class PilotEvent(Contract):
    stage: Literal["analysis", "report", "history"]


def pilot_event(store, attempt_id, event, status, code=None):
    with store.connect() as conn:
        row = conn.execute("SELECT payload FROM pilot_records WHERE attempt_id=%s FOR UPDATE", (attempt_id,)).fetchone()
        if not row:
            return
        payload = row["payload"]
        # Only fixed event names/status/error codes; no SQL, rows, messages or drafts.
        payload.setdefault("events", []).append({"at": now(), "event": event, "status": status, "code": code})
        conn.execute("UPDATE pilot_records SET payload=%s WHERE attempt_id=%s", (Jsonb(payload), attempt_id))


def sql_for(package, case_id):
    problem = package.problem("problem-001")
    # Package loader validated identity and hashes. Timestamps come from its problem.
    bounds = {key: problem[key] for key in ("cohort_start", "cohort_end", "data_complete_before")}
    for value in bounds.values():
        from datetime import datetime
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    base = f"""WITH cohort AS (
 SELECT user_id, date_trunc('day', signup_at AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul' AS d0
 FROM users WHERE signup_at >= TIMESTAMPTZ '{bounds['cohort_start']}' AND signup_at < TIMESTAMPTZ '{bounds['cohort_end']}'
), eligible AS (
 SELECT * FROM cohort WHERE d0 + INTERVAL '8 days' <= TIMESTAMPTZ '{bounds['data_complete_before']}'
)"""
    if case_id in {"correct", "causal"}:
        return package.reference("problem-001")["sql"]
    if case_id == "incomplete":
        base = base.replace(f"WHERE d0 + INTERVAL '8 days' <= TIMESTAMPTZ '{bounds['data_complete_before']}'", "")
    counts = "count(DISTINCT e.user_id)" if case_id in {"alternative", "incomplete"} else "count(*)"
    churned = "count(DISTINCT e.user_id) FILTER (WHERE s.session_id IS NULL)" if case_id in {"alternative", "incomplete"} else "count(*) FILTER (WHERE s.session_id IS NULL)"
    return base + f"""
SELECT {counts}::int AS eligible_count, {churned}::int AS churned_count,
 (100.0 * {churned} / NULLIF({counts},0))::float8 AS churn_rate
FROM eligible e LEFT JOIN sessions s ON s.user_id=e.user_id
 AND s.login_at >= e.d0 + INTERVAL '1 day' AND s.login_at < e.d0 + INTERVAL '8 days';"""


def prepare_samples(package, runner, run_id):
    samples = []
    reference = package.reference("problem-001")["expected"]
    for case in CASES:
        sql = sql_for(package, case["id"])
        result = runner.execute("quality:" + run_id, package.schema_name, sql)
        # These are explicit operator fixtures, not learner execution records.
        with runner.lock:
            runner.pending.pop(result["execution_id"], None)
        if result["status"] != "success" or not result["result_complete"] or len(result["rows"]) != 1:
            raise DomainError("quality_sql", "평가 표본 SQL 실행을 완료하지 못했습니다. DB·패키지를 확인하세요.")
        values = dict(zip([col["name"] for col in result["columns"]], result["rows"][0]))
        valid = all(abs(float(values[key]) - float(reference[key])) < 1e-8 for key in ("eligible_count", "churned_count", "churn_rate")) if reference["churn_rate"] is not None else False
        if (case["id"] in {"correct", "causal", "alternative"}) != valid:
            raise DomainError("quality_fixture", "표본의 정상·오류 조건이 현재 데이터에서 재현되지 않았습니다.")
        if case["id"] == "incomplete" and values["eligible_count"] <= reference["eligible_count"]:
            raise DomainError("quality_fixture", "관측 미완료 포함 표본이 재현되지 않았습니다.")
        if case["id"] == "duplicate" and values["eligible_count"] <= reference["eligible_count"]:
            raise DomainError("quality_fixture", "반복 세션 중복 표본이 재현되지 않았습니다.")
        eid = "quality-evidence-" + case["id"]
        text = f"대상 {values['eligible_count']}명, 이탈 {values['churned_count']}명, 이탈률 {float(values['churn_rate']):.1f}%."
        if case["id"] == "causal":
            text += " 이 결과만으로 튜토리얼 난이도 변경이 이탈의 원인임을 확정할 수 있다."
        definitions = {
            "duplicate": "D1~D7 세션을 LEFT JOIN하여 count(*)로 대상자를 센다. 반복 세션도 분모에 포함한다.",
            "incomplete": "지정 가입 기간의 모든 유저를 포함하고 D8 관측 완료 여부는 제한하지 않는다.",
        }
        report = {"problem_id": "problem-001", "content": {
            "problem_definition": definitions.get(case["id"], "지정 가입 기간 중 KST D8까지 관측 완료된 고유 유저의 D1~D7 미재접속 비율을 계산한다."),
            "hypothesis": "유저 단위로 재접속 유무를 집계하고 날짜 경계를 확인한다.",
            "limitations": "이 결과는 지정 기간의 재접속 여부이며 이탈 원인을 입증하는 분석은 아니다." if case["id"] != "causal" else "추가 검증 없이 원인을 확정했다.",
            "next_actions": "날짜 경계·중복·관측 조건을 검토하고 다른 가입 기간에서도 같은 정의로 계산한다."},
            "claims": [{"claim_id": "quality-claim", "text": text, "evidence_refs": [{"saved_execution_id": eid}]}]}
        expected_levels = {key: [3, 4] for key in ("problem_definition", "analysis_approach", "sql_accuracy", "interpretation", "next_actions")}
        if case["id"] in {"duplicate", "incomplete"}:
            expected_levels.update(sql_accuracy=[0, 2], interpretation=[0, 2])
        if case["id"] == "causal":
            expected_levels.update(interpretation=[0, 2])
        samples.append(dict(case, expected_levels=expected_levels, sql=sql, report=report, evidence=[{"saved_execution_id": eid, "sql": sql, "result": result}], results=[]))
    return samples


def summarize(run):
    value = copy.deepcopy(run)
    for sample in value["samples"]:
        results = sample["results"]
        scores = [r["feedback"]["total_score"] for r in results if r["status"] == "completed"]
        sample["score_range"] = round(max(scores) - min(scores), 1) if scores else None
        checked = len(results) == 3 and all(r["status"] == "completed" and r.get("human", {}).get("checks") == ["pass", "pass"] and r.get("human", {}).get("critical_error") == "no" for r in results)
        failed = any(r["status"] == "failed" or "fail" in r.get("human", {}).get("checks", []) or r.get("human", {}).get("critical_error") == "yes" for r in results)
        sample["verdict"] = "fail" if failed else "pass" if checked and sample["score_range"] <= 10 else "pending"
    value["verdict"] = "pass" if value["status"] == "completed" and all(s["verdict"] == "pass" for s in value["samples"]) else "fail" if any(s["verdict"] == "fail" for s in value["samples"]) else "pending"
    value["completed_calls"] = sum(len(s["results"]) for s in value["samples"])
    return value


def routes(app, store, runner, load, auth):
    router = APIRouter(prefix="/api/quality")

    def get_run(run_id):
        with store.connect() as conn:
            row = conn.execute("SELECT payload FROM quality_runs WHERE run_id=%s", (run_id,)).fetchone()
        if not row:
            raise DomainError("not_found", "검증 묶음을 찾을 수 없습니다.", 404)
        return row["payload"]

    def update(run_id, change):
        with store.connect() as conn:
            row = conn.execute("SELECT payload FROM quality_runs WHERE run_id=%s FOR UPDATE", (run_id,)).fetchone()
            value = row["payload"]
            change(value)
            conn.execute("UPDATE quality_runs SET payload=%s WHERE run_id=%s", (Jsonb(value), run_id))

    def evaluate(run_id, package):
        try:
            run = get_run(run_id)
            for sample in run["samples"]:
                for repetition in range(1, 4):
                    result = review_report(auth, package, sample["report"], sample["evidence"])
                    result.update(repetition=repetition, finished_at=now())
                    def append(value):
                        next(s for s in value["samples"] if s["id"] == sample["id"])["results"].append(result)
                    update(run_id, append)
            update(run_id, lambda value: value.update(status="completed", finished_at=now()))
        except Exception:
            # Exception text may contain sensitive provider/network data.
            update(run_id, lambda value: value.update(status="interrupted", error="검증 실행이 중단됐습니다. 보존된 결과를 확인하고 새 묶음으로 실행하세요.", finished_at=now()))

    @router.get("/cases")
    def cases():
        return {"version": FIXTURE_VERSION, "cases": CASES}

    @router.get("/runs")
    def runs():
        with store.connect() as conn:
            rows = conn.execute("SELECT payload FROM quality_runs ORDER BY payload->>'started_at' DESC").fetchall()
        # Full reports/evidence are fetched only when an operator opens a run.
        return {"runs": [{key: summarize(row["payload"])[key] for key in ("run_id", "started_at", "status", "verdict", "completed_calls", "package_id", "release_version")} for row in rows]}

    @router.get("/runs/{run_id}")
    def read(run_id: str):
        return summarize(get_run(run_id))

    @router.get("/runs/{run_id}/export")
    def export_run(run_id: str):
        value = summarize(get_run(run_id))
        return JSONResponse(value, headers={"Content-Disposition": f'attachment; filename="quality-{value["run_id"]}.json"'})

    @router.post("/runs")
    def begin(data: QualityStart, background: BackgroundTasks):
        with store.connect() as conn:
            # Lock is held only while preparing SQL fixtures, never during AI calls.
            conn.execute("SELECT pg_advisory_xact_lock(724105)")
            previous = conn.execute("SELECT payload FROM quality_runs WHERE payload->>'request_id'=%s", (data.request_id,)).fetchone()
            if previous:
                value = previous["payload"]
                if any(value[k] != getattr(data, k) for k in ("package_id", "release_version", "problem_id")):
                    raise DomainError("idempotency_conflict", "같은 요청 ID에 다른 패키지를 사용할 수 없습니다.", 409)
                return summarize(value)
            if conn.execute("SELECT 1 FROM quality_runs WHERE payload->>'status'='running'").fetchone():
                raise DomainError("quality_busy", "이미 평가가 실행 중입니다. 진행 중 묶음을 확인하세요.", 409)
            if data.problem_id != "problem-001":
                raise DomainError("quality_problem", "현재 검증 표본은 문제 1 전용입니다.")
            package = load(data.package_id, data.release_version)
            run_id = str(uuid.uuid4())
            samples = prepare_samples(package, runner, run_id)
            value = dict(data.model_dump(), run_id=run_id, started_at=now(), status="running", samples=samples,
                         dataset_id=package.public["dataset_id"], fixture_version=FIXTURE_VERSION, rules_version=RULES_VERSION,
                         evaluation_version=package.release_version if hasattr(package, "release_version") else data.release_version,
                         configured_model=auth.status().get("model"), provider=auth.status().get("provider"))
            conn.execute("INSERT INTO quality_runs VALUES(%s,%s)", (run_id, Jsonb(value)))
        background.add_task(evaluate, run_id, package)
        return summarize(value)

    @router.put("/runs/{run_id}/human")
    def human(run_id: str, data: HumanCheck):
        get_run(run_id)
        if not data.reviewer.strip() or (data.critical_error == "yes" or "fail" in data.checks) and not data.note.strip():
            raise DomainError("quality_judgment", "검토자와 미충족·중대한 오류 판정의 근거를 입력하세요.")
        def change(value):
            sample = next((s for s in value["samples"] if s["id"] == data.sample_id), None)
            result = next((r for r in sample["results"] if r["repetition"] == data.repetition), None) if sample else None
            if not result or result["status"] != "completed":
                raise DomainError("quality_result", "완료된 AI 평가에만 사람 판정을 저장할 수 있습니다.")
            result["human"] = dict(data.model_dump(exclude={"sample_id", "repetition"}), updated_at=now())
        update(run_id, change)
        return summarize(get_run(run_id))

    def pilot_view(row):
        value = dict(row["payload"])
        attempt = store.get(row["attempt_id"])
        value["attempt"] = attempt
        value["completed"] = any(r["status"] == "completed" for r in attempt["reviews"])
        value["independent"] = value["completed"] and value["assistance"] == "no"
        value["hint_count"] = len(attempt["hints"])
        return value

    @router.get("/pilots")
    def pilots():
        with store.connect() as conn:
            rows = conn.execute("SELECT * FROM pilot_records ORDER BY participant_code").fetchall()
        values = [pilot_view(row) for row in rows]
        independent = [p for p in values if p["independent"]]
        return {"pilots": values, "summary": {"participants": len(values), "independent_completions": len(independent),
                "actionable_independent": sum(p["actionable"] == "yes" and bool(p["next_action"].strip()) for p in independent),
                "improved": sum(p["improved"] == "yes" for p in values),
                "pending_judgments": sum(p["assistance"] == "pending" or p["actionable"] == "pending" or p["improved"] == "pending" for p in values)}}

    @router.get("/pilots/{attempt_id}/export")
    def export_pilot(attempt_id: str):
        with store.connect() as conn:
            row = conn.execute("SELECT * FROM pilot_records WHERE attempt_id=%s", (attempt_id,)).fetchone()
        if not row:
            raise DomainError("not_found", "파일럿 기록을 찾을 수 없습니다.", 404)
        value = pilot_view(row)
        return JSONResponse(value, headers={"Content-Disposition": f'attachment; filename="pilot-{value["participant_code"]}.json"'})

    @router.put("/pilots/{attempt_id}")
    def save_pilot(attempt_id: str, data: PilotInput):
        attempt = store.get(attempt_id)
        if data.improved == "yes" and len(attempt["reports"]) < 2:
            raise DomainError("pilot_revision", "최초·수정 보고서가 모두 제출된 뒤 보완 여부를 판정하세요.")
        if data.actionable == "yes" and not data.next_action.strip():
            raise DomainError("pilot_action", "구체적인 다음 행동을 입력한 뒤 구체성 판정을 저장하세요.")
        with store.locked(attempt_id) as (conn, _):
            conn.execute("SELECT pg_advisory_xact_lock(724106)")
            other = conn.execute("SELECT attempt_id FROM pilot_records WHERE participant_code=%s AND attempt_id<>%s", (data.participant_code, attempt_id)).fetchone()
            if other:
                raise DomainError("pilot_duplicate", "참가자 코드는 이미 다른 훈련에 연결되어 있습니다.", 409)
            old = conn.execute("SELECT payload FROM pilot_records WHERE attempt_id=%s FOR UPDATE", (attempt_id,)).fetchone()
            payload = dict(data.model_dump(), attempt_id=attempt_id, updated_at=now(), events=old["payload"].get("events", []) if old else [], linked_at=old["payload"]["linked_at"] if old else now())
            conn.execute("INSERT INTO pilot_records VALUES(%s,%s,%s) ON CONFLICT(attempt_id) DO UPDATE SET participant_code=EXCLUDED.participant_code,payload=EXCLUDED.payload", (attempt_id, data.participant_code, Jsonb(payload)))
        return payload

    @router.post("/pilots/{attempt_id}/stage")
    def stage(attempt_id: str, data: PilotEvent):
        store.get(attempt_id)
        pilot_event(store, attempt_id, "stage:" + data.stage, "visited")
        return {"status": "ok"}

    app.include_router(router)
