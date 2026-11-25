"""Episodic recall for one prompt: "last time you asked something like this…".

Finds the past exchanges most like a prompt (hybrid search: keywords + meaning) and says what
happened after them: how many went wrong, the most common kind of trouble, and one lesson
drawn from it. Pompy shows this AFTER a prompt is sent (the notch) or on the dashboard; it
never holds a prompt.

Memory concept: retrieval from episodic memory to inform the present. The episodes are
excerpts (Step 10), found by hybrid retrieval (Step 12); the lesson is a tiny, rule-based
step toward long-term memory. When long-term memory holds a SUPPORTED pattern that applies to
this prompt (Step 20), that pattern is reported too, ahead of the rule-based lesson.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, tzinfo

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
    pattern = applies(conn, text, {h["uuid"] for h in hits}, tz=tz)
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
        "pattern": pattern,
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


def applies(
    conn: sqlite3.Connection,
    text: str,
    neighbours: set[str],
    tz: tzinfo | None = None,
    now: datetime | None = None,
) -> dict | None:
    """The strongest supported habit or risk that applies to `text`, if any.

    A statistical pattern applies when its condition is true of this prompt (it lacks that
    rubric part, it is very short, it is late at night). An agent's pattern applies when one of
    this prompt's nearest past exchanges is part of its supporting evidence. Patterns scoped to
    one theme are skipped: placing a new prompt in a theme needs the theme model.
    """
    from pompy import patterns, score

    try:
        held = patterns.load(conn, status="supported", now=now)
    except sqlite3.Error:
        return None
    scored = score.score_text(text)
    if scored["role"] != "task":  # the rules are claims about task prompts, not "yes" or questions
        return None
    missing = set(scored["missing"])
    hour = (now or datetime.now()).astimezone(tz).hour
    for p in held:  # strongest first
        if p["kind"] not in ("risk", "habit"):
            continue
        key = p["key"]
        if key.startswith("agent:"):
            hit = bool(neighbours & {e["prompt_uuid"] for e in p["supports"]})
        elif not key.endswith("@all"):
            continue
        elif key.startswith("lacks:"):
            hit = key[len("lacks:") : -len("@all")] in missing
        elif key == "very-short@all":
            hit = len(text.split()) <= 6
        elif key == "late-night@all":
            hit = hour < 5
        else:
            continue
        if hit:
            return {"key": key, "statement": p["statement"],
                    "confidence": round(p["confidence_now"], 2)}  # fmt: skip
    return None


def render(result: dict) -> str:
    learned = result.get("pattern")
    if not result["count"]:
        none = "Nothing like this in your history yet."
        return f"{none}\nPompy has learned: {learned['statement']}" if learned else none
    out = [
        f"{result['count']} past exchanges like this ({result['mode']} search); "
        f"{result['went_wrong']} went wrong."
    ]
    if learned:
        out.append(f"Pompy has learned: {learned['statement']}")
    if result["lesson"]:
        out.append(f"Lesson: {result['lesson']}")
    out.append("")
    for h in result["hits"]:
        mark = "✕" if h["friction"] else "·"
        out.append(f" {mark} {h['local_time']}  {h['project']}")
        out.append(f"   You: {h['prompt'][:140]}")
        out.append(f"   Then: {h['outcome']}")
    return "\n".join(out)
