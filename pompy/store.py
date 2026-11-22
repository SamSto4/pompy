"""Pompy's memory store: one SQLite file, local and git-ignored."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path, PurePosixPath

from pompy.parse import Event, Item, Prompt, SessionInfo, Turn

# Derived by `pompy score` and recomputed each run, so it can be dropped and recreated freely.
PROMPT_SCORES_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_scores (  -- derived by `pompy score`; recomputed each run
    uuid        TEXT PRIMARY KEY,
    session_id  TEXT,
    ts          TEXT,
    role        TEXT,                 -- task | reply | routine | other
    score       INTEGER,              -- 0-7 for tasks, NULL otherwise
    f_goal INTEGER, f_context INTEGER, f_constraints INTEGER, f_done INTEGER,
    f_format INTEGER, f_example INTEGER, f_reference INTEGER,
    fr_correction INTEGER, fr_problem INTEGER, fr_interrupt INTEGER, fr_rejected INTEGER,
    fr_rephrase INTEGER, fr_clarify INTEGER,
    friction    INTEGER,
    rubric_version INTEGER
);
"""

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    project_key    TEXT,              -- short hash of the folder path (safe to share)
    project_name   TEXT,              -- folder name (local only)
    cwd            TEXT,              -- full path, used to match Pre-flight to a project
    entrypoint     TEXT,              -- cli | claude-desktop | claude-vscode | ...
    version        TEXT,
    title          TEXT,
    first_ts       TEXT,
    last_ts        TEXT,
    subagent_files INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prompts (
    id          INTEGER PRIMARY KEY,  -- stable row id for the keyword index
    uuid        TEXT NOT NULL UNIQUE,
    session_id  TEXT NOT NULL,
    ts          TEXT,                 -- UTC ISO-8601, as Claude Code wrote it
    kind        TEXT NOT NULL,        -- typed | command | bash | suggestion
    source      TEXT,
    text        TEXT NOT NULL,        -- everything sent, pasted text included
    own_text    TEXT NOT NULL,        -- only the words you wrote (pasted blocks removed)
    char_len    INTEGER,
    word_len    INTEGER,
    has_paste   INTEGER,
    has_image   INTEGER,
    parent_uuid TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    message_id            TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL,
    ts                    TEXT,
    model                 TEXT,
    effort                TEXT,
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cache_read_tokens     INTEGER,
    cache_creation_tokens INTEGER
);
CREATE TABLE IF NOT EXISTS tool_uses (  -- one row per tool call, so re-reading never double-counts
    tool_use_id TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    ts          TEXT,
    name        TEXT
);
CREATE TABLE IF NOT EXISTS replies (  -- what Claude said back, one row per record with text
    record_uuid TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    ts          TEXT,
    text        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    uuid       TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ts         TEXT,
    kind       TEXT NOT NULL,
    PRIMARY KEY (uuid, kind)
);
CREATE TABLE IF NOT EXISTS files (     -- ingest bookkeeping: how far each transcript was read
    source_path TEXT PRIMARY KEY,
    raw_path    TEXT,
    session_id  TEXT,
    offset      INTEGER NOT NULL,
    size        INTEGER NOT NULL
);
-- Layer 3a of the RAG architecture: keyword / exact-match index (BM25), with stemming.
CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
    text, content='prompts', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS prompts_fts_insert AFTER INSERT ON prompts BEGIN
    INSERT INTO prompts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS prompts_fts_delete AFTER DELETE ON prompts BEGIN
    INSERT INTO prompts_fts(prompts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE INDEX IF NOT EXISTS idx_prompts_session ON prompts(session_id);
CREATE INDEX IF NOT EXISTS idx_prompts_ts ON prompts(ts);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_uses_message ON tool_uses(message_id);
CREATE INDEX IF NOT EXISTS idx_replies_session ON replies(session_id, ts);
"""
    + PROMPT_SCORES_SCHEMA
)


# Bump when the schema OR the parsing rules change: older stores are rebuilt from data/raw/.
SCHEMA_VERSION = 4


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    is_new = not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'prompts'").fetchone()
    if is_new:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif not needs_rebuild(conn):
        conn.executescript(SCHEMA)
    return conn


def needs_rebuild(conn: sqlite3.Connection) -> bool:
    """True when the store was made by an older Pompy and must be rebuilt from raw copies."""
    return conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION


def project_key(cwd: str) -> str:
    return _digest(cwd)


def _digest(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def save(conn: sqlite3.Connection, item: Item) -> str:
    """Write one parsed item. Returns its kind name, for counting."""
    if isinstance(item, Prompt):
        cur = conn.execute(
            """INSERT OR IGNORE INTO prompts (uuid, session_id, ts, kind, source, text, own_text,
                   char_len, word_len, has_paste, has_image, parent_uuid)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item.uuid,
                item.session_id,
                item.ts,
                item.kind,
                item.source,
                item.text,
                item.own_text,
                item.char_len,
                item.word_len,
                int(item.has_paste),
                int(item.has_image),
                item.parent_uuid,
            ),
        )
        return "prompt" if cur.rowcount else "duplicate"
    if isinstance(item, Turn):
        # One message can arrive as several records: merge them. Every write here is
        # idempotent, so reading the same record twice changes nothing.
        conn.execute(
            """INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(message_id) DO UPDATE SET
                 model = COALESCE(turns.model, excluded.model),
                 effort = COALESCE(turns.effort, excluded.effort),
                 input_tokens = MAX(turns.input_tokens, excluded.input_tokens),
                 output_tokens = MAX(turns.output_tokens, excluded.output_tokens),
                 cache_read_tokens = MAX(turns.cache_read_tokens, excluded.cache_read_tokens),
                 cache_creation_tokens =
                     MAX(turns.cache_creation_tokens, excluded.cache_creation_tokens)""",
            (
                item.message_id,
                item.session_id,
                item.ts,
                item.model,
                item.effort,
                item.input_tokens,
                item.output_tokens,
                item.cache_read_tokens,
                item.cache_creation_tokens,
            ),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO tool_uses VALUES (?,?,?,?,?)",
            [
                (tid, item.message_id, item.session_id, item.ts, name)
                for tid, name in item.tool_uses
            ],
        )
        if item.text:
            key = item.record_uuid or f"{item.message_id}:{_digest(item.text)}"
            conn.execute(
                "INSERT OR IGNORE INTO replies VALUES (?,?,?,?,?)",
                (key, item.message_id, item.session_id, item.ts, item.text),
            )
        return "turn"
    if isinstance(item, Event):
        cur = conn.execute(
            "INSERT OR IGNORE INTO events VALUES (?,?,?,?)",
            (item.uuid, item.session_id, item.ts, item.kind),
        )
        return "event" if cur.rowcount else "duplicate"
    if isinstance(item, SessionInfo):
        cwd = item.cwd
        conn.execute(
            """INSERT INTO sessions (session_id, project_key, project_name, cwd, entrypoint,
                                     version, title, first_ts, last_ts)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 project_key = COALESCE(sessions.project_key, excluded.project_key),
                 project_name = COALESCE(sessions.project_name, excluded.project_name),
                 cwd = COALESCE(sessions.cwd, excluded.cwd),
                 entrypoint = COALESCE(sessions.entrypoint, excluded.entrypoint),
                 version = COALESCE(excluded.version, sessions.version),
                 title = COALESCE(excluded.title, sessions.title),
                 first_ts = MIN(COALESCE(sessions.first_ts, excluded.first_ts),
                                COALESCE(excluded.first_ts, sessions.first_ts)),
                 last_ts = MAX(COALESCE(sessions.last_ts, excluded.last_ts),
                               COALESCE(excluded.last_ts, sessions.last_ts))""",
            (
                item.session_id,
                project_key(cwd) if cwd else None,
                PurePosixPath(cwd).name if cwd else None,
                cwd,
                item.entrypoint,
                item.version,
                item.title,
                item.ts,
                item.ts,
            ),
        )
        return "session"
    raise TypeError(f"unknown item {item!r}")


def forget_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Drop everything derived from one transcript (used when a file must be re-read)."""
    for table in ("prompts", "turns", "tool_uses", "replies", "events"):
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
