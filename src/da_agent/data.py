"""Deterministic CSV generator and independently checked PostgreSQL answers."""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random

from .packages import PackageCatalog, PackageError, digest, write_json

KST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 1, tzinfo=KST)
END = datetime(2026, 9, 15, tzinfo=KST)
COMPLETE = datetime(2026, 9, 19, tzinfo=KST)
USER_FIELDS = ["user_id", "signup_at", "platform", "country", "acquisition_channel", "signup_app_version"]
SESSION_FIELDS = ["session_id", "user_id", "login_at", "logout_at", "app_version"]
DATA_DICTIONARY = {
    "users": {"grain": "유저 1명", "columns": {
        "user_id": "text, 유저 고유 ID (기본 키)", "signup_at": "timestamptz, 가입 시각",
        "platform": "text, 가입 플랫폼 android/ios/pc", "country": "text, 가입 국가 KR",
        "acquisition_channel": "text, 유입 채널 organic/ad/referral", "signup_app_version": "text, 가입 앱 버전"}},
    "sessions": {"grain": "로그인 세션 1회", "columns": {
        "session_id": "text, 세션 고유 ID (기본 키)", "user_id": "text, users.user_id 참조 (외래 키)",
        "login_at": "timestamptz, 로그인 시작 시각", "logout_at": "nullable timestamptz, 종료 시각; 종료 미기록은 null",
        "app_version": "text, 세션 앱 버전"}},
    "time_rules": "파일 시각은 UTC ISO 8601 초 단위. PostgreSQL timestamptz 저장. 날짜 집계는 signup_at AT TIME ZONE 'Asia/Seoul'로 변환하세요.",
    "categories": {"platform": ["android", "ios", "pc"], "country": ["KR"], "acquisition_channel": ["organic", "ad", "referral"], "app_version": ["1.0.0"]}}


def iso(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def midnight(value):
    return value.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)


def calculate(users, sessions, start=START, end=END, complete=COMPLETE):
    logins = {}
    for row in sessions:
        logins.setdefault(row["user_id"], []).append(dt(row["login_at"]))
    eligible, churned, incomplete = [], [], []
    for row in users:
        signup = dt(row["signup_at"])
        if not start <= signup < end:
            continue
        d0 = midnight(signup)
        if d0 + timedelta(days=8) > complete:
            incomplete.append(row["user_id"])
            continue
        eligible.append(row["user_id"])
        if not any(d0 + timedelta(days=1) <= login < d0 + timedelta(days=8) for login in logins.get(row["user_id"], [])):
            churned.append(row["user_id"])
    return {"eligible_count": len(eligible), "churned_count": len(churned), "excluded_incomplete_count": len(incomplete), "churn_rate": len(churned) * 100 / len(eligible) if eligible else None}


REFERENCE_SQL = """WITH cohort AS (
 SELECT user_id, date_trunc('day', signup_at AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul' AS d0
 FROM users WHERE signup_at >= TIMESTAMPTZ '{start}' AND signup_at < TIMESTAMPTZ '{end}'
), eligible AS (
 SELECT * FROM cohort WHERE d0 + INTERVAL '8 days' <= TIMESTAMPTZ '{complete}'
), flags AS (
 SELECT e.user_id, NOT EXISTS (SELECT 1 FROM sessions s WHERE s.user_id=e.user_id
 AND s.login_at >= e.d0 + INTERVAL '1 day' AND s.login_at < e.d0 + INTERVAL '8 days') AS churned FROM eligible e
)
SELECT count(*)::int AS eligible_count, count(*) FILTER (WHERE churned)::int AS churned_count,
 (SELECT count(*) FROM cohort WHERE d0 + INTERVAL '8 days' > TIMESTAMPTZ '{complete}')::int AS excluded_incomplete_count,
 (100.0 * count(*) FILTER (WHERE churned) / NULLIF(count(*),0))::float8 AS churn_rate FROM flags;"""


def generate_package(root, package_id="training-001", release_version="v1", seed=20261003, user_count=200):
    if user_count < 12:
        raise ValueError("At least 12 users needed for boundary cases")
    # Validate identity before filesystem mutation.
    import re
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", package_id) or not re.fullmatch(r"v[1-9][0-9]*", release_version):
        raise PackageError("Invalid package identity")
    path = Path(root) / package_id / release_version
    path.mkdir(parents=True, exist_ok=False)
    (path / "public").mkdir()
    (path / "private").mkdir()
    rng = random.Random(seed)
    users, sessions = [], []
    for index in range(user_count):
        if index < 9:
            signup = START + timedelta(hours=23 if index == 8 else 0)
        elif index == 9:
            signup = END
        elif index == 10:
            signup = START + timedelta(days=10)  # D8 exactly collection cutoff
        elif index == 11:
            signup = START + timedelta(days=11)  # incomplete observation
        else:
            signup = START + timedelta(seconds=rng.randrange(14 * 86400))
        user_id = f"u{index + 1:04d}"
        users.append(dict(zip(USER_FIELDS, [user_id, iso(signup), rng.choice(["android", "ios", "pc"]), "KR", rng.choice(["organic", "ad", "referral"]), "1.0.0"])))
        def add(login, logout=None):
            if login < COMPLETE:
                sessions.append(dict(zip(SESSION_FIELDS, [f"s{len(sessions)+1:05d}", user_id, iso(login), iso(logout) if logout else "", "1.0.0"])))
        add(signup, signup + timedelta(hours=2) if index == 8 else signup + timedelta(minutes=20))
        base = midnight(signup)
        if index == 2:
            add(base + timedelta(days=1))
        elif index == 3:
            add(base + timedelta(days=8, seconds=-1))
        elif index == 4:
            add(base + timedelta(days=8))
        elif index == 5:
            add(base + timedelta(days=3))
        elif index == 6:
            for day in [1, 2, 2, 7]:
                add(base + timedelta(days=day, hours=12))
        elif index >= 12 and rng.random() < .65:
            for _ in range(rng.randint(1, 4)):
                add(base + timedelta(days=rng.randint(1, 7), seconds=rng.randint(0, 86399)))
    for table, rows, fields in [("users", users, USER_FIELDS), ("sessions", sessions, SESSION_FIELDS)]:
        with (path / f"public/{table}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    dataset_id = "ds-" + digest(path / "public/users.csv")[:16] + digest(path / "public/sessions.csv")[:16]
    identity = {"package_id": package_id, "release_version": release_version, "dataset_id": dataset_id}
    problem = {**identity, "problem_id": "problem-001", "problem_type_id": "new-user-churn", "problem_version": release_version,
               "title": "신규 유저 D1~D7 미재접속 이탈률", "description": "지정 가입 코호트에서 가입 다음 날부터 7일간 재접속하지 않은 유저의 비율을 구하세요. 관측 완료 유저만 포함하고 대상자 수, 이탈자 수, 제외 인원, 이탈률 및 계산 기준을 제출하세요.",
               "cohort_start": iso(START), "cohort_end": iso(END), "data_complete_before": iso(COMPLETE), "timezone": "Asia/Seoul", "required_tables": ["users", "sessions"],
               "definitions": {"observation": "KST 가입 날짜 D1 00:00 포함부터 D8 00:00 제외", "complete": "D8 00:00 <= data_complete_before", "return": "관측 구간 안에서 시작한 로그인 1회 이상", "empty_denominator": "계산 불가 (null)", "display": "이탈률 소수점 한 자리"}}
    write_json(path / "public/problem-001.json", problem)
    reference = {**identity, "problem_id": "problem-001", "evaluation_version": release_version,
                 "sql": REFERENCE_SQL.format(start=iso(START), end=iso(END), complete=iso(COMPLETE)), "expected": calculate(users, sessions),
                 "hints": {"direction": "가입 날짜를 Asia/Seoul로 해석하고 D1~D7을 정의하세요.", "metric": "D8이 수집 완료 경계 이하인 유저만 분모에 넣으세요.", "sql_structure": "세션 수가 아닌 고유 유저를 세고 NOT EXISTS로 재접속이 없는 유저를 찾으세요."},
                 "explanation": "D0와 D8 로그인은 재접속에서 제외하며 반복 로그인은 한 유저로 셉니다. D8까지 관측되지 않은 유저는 분모와 분자 모두에서 제외합니다."}
    write_json(path / "private/reference-001.json", reference)
    write_json(path / "public/manifest.json", {**identity, "dataset_version": release_version, "schema_version": "1", "timezone": "Asia/Seoul", "timestamp_format": "UTC ISO 8601 seconds", "data_complete_before": iso(COMPLETE),
        "data_files": [{"table": table, "path": f"{table}.csv", "sha256": digest(path / f"public/{table}.csv")} for table in ["users", "sessions"]],
        "data_dictionary": DATA_DICTIONARY,
        "problems": [{"problem_id": "problem-001", "problem_type_id": "new-user-churn", "problem_version": release_version, "title": problem["title"], "path": "problem-001.json", "sha256": digest(path / "public/problem-001.json"), "cohort_start": iso(START), "cohort_end": iso(END), "required_tables": ["users", "sessions"]}]})
    write_json(path / "private/manifest.json", {**identity, "dataset_version": release_version, "generator_version": "1", "seed": seed, "user_count": user_count,
        "problems": [{"problem_id": "problem-001", "evaluation_version": release_version, "path": "reference-001.json", "sha256": digest(path / "private/reference-001.json")} ]})
    return PackageCatalog(root).load(package_id, release_version, allow_unvalidated=True)


def validate_rows(package):
    users, sessions = package.rows("users"), package.rows("sessions")
    ids = {r["user_id"]: r for r in users}
    if len(ids) != len(users) or len({r["session_id"] for r in sessions}) != len(sessions):
        raise PackageError("Duplicate primary key")
    for row in users:
        if set(row) != set(USER_FIELDS) or not all(row.values()):
            raise PackageError("Invalid user columns")
        dt(row["signup_at"])
    for row in sessions:
        if set(row) != set(SESSION_FIELDS) or any(not row[key] for key in SESSION_FIELDS if key != "logout_at"):
            raise PackageError("Invalid session columns")
        if row["user_id"] not in ids or dt(row["login_at"]) < dt(ids[row["user_id"]]["signup_at"]):
            raise PackageError("Invalid session reference/time")
        if dt(row["login_at"]) >= dt(package.public["data_complete_before"]):
            raise PackageError("Login outside collected data")
        if row["logout_at"] and dt(row["logout_at"]) < dt(row["login_at"]):
            raise PackageError("Invalid logout time")
    return users, sessions


def load_package(conn, package):
    from psycopg import sql
    users, sessions = validate_rows(package)
    schema = sql.Identifier(package.schema_name)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
        cur.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {}.users (user_id text PRIMARY KEY, signup_at timestamptz NOT NULL, platform text NOT NULL, country text NOT NULL, acquisition_channel text NOT NULL, signup_app_version text NOT NULL)").format(schema))
        cur.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {}.sessions (session_id text PRIMARY KEY, user_id text NOT NULL REFERENCES {}.users(user_id), login_at timestamptz NOT NULL, logout_at timestamptz, app_version text NOT NULL, CHECK (logout_at IS NULL OR logout_at >= login_at))").format(schema, schema))
        current = {}
        for table, fields in [("users", USER_FIELDS), ("sessions", SESSION_FIELDS)]:
            cur.execute(sql.SQL("SELECT {} FROM {}.{} ORDER BY {}").format(sql.SQL(",").join(map(sql.Identifier, fields)), schema, sql.Identifier(table), sql.Identifier(fields[0])))
            normalized = []
            for db_row in cur.fetchall():
                values = [db_row[k] for k in fields] if isinstance(db_row, dict) else db_row
                normalized.append({k: iso(v) if isinstance(v, datetime) else (v or "") for k, v in zip(fields, values)})
            current[table] = normalized
        if current["users"] or current["sessions"]:
            if current["users"] != sorted(users, key=lambda r: r["user_id"]) or current["sessions"] != sorted(sessions, key=lambda r: r["session_id"]):
                raise PackageError("Existing release database differs from immutable package")
        for table, rows, fields in [("users", users, USER_FIELDS), ("sessions", sessions, SESSION_FIELDS)]:
            statement = sql.SQL("INSERT INTO {}.{} VALUES ({}) ON CONFLICT DO NOTHING").format(schema, sql.Identifier(table), sql.SQL(",").join([sql.Placeholder()] * len(fields)))
            cur.executemany(statement, [[r[k] or None for k in fields] for r in rows])
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO learner").format(schema))
        cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO learner").format(schema))
    conn.commit()


def validate_package(conn, root, package_id="training-001", release_version="v1"):
    from psycopg import sql
    package = PackageCatalog(root).load(package_id, release_version, allow_unvalidated=True)
    users, sessions = validate_rows(package)
    load_package(conn, package)
    results, boundaries = {}, {}
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'UTC'")
        cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(package.schema_name)))
        for entry in package.public["problems"]:
            pid = entry["problem_id"]
            problem, ref = package.problem(pid), package.reference(pid)
            expected = calculate(users, sessions, dt(problem["cohort_start"]), dt(problem["cohort_end"]), dt(problem["data_complete_before"]))
            cur.execute(ref["sql"])
            raw = cur.fetchone()
            actual = dict(raw) if isinstance(raw, dict) else dict(zip([c.name for c in cur.description], raw))
            for key, value in expected.items():
                if key == "churn_rate" and value is not None:
                    matches = actual.get(key) is not None and abs(float(actual[key]) - value) < 1e-9
                else:
                    matches = actual.get(key) == value
                if not matches or ref["expected"].get(key) != value:
                    raise PackageError("Reference SQL/Python answer mismatch")
            results[pid] = actual
            # Run actual SQL for each generated edge user plus an empty cohort.
            # Temporary tables shadow package tables only during this validation.
            cur.execute(sql.SQL("CREATE TEMP TABLE boundary_users AS SELECT * FROM {}.users WHERE false").format(sql.Identifier(package.schema_name)))
            cur.execute(sql.SQL("CREATE TEMP TABLE boundary_sessions AS SELECT * FROM {}.sessions WHERE false").format(sql.Identifier(package.schema_name)))
            cur.execute("ALTER TABLE boundary_users RENAME TO users")
            cur.execute("ALTER TABLE boundary_sessions RENAME TO sessions")
            cur.execute("SET search_path TO pg_temp")
            cases = []
            for fixture in [None] + users[:12]:
                cur.execute("TRUNCATE pg_temp.users, pg_temp.sessions")
                fixture_users = [fixture] if fixture else []
                fixture_sessions = [s for s in sessions if fixture and s["user_id"] == fixture["user_id"]]
                if fixture:
                    cur.execute("INSERT INTO pg_temp.users VALUES (%s,%s,%s,%s,%s,%s)", [fixture[k] for k in USER_FIELDS])
                if fixture_sessions:
                    cur.executemany("INSERT INTO pg_temp.sessions VALUES (%s,%s,%s,%s,%s)", [[s[k] or None for k in SESSION_FIELDS] for s in fixture_sessions])
                cur.execute(ref["sql"])
                raw = cur.fetchone()
                observed = dict(raw) if isinstance(raw, dict) else dict(zip([c.name for c in cur.description], raw))
                expected_case = calculate(fixture_users, fixture_sessions, dt(problem["cohort_start"]), dt(problem["cohort_end"]), dt(problem["data_complete_before"]))
                if observed != expected_case:
                    raise PackageError("SQL boundary case mismatch")
                cases.append({"case": fixture["user_id"] if fixture else "empty", "result": observed})
            cur.execute("DROP TABLE pg_temp.sessions, pg_temp.users")
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(package.schema_name)))
            boundaries[pid] = cases
    conn.commit()
    write_json(package.path / "private/validation.json", {"status": "publishable", "engine": "PostgreSQL", "public_sha256": digest(package.path / "public/manifest.json"), "private_sha256": digest(package.path / "private/manifest.json"), "results": results, "boundary_cases": boundaries})
    return PackageCatalog(root).load(package_id, release_version)
