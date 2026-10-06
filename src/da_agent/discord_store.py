"""Discord-only PostgreSQL records. Never initialize the web records tables."""
import copy
import datetime as dt
import json
import uuid
from urllib.parse import urlparse
from contextlib import contextmanager

import psycopg
from psycopg.types.json import Jsonb

from .errors import DomainError


class DiscordStore:
    def __init__(self, records_dsn):
        if not records_dsn:
            raise ValueError('DISCORD_RECORDS_DSN must be explicitly configured')
        if urlparse(records_dsn).path.strip('/') in {'records', 'training', 'postgres'}:
            raise ValueError('Discord records must use an isolated database')
        self.dsn = records_dsn

    def connect(self):
        return psycopg.connect(self.dsn, connect_timeout=5)

    def initialize(self):
        with self.connect() as conn:
            conn.execute('CREATE SCHEMA IF NOT EXISTS discord_records')
            conn.execute('''CREATE TABLE IF NOT EXISTS discord_records.sessions (
                session_id text PRIMARY KEY, owner_id text NOT NULL, guild_id text NOT NULL,
                thread_id text UNIQUE, updated_at timestamptz NOT NULL DEFAULT now(),
                document jsonb NOT NULL)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS discord_records.events (
                event_id text PRIMARY KEY, owner_id text NOT NULL, session_id text,
                status text NOT NULL, response jsonb, request jsonb, created_at timestamptz NOT NULL DEFAULT now())''')
            conn.execute('ALTER TABLE discord_records.events ADD COLUMN IF NOT EXISTS request jsonb')
            conn.execute('''CREATE TABLE IF NOT EXISTS discord_records.usage (
                owner_id text NOT NULL, day date NOT NULL, calls integer NOT NULL,
                PRIMARY KEY(owner_id, day))''')

    def claim_event(self, event_id, user_id, session_id=None, request=None):
        if not event_id or len(str(event_id)) > 180:
            raise DomainError('invalid_event', '이벤트 ID를 확인하세요.')
        with self.connect() as conn:
            row = conn.execute('''INSERT INTO discord_records.events
                (event_id,owner_id,session_id,status,request) VALUES (%s,%s,%s,'running',%s)
                ON CONFLICT DO NOTHING RETURNING event_id''',
                (str(event_id), str(user_id), session_id, Jsonb(request or {}))).fetchone()
            if row:
                return None
            prior = conn.execute('SELECT owner_id,session_id,status,response FROM discord_records.events WHERE event_id=%s', (str(event_id),)).fetchone()
            if prior[0] != str(user_id) or prior[1] != session_id:
                raise DomainError('forbidden', '다른 사용자의 이벤트에 접근할 수 없습니다.', 403)
            if prior[3] is not None:
                return prior[3]
            return {'messages': ['이 요청은 처리 중이거나 중단되어 결과를 확인해야 합니다. 자동으로 재실행하지 않습니다. /resume로 기록을 확인하세요.'], 'event_state': 'uncertain'}

    def finish_event(self, event_id, response, conn=None):
        if conn is not None:
            conn.execute("UPDATE discord_records.events SET status='completed',response=%s WHERE event_id=%s", (Jsonb(response), str(event_id)))
        else:
            with self.connect() as connection:
                self.finish_event(event_id, response, connection)

    def create(self, document, event_id, response):
        with self.connect() as conn:
            conn.execute('''INSERT INTO discord_records.sessions(session_id,owner_id,guild_id,document)
                VALUES (%s,%s,%s,%s)''', (document['session_id'], document['owner_user_id'], document['guild_id'], Jsonb(document)))
            self.finish_event(event_id, response, conn)

    def get(self, user_id, session_id):
        with self.connect() as conn:
            row = conn.execute('SELECT owner_id,document FROM discord_records.sessions WHERE session_id=%s', (session_id,)).fetchone()
        if not row or row[0] != str(user_id):
            raise DomainError('forbidden', '자신의 훈련 기록만 사용할 수 있습니다.', 403)
        return row[1]

    def list(self, user_id, guild_id=None):
        with self.connect() as conn:
            return [row[0] for row in conn.execute('''SELECT document FROM discord_records.sessions
                WHERE owner_id=%s AND (%s::text IS NULL OR guild_id=%s) ORDER BY updated_at DESC''',
                (str(user_id), str(guild_id) if guild_id is not None else None, str(guild_id) if guild_id is not None else None))]

    @contextmanager
    def edit(self, user_id, session_id):
        with self.connect() as conn:
            row = conn.execute('SELECT owner_id,document FROM discord_records.sessions WHERE session_id=%s FOR UPDATE', (session_id,)).fetchone()
            if not row or row[0] != str(user_id):
                raise DomainError('forbidden', '자신의 훈련 기록만 사용할 수 있습니다.', 403)
            document = copy.deepcopy(row[1])
            yield document, conn
            conn.execute('''UPDATE discord_records.sessions SET document=%s,thread_id=%s,updated_at=now()
                WHERE session_id=%s''', (Jsonb(document), document.get('thread_id'), session_id))

    def reserve_call(self, user_id, limit):
        if limit < 1:
            raise DomainError('usage_limit', '새 API 작업이 비활성화되어 있습니다.', 429)
        day = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
        with self.connect() as conn:
            row = conn.execute('''INSERT INTO discord_records.usage(owner_id,day,calls) VALUES (%s,%s,1)
                ON CONFLICT(owner_id,day) DO UPDATE SET calls=discord_records.usage.calls+1
                WHERE discord_records.usage.calls < %s RETURNING calls''', (str(user_id), day, limit)).fetchone()
        if not row:
            raise DomainError('usage_limit', '오늘의 API 호출 한도에 도달했습니다. 기존 기록은 계속 열람할 수 있습니다.', 429)
        return row[0]

    def reserve_thread_name(self, user_id, session_id):
        from .discord_thread_titles import reserve_title
        snapshot = self.get(user_id, session_id)
        scope = f"thread-name:{user_id}:{snapshot['guild_id']}:{snapshot['channel_id']}"
        with self.connect() as conn:
            # Lock the whole naming scope before locking the individual session.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (scope,))
            row = conn.execute('SELECT owner_id,document FROM discord_records.sessions WHERE session_id=%s FOR UPDATE', (session_id,)).fetchone()
            if not row or row[0] != str(user_id):
                raise DomainError('forbidden', '자신의 훈련 기록만 사용할 수 있습니다.', 403)
            document = copy.deepcopy(row[1])
            previous = [r[0] for r in conn.execute('''SELECT document FROM discord_records.sessions
                WHERE owner_id=%s AND guild_id=%s AND document->>'channel_id'=%s''',
                (str(user_id), document['guild_id'], document['channel_id']))]
            reserve_title(document, previous)
            conn.execute('UPDATE discord_records.sessions SET document=%s WHERE session_id=%s', (Jsonb(document), session_id))
        return document


def record_id():
    return str(uuid.uuid4())


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()
