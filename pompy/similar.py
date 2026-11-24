"""Episodic recall for one prompt: "last time you asked something like this…".

Finds the past exchanges most like a prompt (hybrid search: keywords + meaning) and says what
happened after them: how many went wrong, the most common kind of trouble, and one lesson
drawn from it. Pompy shows this AFTER a prompt is sent (the notch) or on the dashboard; it
never holds a prompt.

Memory concept: retrieval from episodic memory to inform the present. The episodes are
excerpts (Step 10), found by hybrid retrieval (Step 12); the lesson is a tiny, rule-based
step toward long-term memory (Phase 3 will learn these from evidence instead).
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import tzinfo

LESSONS = {
    "clarify": "Claude often had to ask what you meant. Name the page, file or error up front.",
    "correction": "You often corrected Claude after these. Say what to keep or avoid.",
    "problem": "These often broke afterwards. Say how you'll check that it works.",
    "interrupt": "You stopped Claude mid-way on these. Give the limits up front.",
    "rejected": "You rejected Claude's changes on these. Say which files it may touch.",
    "rephrase": "You had to ask again on these. Be specific the first time.",
}
OUTCOME_LABEL = {
    "clarify": "Claude had to ask",
    "correction": "you corrected it",
    "problem": "it broke after",
    "interrupt": "you interrupted",
    "rejected": "you rejected a change",
    "rephrase": "you asked again",
}


def similar(
    conn: sqlite3.Connection,
    text: str,
    embedder=None,
    limit: int = 5,
    tz: tzinfo | None = None,
) -> dict:
    """The `limit` past exchanges most like `text`, and what happened after them."""
    from pompy import hybrid

    text = " ".join(text.split())
    hits = [
        h
        for h in hybrid.search(conn, text, embedder, limit=limit + 3, tz=tz)
        if " ".join(h["prompt"].split()) != text  # not the prompt itself, if already stored
    ][:limit]
    kinds = Counter(k for h in hits for k in h["friction"].split(",") if k)
    went_wrong = sum(1 for h in hits if h["friction"])
    top_kind = kinds.most_common(1)[0][0] if kinds else None
    return {
        "query": text,
        "mode": "hybrid" if embedder is not None else "keyword",
        "count": len(hits),
        "went_wrong": went_wrong,
        "kinds": dict(kinds),
        "lesson": LESSONS[top_kind] if top_kind and went_wrong >= 2 else None,
        "hits": [
            {
                "uuid": h["uuid"],
                "local_time": h["local_time"],
                "project": h["project_name"] or h["project_key"],
                "prompt": h["prompt"][:240],
                "outcome": h["outcome"],
                "friction": [k for k in h["friction"].split(",") if k],
                "found_by": [m for m in ("keyword", "meaning") if h.get(f"{m}_rank") is not None],
            }
            for h in hits
        ],
    }


def render(result: dict) -> str:
    if not result["count"]:
        return "Nothing like this in your history yet."
    out = [
        f"{result['count']} past exchanges like this ({result['mode']} search); "
        f"{result['went_wrong']} went wrong."
    ]
    if result["lesson"]:
        out.append(f"Lesson: {result['lesson']}")
    out.append("")
    for h in result["hits"]:
        mark = "✕" if h["friction"] else "·"
        out.append(f" {mark} {h['local_time']}  {h['project']}")
        out.append(f"   You: {h['prompt'][:140]}")
        out.append(f"   Then: {h['outcome']}")
    return "\n".join(out)
