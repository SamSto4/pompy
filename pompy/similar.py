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
    cwd: str | None = None,
) -> dict:
    """The `limit` past exchanges most like `text`, and what happened after them.

    `tips` lists every tip that could follow this prompt (a learned pattern, a lesson, this
    project's goal and usual gap, from the folder `cwd`), strongest first. Which of them are
    shown is decided by `advise` (the holdout and procedural memory, Phase 4).
    """
    from pompy import facts, hybrid, score

    text = " ".join(text.split())
    hits = [
        h
        for h in hybrid.search(conn, text, embedder, limit=limit + 3, tz=tz)
        if " ".join(h["prompt"].split()) != text  # not the prompt itself, if already stored
    ][:limit]
    pattern = applies(conn, text, {h["uuid"] for h in hits}, tz=tz)
    misses, why = from_misses(conn, text, [h["uuid"] for h in hits])
    kinds = Counter(k for h in hits for k in h["friction"].split(",") if k)
    went_wrong = sum(1 for h in hits if h["friction"])
    top_kind = kinds.most_common(1)[0][0] if kinds else None
    lesson = LESSONS[top_kind] if top_kind and went_wrong >= 2 else None
    scored = score.score_text(text)
    project = None
    if scored["role"] == "task":
        project = facts.for_cwd(conn, cwd, scored["missing"])
    tips = []
    if pattern:
        tips.append({"id": f"learned:{pattern['key']}", "text": pattern["statement"]})
    if misses:
        tips.append({"id": f"misses:{misses['part']}", "text": misses["text"]})
    if lesson:
        tips.append({"id": f"lesson:{top_kind}", "text": lesson})
    if project:
        tips.append({"id": project["tip"], "text": project["text"]})
    return {
        "query": text,
        "mode": "hybrid" if embedder is not None else "keyword",
        "count": len(hits),
        "went_wrong": went_wrong,
        "kinds": dict(kinds),
        "lesson": lesson,
        "pattern": pattern,
        "advice": misses,
        "project": project,
        "role": scored["role"],
        "score": scored["score"],
        "missing": scored["missing"],
        "tips": tips,
        "hits": [
            {
                "uuid": h["uuid"],
                "local_time": h["local_time"],
                "project": h["project_name"] or h["project_key"],
                "prompt": h["prompt"][:240],
                "outcome": h["outcome"],
                "friction": [k for k in h["friction"].split(",") if k],
                "found_by": [m for m in ("keyword", "meaning") if h.get(f"{m}_rank") is not None],
                "verdict": why.get(h["uuid"], {}).get("verdict"),
                "why": why.get(h["uuid"], {}).get("steps"),
            }
            for h in hits
        ],
    }


ADD = {
    "reference": "name the file, page or error",
    "context": "say where this happens and why",
    "constraints": "say what to keep or avoid",
    "done": "say how you'll check it worked",
    "example": "show what it should look like",
    "format": "say what shape of answer you want",
}


def from_misses(conn: sqlite3.Connection, text: str, uuids: list[str]) -> tuple[dict | None, dict]:
    """Personal advice from how similar past prompts went: the part their misses lacked, if this
    prompt lacks it too. Returns (advice or None, {uuid: explanation}).

    Deductive: each similar miss is traced to the part its prompt lacked (`outcomes.cause`);
    the part blamed most often, when this prompt lacks it too, becomes the tip.
    """
    from pompy import outcomes, score

    try:
        why = outcomes.explain(conn, uuids)
    except sqlite3.Error:
        return None, {}
    scored = score.score_text(text)
    if scored["role"] != "task":
        return None, why
    parts = Counter(w["part"] for w in why.values() if w["part"])
    for part, n in parts.most_common():
        if part in scored["missing"] and part in ADD:
            return {
                "part": part,
                "times": n,
                "text": f"{n} similar prompt{'s' if n > 1 else ''} missed because "
                f"{'they' if n > 1 else 'it'} left this out: {ADD[part]}.",
            }, why
    return None, why


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


def advise(result: dict, fingerprint: str, profile: dict | None) -> dict:
    """Apply the holdout and procedural memory: keep only the tips to show (Phase 4).

    The JSON keeps every candidate in `tips` (for the log) and marks which were `shown`; the
    fields the notch reads (`pattern`, `lesson`, `project`) are cleared when their tip isn't.
    """
    from pompy import advice

    decision = advice.decide([t["id"] for t in result["tips"]], fingerprint, profile)
    shown = set(decision["shown"])
    order = {t: i for i, t in enumerate(decision["shown"])}
    out = {**result, **decision, "tips": result["tips"]}
    out["shown_tips"] = sorted((t for t in result["tips"] if t["id"] in shown),
                               key=lambda t: order[t["id"]])  # fmt: skip
    if result["pattern"] and f"learned:{result['pattern']['key']}" not in shown:
        out["pattern"] = None
    if result["lesson"] and not any(t.startswith("lesson:") for t in shown):
        out["lesson"] = None
    if result.get("advice") and f"misses:{result['advice']['part']}" not in shown:
        out["advice"] = None
    if result["project"] and result["project"]["tip"] not in shown:
        out["project"] = None
    return out


def log_row(result: dict, fingerprint: str, session: str | None, project_key: str | None,
            now: float) -> dict:  # fmt: skip
    """The advice log line for one prompt: ids and numbers only, never the prompt's words."""
    return {
        "at": round(now, 3),
        "session": session,
        "project": project_key,
        "digest": fingerprint,
        "role": result.get("role"),
        "score": result.get("score"),
        "missing": result.get("missing"),
        "count": result["count"],
        "went_wrong": result["went_wrong"],
        "tips": [t["id"] for t in result["tips"]],
        "shown": result.get("shown", [t["id"] for t in result["tips"]]),
        "quiet": result.get("quiet", []),
        "held_back": result.get("held_back", False),
    }


def render(result: dict) -> str:
    learned = result.get("pattern")
    project = result.get("project")
    head = []
    if learned:
        head.append(f"Pompy has learned: {learned['statement']}")
    if result.get("advice"):
        head.append(f"Next time: {result['advice']['text']}")
    if result["lesson"] and not result.get("advice"):
        head.append(f"Lesson: {result['lesson']}")
    if project:
        head.append(project["text"])
    if not result["count"]:
        return "\n".join(["Nothing like this in your history yet.", *head])
    out = [
        f"{result['count']} past exchanges like this ({result['mode']} search); "
        f"{result['went_wrong']} went wrong.",
        *head,
    ]
    out.append("")
    for h in result["hits"]:
        mark = "✕" if h["friction"] else "·"
        out.append(f" {mark} {h['local_time']}  {h['project']}")
        out.append(f"   You: {h['prompt'][:140]}")
        out.append(f"   Then: {h['outcome']}")
    return "\n".join(out)
