"""Conservative query policy plus a separate read-only PostgreSQL identity.

SQL and DB exceptions must never be logged: error responses contain fixed messages.
"""
import datetime as dt
import decimal
import json
import logging
import threading
import time
import uuid

import psycopg
from psycopg import sql
import sqlglot
from sqlglot import exp
from .errors import DomainError

# Parser fallback warnings can include the SQL body; suppress this data path.
logging.getLogger("sqlglot").disabled = True

ALLOWED_FUNCTIONS = {
    "COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NULLIF", "ROUND",
    "ABS", "CEIL", "CEILING", "FLOOR", "LOWER", "UPPER", "LENGTH", "TRIM",
    "SUBSTRING", "CONCAT", "GREATEST", "LEAST", "DATE_TRUNC", "EXTRACT",
    "TIME_TO_STR", "TO_CHAR", "DATE", "CAST", "TRY_CAST", "IF", "CASE",
    "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "FIRST_VALUE",
    "LAST_VALUE", "AT_TIME_ZONE", "ARRAY_AGG", "BOOL_OR", "BOOL_AND",
    "STDDEV", "STDDEV_POP", "STDDEV_SAMP", "VARIANCE", "VAR_POP", "VAR_SAMP",
    "CURRENT_DATE", "CURRENT_TIMESTAMP", "TIMESTAMP_TRUNC", "TIMESTAMP", "EXISTS", "AND", "OR",
}


def check_query(text):
    try:
        trees = sqlglot.parse(text, read="postgres")
    except sqlglot.errors.SqlglotError:
        raise DomainError("syntax", "SQL 문법을 확인하세요.") from None
    if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise DomainError("blocked", "하나의 SELECT 조회만 실행할 수 있습니다.")
    tree = trees[0]
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
                             exp.Command, exp.Into, exp.Lock, exp.Copy, exp.Merge)):
            raise DomainError("blocked", "데이터 변경·잠금·외부 접근은 허용되지 않습니다.")
        if isinstance(node, exp.Table):
            if node.db or node.catalog or node.name not in {"users", "sessions", "tutorial_attempts"} | ctes:
                raise DomainError("blocked", "공개 학습 표만 조회할 수 있습니다.")
        if isinstance(node, exp.Dot):
            if isinstance(node.expression, exp.Func):
                raise DomainError("blocked", "스키마 지정 함수는 사용할 수 없습니다.")
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if name not in ALLOWED_FUNCTIONS:
                raise DomainError("blocked", "학습 조회에 허용된 집계·날짜·문자 함수만 사용할 수 있습니다.")
        if isinstance(node, exp.DataType) and any(term in node.sql().lower() for term in ("reg", "oid")):
            raise DomainError("blocked", "시스템 객체 자료형으로 변환할 수 없습니다.")
    return tree


def encode(value):
    if isinstance(value, (decimal.Decimal, int)) and not isinstance(value, bool):
        return str(value) if isinstance(value, decimal.Decimal) or abs(value) > 9007199254740991 else value
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, dt.timedelta)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [encode(x) for x in value]
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    return value


class SqlRunner:
    def __init__(self, settings):
        self.settings = settings
        self.pending = {}
        self.lock = threading.Lock()

    def execute(self, attempt_id, schema_name, text):
        started = time.monotonic()
        result = dict(execution_id=str(uuid.uuid4()), status="success",
                      executed_at=dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                      columns=[], rows=[], preview_row_count=0, total_row_count=None,
                      result_complete=False, truncated=False, error=None,
                      expires_in_seconds=self.settings.execution_ttl)
        full_rows = []
        try:
            check_query(text)
            with psycopg.connect(self.settings.learner_dsn, connect_timeout=5, options="-c default_transaction_read_only=on") as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(schema_name)))
                conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(self.settings.query_timeout_ms),))
                deadline = started + self.settings.query_timeout_ms / 1000
                timer = threading.Timer(max(0.001, deadline - time.monotonic()), conn.cancel_safe)
                timer.daemon = True
                timer.start()
                with conn.cursor(name="training_result") as cursor:
                    try:
                        cursor.execute(text)
                        size = 0
                        for row in cursor:
                            if time.monotonic() > deadline:
                                raise DomainError("timeout", "실행 제한 시간 5초를 넘었습니다.")
                            encoded = [encode(x) for x in row]
                            size += len(json.dumps(encoded, ensure_ascii=False).encode())
                            if len(full_rows) >= self.settings.max_rows or size > self.settings.max_bytes:
                                break
                            full_rows.append(encoded)
                        else:
                            result["result_complete"] = True
                        result["columns"] = [{"name": c.name, "type": conn.adapters.types.get(c.type_code).name if conn.adapters.types.get(c.type_code) else str(c.type_code)} for c in cursor.description]
                    finally:
                        timer.cancel()
            result["rows"] = full_rows[:self.settings.preview_rows]
            result["preview_row_count"] = len(result["rows"])
            result["total_row_count"] = len(full_rows) if result["result_complete"] else None
            result["truncated"] = len(full_rows) > self.settings.preview_rows or not result["result_complete"]
        except DomainError as exc:
            result.update(status="error" if exc.code == "syntax" else "timeout" if exc.code == "timeout" else "blocked", error={"code": exc.code, "message": exc.message})
        except psycopg.errors.QueryCanceled:
            result.update(status="timeout", error={"code": "timeout", "message": "실행 제한 시간 5초를 넘었습니다."})
        except psycopg.Error as exc:
            code = exc.sqlstate or "connection"
            result.update(status="error", error={"code": code, "message": "SQL 문법·표·컬럼 또는 DB 연결 상태를 확인하세요."})
        result["duration_ms"] = round((time.monotonic() - started) * 1000)
        with self.lock:
            self._purge()
            if len(self.pending) >= 100:
                oldest = min(self.pending, key=lambda k: self.pending[k][0])
                del self.pending[oldest]
            saved_result = dict(result, rows=full_rows, preview_row_count=len(full_rows), truncated=not result["result_complete"])
            self.pending[result["execution_id"]] = (time.monotonic(), attempt_id, text, saved_result)
        return result

    def _purge(self):
        now = time.monotonic()
        for key in list(self.pending):
            if now - self.pending[key][0] >= self.settings.execution_ttl:
                del self.pending[key]

    def get(self, attempt_id, execution_id):
        with self.lock:
            self._purge()
            value = self.pending.get(execution_id)
            if not value or value[1] != attempt_id:
                raise DomainError("execution_expired", "임시 실행이 만료됐습니다. 다시 실행한 후 저장하세요.", 410)
            return {"sql": value[2], "result": value[3]}
