"""Pompy's memory store: one SQLite file, local and git-ignored."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path, PurePosixPath

from pompy.parse import Event, Item, Prompt, SessionInfo, Turn

SCHEMA = """
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
    uuid        TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    ts          TEXT,                 -- UTC ISO-8601, as Claude Code wrote it
    kind        TEXT NOT NULL,        -- typed | command | bash | suggestion
    source      TEXT,
    text        TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_prompts_session ON prompts(session_id);
CREATE INDEX IF NOT EXISTS idx_prompts_ts ON prompts(ts);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_uses_message ON tool_uses(message_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def project_key(cwd: str) -> str:
    return hashlib.sha1(cwd.encode()).hexdigest()[:12]


def save(conn: sqlite3.Connection, item: Item) -> str:
    """Write one parsed item. Returns its kind name, for counting."""
    if isinstance(item, Prompt):
        cur = conn.execute(
            "INSERT OR IGNORE INTO prompts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                item.uuid,
                item.session_id,
                item.ts,
                item.kind,
                item.source,
                item.text,
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
    for table in ("prompts", "turns", "tool_uses", "events"):
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
