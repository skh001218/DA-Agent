"""Persistent shared rolling input estimate for the single Discord deployment."""
import sqlite3
import time
import uuid
from pathlib import Path


class ApiBudget:
    def __init__(self, path, *, limit=14000, window=65):
        self.path, self.limit, self.window = Path(path), limit, window
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, scope TEXT, at REAL, tokens INTEGER)')
            if 'pending' not in {r[1] for r in conn.execute('PRAGMA table_info(calls)')}:
                conn.execute('ALTER TABLE calls ADD COLUMN pending INTEGER NOT NULL DEFAULT 0')
            conn.execute('CREATE TABLE IF NOT EXISTS cooldowns (scope TEXT PRIMARY KEY, until_at REAL)')

    def reserve(self, scope, tokens, *, now=None):
        now = time.time() if now is None else now
        if tokens > self.limit:
            return None, None  # A single oversized request cannot fit after waiting.
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM calls WHERE (pending=0 AND at<=?) OR (pending=1 AND at<=?)',
                         (now-self.window, now-3*self.window))
            cooldown = conn.execute('SELECT until_at FROM cooldowns WHERE scope=?', (scope,)).fetchone()
            delay = max(0, cooldown[0]-now) if cooldown else 0
            calls = conn.execute('SELECT at,tokens,pending FROM calls WHERE scope=? ORDER BY pending,at', (scope,)).fetchall()
            remaining = sum(c[1] for c in calls) + tokens
            for at, used, pending in calls:
                if remaining <= self.limit:
                    break
                delay = max(delay, (now if pending else at)+self.window-now)
                remaining -= used
            if delay > 0:
                return None, delay
            ident = uuid.uuid4().hex
            conn.execute('INSERT INTO calls (id,scope,at,tokens,pending) VALUES (?,?,?,?,1)', (ident, scope, now, tokens))
            return ident, 0

    def actual(self, ident, tokens, *, now=None):
        now = time.time() if now is None else now
        with sqlite3.connect(self.path) as conn:
            if isinstance(tokens, int) and tokens >= 0:
                conn.execute('UPDATE calls SET tokens=?,at=?,pending=0 WHERE id=?', (tokens, now, ident))
            else:
                conn.execute('UPDATE calls SET at=?,pending=0 WHERE id=?', (now, ident))

    def cooldown(self, scope, seconds):
        with sqlite3.connect(self.path) as conn:
            conn.execute('INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET until_at=MAX(until_at,excluded.until_at)',
                         (scope, time.time()+seconds))
