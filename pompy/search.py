"""`pompy search`: keyword search over every prompt you've sent (layer 3a: BM25 via FTS5).

Matches word stems ("fixing buttons" finds "fix the button"). Each hit carries its source
reference (session + record uuid), so it can always be traced back to the original transcript.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import tzinfo

from pompy.stats import parse_ts

_PART_RE = re.compile(r"[^\W_]+")


def to_fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 query: every word must match (implicit AND).

    Each whitespace-separated chunk becomes one phrase, so "it's" must appear as "it s"
    (adjacent) and "co-author" as "co author", not as two unrelated words anywhere.
    """
    phrases = (" ".join(_PART_RE.findall(chunk)) for chunk in query.split())
    return " ".join(f'"{p}"' for p in phrases if p)


def project_keys(conn: sqlite3.Connection, project: str) -> list[str]:
    """Projects matching `project`: an exact name (any case) or key wins; else name contains it."""
    rows = conn.execute("SELECT DISTINCT project_key, project_name FROM sessions").fetchall()
    wanted = project.lower()
    exact = [k for k, name in rows if k == project or (name or "").lower() == wanted]
    if exact:
        return exact
    return [k for k, name in rows if wanted in (name or "").lower()]


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
    project: str | None = None,
    raw: bool = False,
    tz: tzinfo | None = None,
) -> list[dict]:
    fts_query = query if raw else to_fts_query(query)
    if not fts_query:
        return []
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT p.uuid, p.session_id, p.ts, p.kind, s.project_name, s.project_key,
               snippet(prompts_fts, 0, '[', ']', '…', 14) AS snippet,
               bm25(prompts_fts) AS score
        FROM prompts_fts
        JOIN prompts p ON p.id = prompts_fts.rowid
        JOIN sessions s ON s.session_id = p.session_id
        WHERE prompts_fts MATCH ?
    """
    params: list = [fts_query]
    if project:
        keys = project_keys(conn, project)
        if not keys:
            return []
        sql += f" AND s.project_key IN ({','.join('?' * len(keys))})"
        params += keys
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    return [
        {
            **dict(r),
            "local_time": parse_ts(r["ts"], tz).strftime("%Y-%m-%d %H:%M") if r["ts"] else None,
            "snippet": " ".join(r["snippet"].split()),
        }
        for r in conn.execute(sql, params)
    ]
