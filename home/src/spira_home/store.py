import json
import sqlite3
from pathlib import Path
from spira_home.models import now


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL,
          received REAL NOT NULL, result TEXT, before_state TEXT, after_state TEXT, restore_status TEXT);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, body TEXT NOT NULL, received REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS controls(id TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ownership(target TEXT, property TEXT, task TEXT, before_value TEXT,
          desired TEXT, revision TEXT, owned INTEGER NOT NULL, PRIMARY KEY(target,property));
        CREATE TABLE IF NOT EXISTS outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL,
          delivered INTEGER NOT NULL DEFAULT 0);
        ''')
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS plan_batches(id TEXT, version INTEGER, body TEXT NOT NULL,
          received REAL NOT NULL, PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS event_outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id TEXT UNIQUE, body TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS bridge_state(id TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(tasks)')}
        # SQLite DDL otherwise autocommits before a later backfill starts its
        # implicit transaction. Process death must roll back both together.
        self.db.execute('BEGIN IMMEDIATE')
        for name, kind in {'attempts':'INTEGER NOT NULL DEFAULT 0', 'next_attempt':'REAL',
                           'started_at':'REAL', 'ended_at':'REAL', 'stable_since':'REAL',
                           'trigger_since':'REAL', 'restore_retry_at':'REAL', 'gate_evidence':'TEXT', 'command_receipt':'TEXT', 'operation_id':'TEXT',
                           'event_floor':'INTEGER NOT NULL DEFAULT 0', 'started_event_floor':'INTEGER NOT NULL DEFAULT 0'}.items():
            if name not in columns:
                self.db.execute('ALTER TABLE tasks ADD COLUMN '+name+' '+kind)
                if name in ('event_floor', 'started_event_floor'):
                    timestamp = 'received' if name == 'event_floor' else 'started_at'
                    # Old rows have no same-timestamp ingress ordering witness.
                    # Conservatively exclude already-stored facts at that bound;
                    # newly arriving replay still has a later durable cursor.
                    self.db.execute('UPDATE tasks SET '+name+'=coalesce((SELECT max(o.seq) FROM event_outbox o '
                        'JOIN events e ON e.id=o.event_id WHERE e.received<=tasks.'+timestamp+'),0)')
        self.db.commit()

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        r = self.db.execute(sql, args).fetchone()
        return dict(r) if r else None

    def write(self, sql, args=()):
        self.db.execute(sql, args)
        self.db.commit()

    def publish(self, key, result, status=None):
        # Result and durable Service outbox move atomically.
        with self.db:
            if status:
                self.db.execute('UPDATE tasks SET result=?,status=? WHERE id=?', (json.dumps(result), status, key))
            else:
                self.db.execute('UPDATE tasks SET result=? WHERE id=?', (json.dumps(result), key))
            self.db.execute('INSERT INTO outbox(body) VALUES(?)', (json.dumps(result),))

    def close(self):
        self.db.close()
