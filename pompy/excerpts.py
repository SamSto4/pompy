"""Exchange excerpts: Pompy's episodic memory units (layer 2 of the RAG architecture).

One excerpt per typed prompt, holding the whole exchange:
  - a context header: project · local date · the prompt just before (or "opened the chat")
  - your prompt (own words only, pastes left out)
  - the start of Claude's last reply before your next prompt
  - what happened next: friction (corrected, interrupted, Claude had to ask ...) or "moved on"

The header makes a bare "fix it" findable: it carries where and after what it was said
(Anthropic's "contextual retrieval"). `body` is what both indexes see: FTS5 now, embeddings
in Step 11. Excerpts are derived data, rebuilt in full after each ingest; `text_hash` lets the
embedder skip excerpts whose text has not changed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from datetime import tzinfo

from pompy import score, store
from pompy.search import project_keys, to_fts_query
from pompy.stats import parse_ts

REPLY_CHARS = 600  # how much of Claude's reply an excerpt keeps
PREVIOUS_CHARS = 100  # how much of the previous prompt goes in the header

SCHEMA = store.EXCERPTS_SCHEMA

OUTCOME_WORDS = {
    "correction": "you corrected it",
    "problem": "you reported a problem",
    "interrupt": "you interrupted",
    "rejected": "you rejected a tool call",
    "rephrase": "you asked again",
    "clarify": "Claude had to ask",
}


def _flat(text: str) -> str:
    return " ".join(text.split())


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def build(conn: sqlite3.Connection, tz: tzinfo | None = None) -> dict:
    """Rebuild every excerpt from the store. Returns counts."""
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    fr = score.friction(conn)
    prompts = conn.execute(
        "SELECT p.uuid, p.session_id, p.ts, p.kind, p.own_text, s.project_name, s.project_key "
        "FROM main.prompts p JOIN main.sessions s USING (session_id) "
        "WHERE p.ts IS NOT NULL ORDER BY p.session_id, p.ts"
    ).fetchall()
    replies: dict[str, list] = defaultdict(list)
    for r in conn.execute(
        "SELECT session_id, ts, text FROM main.replies WHERE ts IS NOT NULL ORDER BY session_id, ts"
    ):
        replies[r["session_id"]].append((r["ts"], r["text"]))

    by_session: dict[str, list] = defaultdict(list)
    for p in prompts:
        by_session[p["session_id"]].append(p)

    rows = []
    for sid, plist in by_session.items():
        prev_typed = None
        for i, p in enumerate(plist):
            if p["kind"] != "typed":
                continue
            own = _flat(p["own_text"] or "")
            if not own:
                continue
            nxt = plist[i + 1] if i + 1 < len(plist) else None
            end = nxt["ts"] if nxt else "9999"
            said = [t for ts, t in replies[sid] if p["ts"] <= ts < end]
            reply = _clip(_flat(said[-1]), REPLY_CHARS) if said else ""

            flags = fr.get(p["uuid"], {})
            kinds = [k for k in score.FRICTION_KINDS if flags.get(k)]
            parts = [OUTCOME_WORDS[k] for k in kinds]
            if "clarify" in kinds and said:
                q = score.final_question(said[-1])
                if q:
                    parts[kinds.index("clarify")] = f'Claude had to ask: "{_clip(q, 160)}"'
            outcome = "; ".join(parts) if parts else ("moved on" if nxt else "end of chat")

            project = p["project_name"] or p["project_key"]
            day = parse_ts(p["ts"], tz).strftime("%Y-%m-%d")
            before = (
                f'after "{_clip(prev_typed, PREVIOUS_CHARS)}"' if prev_typed else "opened the chat"
            )
            header = f"{project} · {day} · {before}"
            body = f"{header}\nYou: {own}\nClaude: {reply or '(no reply text)'}\nThen: {outcome}"
            rows.append(
                (
                    p["uuid"],
                    sid,
                    p["ts"],
                    header,
                    own,
                    reply,
                    outcome,
                    ",".join(kinds),
                    body,
                    hashlib.sha1(body.encode()).hexdigest(),
                )
            )
            prev_typed = own

    conn.execute("DELETE FROM excerpts")
    conn.execute("DELETE FROM excerpts_fts")
    conn.executemany(
        "INSERT INTO excerpts (prompt_uuid, session_id, ts, header, prompt, reply, outcome, "
        "friction, body, text_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.execute("INSERT INTO excerpts_fts (rowid, body) SELECT id, body FROM excerpts")
    conn.commit()
    return {
        "excerpts": len(rows),
        "with_reply": sum(1 for r in rows if r[5]),
        "with_friction": sum(1 for r in rows if r[7]),
    }


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
    project: str | None = None,
    tz: tzinfo | None = None,
    match_any: bool = False,
) -> list[dict]:
    """Keyword search over whole exchanges (BM25). Respects a WorkingSet lens."""
    fts_query = to_fts_query(query, match_any)
    if not fts_query:
        return []
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT e.prompt_uuid AS uuid, e.session_id, e.ts, e.header, e.prompt, e.reply,
               e.outcome, e.friction, s.project_name, s.project_key,
               bm25(excerpts_fts) AS score
        FROM excerpts_fts
        JOIN excerpts e ON e.id = excerpts_fts.rowid
        JOIN sessions s ON s.session_id = e.session_id
        WHERE excerpts_fts MATCH ?
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
        }
        for r in conn.execute(sql, params)
    ]


def count(conn: sqlite3.Connection) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM main.excerpts").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
