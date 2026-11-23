"""Episode cards: one short card per chat (the third of the "three representations").

  original source  → data/raw + full rows     (the whole record, for deep inspection)
  excerpts         → one per exchange          (the evidence)
  episode cards    → one per chat, this file   (for locating and filtering only)

A card says what the chat was for (its title, or your first task prompt), how it went
(prompts, specificity, friction) and how it ended (shipped, changed files, talk only, or
stopped after friction). Every field is computed locally from the store; a Claude-written
`description` can be added later (Haiku, batched) without changing anything else.

Rule kept by `find`: a card can point you at a chat, but it is never the evidence. Every
card `find` returns comes with the excerpts that matched.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import tzinfo

from pompy import score
from pompy.stats import IDLE_GAP_MINUTES, parse_ts

INTENT_CHARS = 200
WORK_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
_SHIPPED_RE = re.compile(r"\b(committed|pushed|deployed|merged)\b", re.IGNORECASE)
_NEGATORS = frozenset({"not", "nothing", "never", "no", "without", "nor"})

OUTCOMES = {
    "shipped": "shipped (committed, pushed or deployed)",
    "changed files": "changed files",
    "stopped after friction": "stopped after friction",
    "talk only": "talk only (no files changed)",
}


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _shipped(replies: list[str]) -> bool:
    """A reply says the work was committed, pushed, deployed or merged (not "not committed")."""
    for text in replies:
        for m in _SHIPPED_RE.finditer(text):
            before = re.findall(r"[a-z']+", text[max(0, m.start() - 40) : m.start()].lower())[-4:]
            if not any(w in _NEGATORS or w.endswith("n't") for w in before):
                return True
    return False


def build(conn: sqlite3.Connection, tz: tzinfo | None = None) -> dict:
    """Rebuild every card from the store (after excerpts exist). Returns counts."""
    conn.row_factory = sqlite3.Row
    ex: dict[str, list] = defaultdict(list)
    for e in conn.execute(
        "SELECT e.session_id, e.ts, e.prompt, e.friction, p.has_paste, p.has_image "
        "FROM main.excerpts e JOIN main.prompts p ON p.uuid = e.prompt_uuid "
        "ORDER BY e.session_id, e.ts"
    ):
        ex[e["session_id"]].append(e)
    replies: dict[str, list] = defaultdict(list)
    for r in conn.execute("SELECT session_id, text FROM main.replies ORDER BY session_id, ts"):
        replies[r["session_id"]].append(r["text"])
    work: Counter = Counter(
        dict(
            conn.execute(
                f"SELECT session_id, COUNT(*) FROM main.tool_uses "
                f"WHERE name IN ({','.join('?' * len(WORK_TOOLS))}) GROUP BY session_id",
                WORK_TOOLS,
            ).fetchall()
        )
    )
    models: dict[str, Counter] = defaultdict(Counter)
    for t in conn.execute("SELECT session_id, model FROM main.turns WHERE model IS NOT NULL"):
        if not t["model"].startswith("<"):
            models[t["session_id"]][t["model"]] += 1

    rows = []
    for s in conn.execute("SELECT * FROM main.sessions"):
        sid = s["session_id"]
        exchanges = ex.get(sid)
        if not exchanges:
            continue  # no typed exchange: nothing to remember this chat by
        # Same inputs as `pompy score`, so a card's mean matches the prompts' own scores.
        scored = [
            score.score_text(e["prompt"], bool(e["has_paste"]), bool(e["has_image"]))
            for e in exchanges
        ]
        tasks = [(e, r) for e, r in zip(exchanges, scored, strict=True) if r["role"] == "task"]
        kinds: Counter = Counter(k for e in exchanges for k in e["friction"].split(",") if k)
        with_friction = sum(1 for e in exchanges if e["friction"])
        if _shipped(replies.get(sid, [])):
            outcome = "shipped"
        elif work[sid]:
            outcome = "changed files"
        elif exchanges[-1]["friction"]:
            outcome = "stopped after friction"
        else:
            outcome = "talk only"
        intent_src = tasks[0][0]["prompt"] if tasks else exchanges[0]["prompt"]
        first = exchanges[0]["ts"]
        # Active minutes: gaps between your prompts count only up to the idle limit, so a chat
        # reopened two weeks later doesn't read as two weeks of work (same rule as `pompy stats`).
        times = [parse_ts(e["ts"], None) for e in exchanges]
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:], strict=False)]
        minutes = round(sum(g for g in gaps if g <= IDLE_GAP_MINUTES))
        rows.append(
            (
                sid,
                first,
                s["project_key"],
                s["project_name"],
                s["title"] or None,
                _clip(intent_src, INTENT_CHARS),
                _clip(exchanges[-1]["prompt"], INTENT_CHARS),
                parse_ts(first, tz).strftime("%Y-%m-%d %H:%M"),
                minutes,
                len(exchanges),
                len(tasks),
                round(sum(r["score"] for _, r in tasks) / len(tasks), 2) if tasks else None,
                with_friction,
                json.dumps(dict(kinds)),
                work[sid],
                models[sid].most_common(1)[0][0] if models[sid] else None,
                outcome,
            )
        )
    conn.execute("DELETE FROM episode_cards")
    conn.executemany(
        "INSERT INTO episode_cards (session_id, ts, project_key, project_name, title, intent, "
        "last_prompt, local_start, minutes, exchanges, tasks, mean_score, friction_exchanges, "
        "friction_kinds, files_changed, model, outcome) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return {"cards": len(rows), "outcomes": dict(Counter(r[16] for r in rows))}


def cards(conn: sqlite3.Connection, project: str | None = None, limit: int = 50) -> list[dict]:
    """Cards, newest first. Respects a WorkingSet lens (a card belongs to when it started)."""
    from pompy.search import project_keys

    conn.row_factory = sqlite3.Row
    sql, params = "SELECT * FROM episode_cards", []
    if project:
        keys = project_keys(conn, project)
        if not keys:
            return []
        sql += f" WHERE project_key IN ({','.join('?' * len(keys))})"
        params += keys
    sql += " ORDER BY ts DESC LIMIT ?"
    return [_card(r) for r in conn.execute(sql, [*params, limit])]


def _card(row) -> dict:
    d = dict(row)
    d["friction_kinds"] = json.loads(d["friction_kinds"] or "{}")
    return d


def find(conn: sqlite3.Connection, query: str, embedder=None, chats: int = 5, tz=None) -> list:
    """Which chats were about `query`? Cards to locate them, each with the matching excerpts
    as its evidence (hybrid search, grouped by chat, best chat first)."""
    from pompy import hybrid

    conn.row_factory = sqlite3.Row
    hits = hybrid.search(conn, query, embedder, limit=30, tz=tz)
    by_chat: dict[str, list] = {}
    for h in hits:
        by_chat.setdefault(h["session_id"], []).append(h)
    out = []
    for sid, evidence in list(by_chat.items())[:chats]:
        row = conn.execute("SELECT * FROM episode_cards WHERE session_id = ?", (sid,)).fetchone()
        if row:
            out.append({"card": _card(row), "evidence": evidence[:3]})
    return out


def render_card(c: dict) -> str:
    head = c["title"] or c["intent"]
    score_txt = f"{c['mean_score']}/7" if c["mean_score"] is not None else "no tasks"
    fr = ", ".join(f"{n} {k}" for k, n in c["friction_kinds"].items()) or "none"
    return "\n".join(
        [
            f"{c['local_start']}  {c['project_name'] or c['project_key']}  ·  "
            f"{c['minutes']} active min",
            f"  {head}",
            f"  {c['exchanges']} exchanges ({c['tasks']} tasks, mean {score_txt}) · "
            f"friction: {fr} · ended: {OUTCOMES[c['outcome']]}",
        ]
    )
