"""Project facts: each project's goal and your preferences there (consolidation, Phase 3).

For every project with enough history, a small model reads its episode cards (chat titles,
first prompts, how chats ended) and returns its goal and up to 3 preferences as structured
JSON. Each becomes a long-term fact citing the chats it came from. A fact's confidence grows
with how many chats back it, and fades with time since the project was last active, so a
project you stop working on is gradually forgotten (`patterns.retire_stale`).

Memory concept: consolidation, many episodes compressed into a few durable facts, kept only
while they keep being true.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime

from pompy import patterns

DEFAULT_MODEL = "claude-haiku-5-5"
MIN_CHATS = 2
MIN_EXCHANGES = 5
CHATS_PER_PROJECT = 12  # most recent chats read per project
MAX_PREFERENCES = 3
FULL_CONFIDENCE_CHATS = 6  # a fact backed by this many chats starts at full confidence

INSTRUCTIONS = """You read summaries of one developer's chats with Claude Code inside a single \
project, and state what the project is for and how the developer likes to work in it.

Return:
- goal: one sentence, at most 160 characters: what the project is building or trying to \
achieve. Plain words, no hype.
- preferences: up to 3 short statements (each at most 120 characters) about how they want \
work done in this project, ONLY where several chats show it (tools or stack they insist on, \
style, what they ask Claude to avoid, how they check work). Empty list if nothing recurs.
- evidence_chats: the numbers of the chats that best show the goal (2 to 5).
Never include secrets, keys, emails, phone numbers or other people's personal details."""

SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "preferences": {"type": "array", "items": {"type": "string"}},
        "evidence_chats": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["goal", "preferences", "evidence_chats"],
    "additionalProperties": False,
}


def projects(conn: sqlite3.Connection) -> list[dict]:
    """Projects with enough history, each with its recent chats and their first exchange."""
    conn.row_factory = sqlite3.Row
    first = {
        r["session_id"]: r["prompt_uuid"]
        for r in conn.execute(
            "SELECT session_id, prompt_uuid FROM excerpts e WHERE ts = "
            "(SELECT MIN(ts) FROM excerpts e2 WHERE e2.session_id = e.session_id)"
        )
    }
    by_project: dict[str, list] = defaultdict(list)
    for c in conn.execute("SELECT * FROM episode_cards ORDER BY ts DESC"):
        by_project[c["project_key"]].append(dict(c))
    out = []
    for key, cards in by_project.items():
        if len(cards) < MIN_CHATS or sum(c["exchanges"] for c in cards) < MIN_EXCHANGES:
            continue
        recent = cards[:CHATS_PER_PROJECT]
        out.append(
            {
                "key": key,
                "name": cards[0]["project_name"] or key,
                "chats": len(cards),
                "last_seen": cards[0]["ts"],
                "cards": [{**c, "first_uuid": first.get(c["session_id"])} for c in recent],
            }
        )
    return out


def brief(project: dict) -> str:
    """What the model reads about one project: numbered chats, newest first."""
    from pompy.publish import redact  # paths, keys and emails never leave the Mac as typed

    lines = [f"Project: {project['name']} ({project['chats']} chats)"]
    for i, c in enumerate(project["cards"], 1):
        title = f"title: {c['title']}; " if c["title"] else ""
        lines.append(
            f"{i}. {c['local_start'][:10]} · {redact(title)}first prompt: "
            f"{redact(c['intent'][:220])} · "
            f"{c['exchanges']} exchanges · ended: {c['outcome']}"
        )
    return "\n".join(lines)


def summarise(client, project: dict, model: str = DEFAULT_MODEL) -> tuple[dict, dict]:
    """One structured call per project. Returns (facts, usage)."""
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=INSTRUCTIONS,
        messages=[{"role": "user", "content": brief(project)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    usage = {"input": response.usage.input_tokens or 0, "output": response.usage.output_tokens or 0}
    if response.stop_reason == "refusal":
        return {}, usage
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), usage


def store(
    conn: sqlite3.Connection, project: dict, facts: dict, now: datetime | None = None
) -> list:
    """Write a project's goal and preferences as facts; retire preferences that went away."""
    if not facts.get("goal"):
        return []
    cards = project["cards"]
    picked = [cards[i - 1] for i in facts.get("evidence_chats", []) if 1 <= i <= len(cards)]
    evidence = [c["first_uuid"] for c in (picked or cards[:3]) if c["first_uuid"]]
    confidence = round(min(1.0, project["chats"] / FULL_CONFIDENCE_CHATS), 2)
    scope = f"project: {project['name']}"
    stored = [
        patterns.record_fact(
            conn,
            key=f"goal@{project['key']}",
            statement=" ".join(facts["goal"].split())[:200],
            scope=scope,
            kind="goal",
            evidence=evidence,
            confidence=confidence,
            last_seen=_iso(project["last_seen"]),
            note=f"from {len(cards)} of {project['chats']} chats",
            now=now,
        )
    ]
    prefs = [" ".join(p.split())[:160] for p in facts.get("preferences", []) if p.strip()]
    for i, pref in enumerate(prefs[:MAX_PREFERENCES]):
        stored.append(
            patterns.record_fact(
                conn,
                key=f"pref@{project['key']}#{i}",
                statement=pref,
                scope=scope,
                kind="preference",
                evidence=evidence,
                confidence=round(confidence * 0.8, 2),
                last_seen=_iso(project["last_seen"]),
                now=now,
            )
        )
    at = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    for i in range(len(prefs[:MAX_PREFERENCES]), MAX_PREFERENCES):  # a preference that stopped
        conn.execute(
            "UPDATE patterns SET status='retired', last_checked=? "
            "WHERE key=? AND status!='retired'",
            (at, f"pref@{project['key']}#{i}"),
        )
    conn.commit()
    return stored


def _iso(ts: str) -> str:
    """Transcript UTC timestamps ('...Z') to the ISO form patterns.decayed reads."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).isoformat(timespec="seconds")


def refresh(conn: sqlite3.Connection, client, model: str = DEFAULT_MODEL, now=None) -> dict:
    """Summarise every project with enough history and store its facts."""
    usage = {"input": 0, "output": 0}
    done = []
    for project in projects(conn):
        facts, u = summarise(client, project, model)
        usage["input"] += u["input"]
        usage["output"] += u["output"]
        store(conn, project, facts, now)
        if facts.get("goal"):
            done.append({"project": project["name"], "goal": facts["goal"],
                         "preferences": facts.get("preferences", [])[:MAX_PREFERENCES],
                         })  # fmt: skip
    return {"projects": done, "usage": usage, "model": model}
