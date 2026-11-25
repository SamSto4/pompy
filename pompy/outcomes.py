"""Was Claude's reply useful to you? A verdict for every task prompt, and for the bad ones, why.

Pompy reads your reaction to each reply (the next thing you typed in that chat, and what
happened in between) and sorts each task prompt into:

  - useful     you said it worked, thanked it, or asked to commit or ship it;
  - moved on   you went on to something else without pushing back (probably fine);
  - unclear    Claude had to ask what you meant before it could act;
  - missed     you corrected it, asked again, stopped it, rejected its change, or said it broke;
  - no reaction  the chat ended there, so there is nothing to judge.

For an unclear or missed reply, `cause()` reasons from what happened back to what the prompt
lacked, in three short steps (what happened → what the prompt was missing → so Claude did X),
using only facts Pompy has: the kind of trouble, the rubric parts the prompt lacked, and
Claude's own question when it asked one.

Memory concept: an outcome label on each episode. Episodic memory gets far more useful once
each episode records not only what you said but whether it worked.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

from pompy import score

POSITIVE_RE = re.compile(
    r"^\W*(perfect|great|nice|awesome|amazing|excellent|thanks|thank you|thx|ty|works|it works|"
    r"that works|working now|works now|looks good|looks great|lgtm|love it|love this|good job|"
    r"well done|beautiful|sweet|yes+|yep|yeah|cool|ok good|good)\b"
    r"|\b(commit|push|merge|ship|deploy)\b.{0,30}$",
    re.IGNORECASE,
)
MISSED_KINDS = ("correction", "problem", "rephrase", "interrupt", "rejected")
VERDICTS = ("useful", "moved on", "unclear", "missed", "no reaction")

# Claude asking for a go-ahead ("Should I start Step 4, or hold off?") is not confusion.
PERMISSION_RE = re.compile(
    r"\b(start|begin|proceed|go ahead|continue|commit|push|publish|merge|deploy|ship|move on|"
    r"hold off|not yet|as written|approve|run it|install)\b",
    re.IGNORECASE,
)

# What each rubric part would have told Claude, in plain words.
_LACKED = {
    "reference": "which file, page or error to look at",
    "context": "where this happens or why",
    "constraints": "what to keep, avoid or stay inside",
    "done": "how you would check that it worked",
    "example": "what the result should look like",
    "format": "what shape of answer you wanted",
    "goal": "what you wanted done",
}
# Words in Claude's question that point to the part the prompt lacked.
_QUESTION_HINTS = [
    (
        "reference",
        re.compile(
            r"\b(which|what) (file|page|component|screen|error|route|repo|"
            r"folder|function|link|url)|\bwhere\b",
            re.I,
        ),
    ),  # fmt: skip
    (
        "constraints",
        re.compile(
            r"\b(should i|do you want|would you like|keep|replace|remove|"
            r"overwrite|or)\b",
            re.I,
        ),
    ),  # fmt: skip
    ("format", re.compile(r"\b(format|list|table|summary|how (long|detailed))\b", re.I)),
    ("example", re.compile(r"\b(like|look like|style|design|similar)\b", re.I)),
]


def judge(conn: sqlite3.Connection) -> dict[str, str]:
    """{prompt uuid: verdict} for every typed task prompt with a score."""
    from pompy.publish import _clarifying_questions

    questions = _clarifying_questions(conn)
    conn.row_factory = sqlite3.Row
    roles = {r["uuid"]: r["role"] for r in conn.execute("SELECT uuid, role FROM prompt_scores")}
    fr = score.friction(conn)
    by_session: dict[str, list] = defaultdict(list)
    for p in conn.execute(
        "SELECT uuid, session_id, ts, kind, own_text FROM prompts WHERE ts IS NOT NULL "
        "ORDER BY session_id, ts"
    ):
        by_session[p["session_id"]].append(p)
    out = {}
    for plist in by_session.values():
        typed = [p for p in plist if p["kind"] == "typed"]
        for i, p in enumerate(typed):
            if roles.get(p["uuid"]) != "task":
                continue
            f = fr.get(p["uuid"], {})
            nxt = typed[i + 1] if i + 1 < len(typed) else None
            if any(f.get(k) for k in MISSED_KINDS):
                out[p["uuid"]] = "missed"
            elif f.get("clarify") and not PERMISSION_RE.search(questions.get(p["uuid"]) or ""):
                out[p["uuid"]] = "unclear"
            elif nxt is None:
                out[p["uuid"]] = "no reaction"
            elif POSITIVE_RE.search((nxt["own_text"] or "").strip()[:80]):
                out[p["uuid"]] = "useful"
            else:
                out[p["uuid"]] = "moved on"
    return out


# A next message that says what you want instead is a correction of direction, not a bug report.
WANTS_RE = re.compile(
    r"^\W*(i want|i'd like|i would like|it should|they should|make it|make the|can you|could you|"
    r"instead|rather|change|let'?s make|the .{0,40} should)\b",
    re.IGNORECASE,
)


def cause(
    verdict: str,
    kinds: list[str],
    missing: list[str],
    question: str | None = None,
    next_text: str | None = None,
) -> list[str] | None:
    """Three short steps from what went wrong back to what the prompt lacked. None if fine."""
    if verdict not in ("unclear", "missed"):
        return None
    if "problem" in kinds and next_text and WANTS_RE.search(next_text):
        kinds = ["correction"]
    lacked = [m for m in missing if m != "goal"] or list(missing)
    if verdict == "unclear":
        part = next(
            (
                name
                for name, rx in _QUESTION_HINTS
                if question and rx.search(question) and name in lacked
            ),  # fmt: skip
            next((m for m in ("reference", "context", "constraints") if m in lacked), None),
        )
        said = (
            f'Claude had to ask: "{question}"' if question else "Claude had to ask what you meant."
        )
        if not part:
            return [said, "The prompt had the usual parts, so the request itself was ambiguous.",
                    "Say the one decision Claude would otherwise have to guess."]  # fmt: skip
        return [
            said,
            f"The prompt never said {_LACKED[part]}.",
            f"So Claude stopped to ask instead of acting. Next time, say {_LACKED[part]}.",
        ]
    kind = next((k for k in MISSED_KINDS if k in kinds), "correction")
    if kind == "problem":
        part = "done" if "done" in lacked else ("reference" if "reference" in lacked else None)
        first = "Your next message said something was wrong or still not right."
        then = "So Claude stopped at what looked right, without testing the real result."
    elif kind in ("interrupt", "rejected"):
        part = "constraints" if "constraints" in lacked else ("reference" if "reference" in lacked
                                                            else None)  # fmt: skip
        first = "You stopped Claude or rejected its change."
        then = "So Claude went further, or somewhere else, than you wanted."
    else:  # correction or rephrase
        part = next((m for m in ("constraints", "example", "reference", "context") if m in lacked),
                    None)  # fmt: skip
        first = ("You had to correct it: your next message said what you wanted instead."
                 if kind == "correction"
                 else "You had to ask again in other words.")  # fmt: skip
        then = "So Claude filled the gap with a guess that wasn't yours."
    if not part:
        return [first, "The prompt had the usual parts, so the cause is outside the prompt.",
                "Check Claude's reply below for where it went off."]  # fmt: skip
    return [first, f"The prompt never said {_LACKED[part]}.", then]


def model_for(conn: sqlite3.Connection) -> dict[str, tuple[str | None, str | None]]:
    """{prompt uuid: (model, effort)} of the first reply turn after each typed prompt."""
    turns: dict[str, list] = defaultdict(list)
    for sid, ts, model, effort in conn.execute(
        "SELECT session_id, ts, model, effort FROM turns "
        "WHERE ts IS NOT NULL AND model IS NOT NULL ORDER BY session_id, ts"
    ):
        turns[sid].append((ts, model, effort))
    out = {}
    for uuid, sid, ts in conn.execute(
        "SELECT uuid, session_id, ts FROM prompts WHERE kind = 'typed' AND ts IS NOT NULL"
    ):
        after = next(((m, e) for t, m, e in turns.get(sid, []) if t >= ts), None)
        if after:
            out[uuid] = after
    return out


def next_typed(conn: sqlite3.Connection) -> dict[str, str]:
    """{prompt uuid: the next thing you typed in the same chat}."""
    out, last = {}, {}
    for uuid, sid, text in conn.execute(
        "SELECT uuid, session_id, own_text FROM prompts WHERE kind = 'typed' AND ts IS NOT NULL "
        "ORDER BY session_id, ts"
    ):
        if sid in last:
            out[last[sid]] = text or ""
        last[sid] = uuid
    return out


def lacked_part(steps: list[str]) -> str | None:
    """The rubric part a cause blames ("The prompt never said X." → its part's name)."""
    for name, words in _LACKED.items():
        if len(steps) > 1 and steps[1] == f"The prompt never said {words}.":
            return name
    return None


def explain(conn: sqlite3.Connection, uuids: list[str]) -> dict[str, dict]:
    """{uuid: {verdict, steps, part}} for the given prompts (steps and part only when it missed)."""
    from pompy.publish import _clarifying_questions

    verdicts = judge(conn)
    wanted = [u for u in uuids if u in verdicts]
    if not wanted:
        return {}
    fr = score.friction(conn)
    questions = _clarifying_questions(conn)
    nxt = next_typed(conn)
    conn.row_factory = sqlite3.Row
    marks = ",".join("?" * len(wanted))
    scores = {r["uuid"]: r for r in conn.execute(
        f"SELECT * FROM prompt_scores WHERE uuid IN ({marks})", wanted)}  # fmt: skip
    out = {}
    for u in wanted:
        sc = scores.get(u)
        missing = [f.name for f in score.FEATURES if sc is None or not sc[f"f_{f.name}"]]
        kinds = [k for k in score.FRICTION_KINDS if fr.get(u, {}).get(k)]
        steps = cause(verdicts[u], kinds, missing, questions.get(u), nxt.get(u))
        out[u] = {"verdict": verdicts[u], "steps": steps,
                  "part": lacked_part(steps) if steps else None}  # fmt: skip
    return out
