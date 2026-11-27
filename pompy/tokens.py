"""Token usage: exactly how many tokens each prompt, chat, project and day used.

Claude Code writes the API's own usage numbers on every reply it records (input, output, cache
read and cache write). Pompy stores them in `turns`, one row per reply message, and adds them up
here. Nothing is estimated: these are the numbers the API reported.

Memory concept: the context window. Every reply re-sends the whole chat so far. Most of that is
served from the prompt cache ("cache read"), which is cheaper but still counts. The longer a chat
runs, the more each new prompt re-reads, so the same question costs more on prompt 40 than on
prompt 4. `by_position` shows that growth in your own chats.

Each reply is credited to the latest prompt sent before it in the same chat: a prompt "costs"
everything Claude did to answer it, tool calls and all.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from collections import defaultdict

from pompy import stats

KINDS = ("input", "output", "cache_read", "cache_write")
BUCKETS = [(1, 5, "1–5"), (6, 15, "6–15"), (16, 40, "16–40"), (41, 10**9, "41+")]
TOP_CHATS = 10


def _blank() -> dict:
    return {k: 0 for k in KINDS} | {"total": 0}


def _add(acc: dict, t: sqlite3.Row) -> None:
    parts = {
        "input": t["input_tokens"] or 0,
        "output": t["output_tokens"] or 0,
        "cache_read": t["cache_read_tokens"] or 0,
        "cache_write": t["cache_creation_tokens"] or 0,
    }
    for k, v in parts.items():
        acc[k] += v
    acc["total"] += sum(parts.values())


def compute(conn: sqlite3.Connection, tz=None) -> dict:
    """Totals, per day, per project, the costliest chats, cost by place in the chat, per prompt."""
    conn.row_factory = sqlite3.Row
    turns = conn.execute(
        "SELECT t.*, s.project_name FROM turns t JOIN sessions s USING (session_id) "
        "WHERE t.ts IS NOT NULL ORDER BY t.ts"
    ).fetchall()
    prompts = conn.execute(
        "SELECT uuid, session_id, ts, kind FROM prompts WHERE ts IS NOT NULL ORDER BY ts"
    ).fetchall()

    # A small number per chat, in the same order the dashboard numbers them (first prompt first).
    session_no: dict[str, int] = {}
    by_session_prompts: dict[str, list] = defaultdict(list)
    for p in prompts:
        session_no.setdefault(p["session_id"], len(session_no) + 1)
        by_session_prompts[p["session_id"]].append(p)
    times = {sid: [p["ts"] for p in ps] for sid, ps in by_session_prompts.items()}

    total = _blank()
    by_day: dict[str, dict] = defaultdict(_blank)
    by_project: dict[str, dict] = defaultdict(_blank)
    by_chat: dict[str, dict] = defaultdict(_blank)
    per_prompt: dict[str, dict] = defaultdict(_blank)
    replies_per_chat: dict[str, int] = defaultdict(int)
    unattributed = 0
    for t in turns:
        _add(total, t)
        _add(by_day[stats.parse_ts(t["ts"], tz).strftime("%Y-%m-%d")], t)
        _add(by_project[t["project_name"] or "unknown"], t)
        _add(by_chat[t["session_id"]], t)
        replies_per_chat[t["session_id"]] += 1
        i = bisect_right(times.get(t["session_id"], []), t["ts"]) - 1
        if i >= 0:
            _add(per_prompt[by_session_prompts[t["session_id"]][i]["uuid"]], t)
        else:
            unattributed += 1

    # Cost by place in the chat: typed prompts only, numbered within their chat.
    pos_sums: dict[str, list] = {label: [0, 0] for *_, label in BUCKETS}
    for ps in by_session_prompts.values():
        n = 0
        for p in ps:
            if p["kind"] != "typed":
                continue
            n += 1
            label = next(lb for a, b, lb in BUCKETS if a <= n <= b)
            pos_sums[label][0] += per_prompt.get(p["uuid"], {}).get("total", 0)
            pos_sums[label][1] += 1
    by_position = [
        {"label": lb, "prompts": c, "mean": round(s / c) if c else None}
        for lb, (s, c) in pos_sums.items()
    ]

    project_of = {t["session_id"]: t["project_name"] or "unknown" for t in turns}
    chats = sorted(by_chat.items(), key=lambda kv: -kv[1]["total"])[:TOP_CHATS]
    return {
        "total": total,
        "reread_pct": round(100 * total["cache_read"] / total["total"], 1) if total["total"] else 0,
        "replies": len(turns),
        "unattributed_replies": unattributed,
        "by_day": dict(sorted(by_day.items())),
        "by_project": dict(sorted(by_project.items(), key=lambda kv: -kv[1]["total"])),
        "top_chats": [
            {
                "si": session_no.get(sid),
                "project": project_of.get(sid, "unknown"),
                "prompts": sum(1 for p in by_session_prompts.get(sid, []) if p["kind"] == "typed"),
                "replies": replies_per_chat[sid],
                **acc,
            }
            for sid, acc in chats
        ],
        "by_position": by_position,
        "per_prompt": {u: acc["total"] for u, acc in per_prompt.items()},
    }
