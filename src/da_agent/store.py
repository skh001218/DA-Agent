import datetime as dt
import uuid
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .errors import DomainError

TABLES = ("saved_executions", "reports", "reviews", "hint_history")
SECTIONS = {"problem_definition", "hypothesis", "interpretation", "limitations", "next_actions", "report_text", "report_evidence"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, dsn):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def initialize(self):
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS attempts (
                attempt_id text PRIMARY KEY, payload jsonb NOT NULL,
                revision integer NOT NULL DEFAULT 0, sections jsonb NOT NULL DEFAULT '{}')""")
            conn.execute("""CREATE TABLE IF NOT EXISTS draft_revisions (
                attempt_id text REFERENCES attempts, revision integer, sections jsonb NOT NULL,
                PRIMARY KEY(attempt_id, revision))""")
            for table in TABLES:
                conn.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    record_id text PRIMARY KEY, attempt_id text NOT NULL REFERENCES attempts,
                    request_id text, payload jsonb NOT NULL,
                    UNIQUE(attempt_id, request_id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS report_evidence (
                report_id text REFERENCES reports(record_id), claim_id text,
                saved_execution_id text REFERENCES saved_executions(record_id),
                PRIMARY KEY(report_id,claim_id,saved_execution_id))""")

    def create(self, content):
        value = dict(content, attempt_id=str(uuid.uuid4()), started_at=now(), explanation_viewed=False)
        with self.connect() as conn:
            conn.execute("INSERT INTO attempts(attempt_id,payload) VALUES(%s,%s)", (value["attempt_id"], Jsonb(value)))
        return self.get(value["attempt_id"])

    def list(self):
        with self.connect() as conn:
            return [row["payload"] for row in conn.execute("SELECT payload FROM attempts ORDER BY payload->>'started_at' DESC")]

    def get(self, attempt_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM attempts WHERE attempt_id=%s", (attempt_id,)).fetchone()
            if not row:
                raise DomainError("not_found", "훈련 기록을 찾을 수 없습니다.", 404)
            value = dict(row["payload"], draft={"revision": row["revision"], "sections": row["sections"]})
            for table in TABLES:
                key = "hints" if table == "hint_history" else table
                value[key] = [x["payload"] for x in conn.execute(f"SELECT payload FROM {table} WHERE attempt_id=%s ORDER BY payload->>'created_at'", (attempt_id,))]
            return value

    @contextmanager
    def locked(self, attempt_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM attempts WHERE attempt_id=%s FOR UPDATE", (attempt_id,)).fetchone()
            if not row:
                raise DomainError("not_found", "훈련 기록을 찾을 수 없습니다.", 404)
            yield conn, row

    def draft(self, attempt_id, revision, sections):
        if set(sections) - SECTIONS or any(len(text) > 20000 for text in sections.values()):
            raise DomainError("invalid_draft", "서술 구역과 최대 길이를 확인하세요.")
        with self.locked(attempt_id) as (conn, row):
            if row["revision"] != revision:
                raise DomainError("revision_conflict", "다른 저장과 충돌했습니다. 입력을 복사한 뒤 최신 기록을 확인하세요.", 409)
            merged = dict(row["sections"], **sections)
            conn.execute("UPDATE attempts SET revision=revision+1,sections=%s WHERE attempt_id=%s", (Jsonb(merged), attempt_id))
            conn.execute("INSERT INTO draft_revisions VALUES(%s,%s,%s)", (attempt_id, revision + 1, Jsonb(merged)))
            return {"revision": revision + 1, "sections": merged}

    def existing(self, conn, table, attempt_id, request_id):
        row = conn.execute(f"SELECT payload FROM {table} WHERE attempt_id=%s AND request_id=%s", (attempt_id, request_id)).fetchone()
        return row["payload"] if row else None

    def insert(self, conn, table, attempt_id, request_id, payload):
        record_id = str(uuid.uuid4())
        id_key = {"saved_executions": "saved_execution_id", "reports": "report_id", "reviews": "review_id", "hint_history": "hint_id"}[table]
        payload = dict(payload, **{id_key: record_id}, created_at=now(), attempt_id=attempt_id)
        conn.execute(f"INSERT INTO {table} VALUES(%s,%s,%s,%s)", (record_id, attempt_id, request_id, Jsonb(payload)))
        return payload

    def save_execution(self, attempt_id, request_id, get_execution):
        with self.locked(attempt_id) as (conn, row):
            existing = self.existing(conn, "saved_executions", attempt_id, request_id)
            if existing:
                return existing
            value = get_execution()
            content = {key: row["payload"][key] for key in ("package_id", "release_version", "dataset_id", "problem_id")}
            return self.insert(conn, "saved_executions", attempt_id, request_id, dict(value, **content))

    def report(self, attempt_id, data):
        with self.locked(attempt_id) as (conn, row):
            existing = self.existing(conn, "reports", attempt_id, data.request_id)
            if existing:
                return existing
            if row["revision"] != data.revision:
                raise DomainError("revision_conflict", "최신 초안이 아닙니다. 저장 상태를 확인하세요.", 409)
            if len(set(c.claim_id for c in data.claims)) != len(data.claims):
                raise DomainError("duplicate_claim", "주장 ID는 서로 달라야 합니다.")
            if sum(len(v) for v in data.content.values()) > 100000:
                raise DomainError("report_size", "보고서 길이를 줄이세요.")
            if data.previous_report_id:
                previous = conn.execute("SELECT payload FROM reports WHERE record_id=%s AND attempt_id=%s", (data.previous_report_id, attempt_id)).fetchone()
                if not previous:
                    raise DomainError("invalid_previous", "같은 훈련의 이전 제출본을 선택하세요.")
            for claim in data.claims:
                for ref in claim.evidence_refs:
                    saved = conn.execute("SELECT payload FROM saved_executions WHERE record_id=%s AND attempt_id=%s", (ref.saved_execution_id, attempt_id)).fetchone()
                    if not saved or saved["payload"]["result"]["status"] != "success":
                        raise DomainError("invalid_evidence", "같은 훈련에서 저장한 성공 결과만 근거로 연결할 수 있습니다.")
                    result = saved["payload"]["result"]
                    if any(index < 0 or index >= len(result["rows"]) for index in ref.rows) or set(ref.columns) - {c["name"] for c in result["columns"]}:
                        raise DomainError("invalid_evidence_range", "근거로 선택한 행·열 범위를 확인하세요.")
            version = conn.execute("SELECT count(*) AS n FROM reports WHERE attempt_id=%s", (attempt_id,)).fetchone()["n"] + 1
            payload = data.model_dump(exclude={"request_id"})
            payload.update(report_version=version, submitted_at=now(), **{key: row["payload"][key] for key in ("package_id", "release_version", "dataset_id", "problem_id")})
            report = self.insert(conn, "reports", attempt_id, data.request_id, payload)
            for claim in data.claims:
                for ref in claim.evidence_refs:
                    conn.execute("INSERT INTO report_evidence VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (report["report_id"], claim.claim_id, ref.saved_execution_id))
            return report

    def review_begin(self, attempt_id, report_id, request_id):
        with self.locked(attempt_id) as (conn, row):
            previous = self.existing(conn, "reviews", attempt_id, request_id)
            if previous:
                if previous["report_id"] != report_id:
                    raise DomainError("idempotency_conflict", "요청 ID가 다른 제출본의 리뷰에 사용됐습니다.", 409)
                return previous, False
            report = conn.execute("SELECT payload FROM reports WHERE record_id=%s AND attempt_id=%s", (report_id, attempt_id)).fetchone()
            if not report:
                raise DomainError("not_found", "제출본을 찾을 수 없습니다.", 404)
            return self.insert(conn, "reviews", attempt_id, request_id, dict(report_id=report_id, status="pending", feedback=None, error=None, rules_version="review-v1")), True

    def review_finish(self, review_id, result):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM reviews WHERE record_id=%s FOR UPDATE", (review_id,)).fetchone()
            payload = dict(row["payload"], **result)
            conn.execute("UPDATE reviews SET payload=%s WHERE record_id=%s", (Jsonb(payload), review_id))
            return payload

    def hint(self, attempt_id, level, content):
        with self.locked(attempt_id) as (conn, _):
            return self.insert(conn, "hint_history", attempt_id, None, dict(level=level, content=content))

    def explanation(self, attempt_id):
        with self.locked(attempt_id) as (conn, row):
            value = dict(row["payload"], explanation_viewed=True)
            conn.execute("UPDATE attempts SET payload=%s WHERE attempt_id=%s", (Jsonb(value), attempt_id))
