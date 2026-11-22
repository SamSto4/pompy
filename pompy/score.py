"""Step 6: prompt specificity score + friction signals. The coaching core.

Two questions, answered from your own history:
1. How specific is each prompt? A rule-based rubric with 7 features, 1 point each.
2. What happened next? Friction: you corrected Claude, interrupted it, rejected a tool
   call, or re-sent nearly the same prompt.
Then: do low-scoring prompts actually cause more friction for *you*? That turns generic
prompting advice into evidence.

Not every prompt should be judged. Replies ("yes", "ok go") and routine commands
("commit and push") are complete for what they do, so they get a role and no score.

Rule-based v0: transparent and instant (Pre-flight needs < 100 ms). Phase 2 can add an
LLM judge and compare it with these rules. Bump RUBRIC_VERSION when the rules change.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from pompy import keywords as kw

RUBRIC_VERSION = 1

# ── The rubric ───────────────────────────────────────────────────────────────


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(patterns), re.IGNORECASE)


_CONTEXT_RE = _rx(
    r"\bbecause\b",
    r"\bso that\b",
    r"\bsince\b",
    r"\bthe reason\b",
    r"\bcurrently\b",
    r"\bright now\b",
    r"\bat the moment\b",
    r"\bthe (problem|issue|goal|idea|context) is\b",
    r"\bi'?m (working on|building|trying to)\b",
    r"\bwe'?re (working on|building|trying to)\b",
    r"\bit'?s for\b",
    r"\bthis is for\b",
    r"\b(for|in|on) (the|my|our|this) [\w-]+ ?(app|site|website|project|page|repo|dashboard|"
    r"api|component|screen|form|agent|service|server|backend|frontend)\b",
    r"\bwhen i\b",
    r"\bafter i\b",
)
_CONSTRAINT_RE = _rx(
    r"\bdon'?t\b",
    r"\bdo not\b",
    r"\bnever\b",
    r"\bavoid\b",
    r"\bmust(n'?t)?\b",
    r"\bonly\b",
    r"\bwithout\b",
    r"\binstead of\b",
    r"\bkeep (the|it|my|our|this|all)\b",
    r"\bmake sure\b",
    r"\bensure\b",
    r"\bshould(n'?t)?\b",
    r"\b(no more|less) than\b",
    r"\bat (most|least)\b",
    r"\bexactly\b",
    r"\bmatch(es|ing)?\b",
    r"\bconsistent with\b",
    r"\bstick to\b",
    r"\bsame (style|as)\b",
)
_DONE_RE = _rx(
    r"\bdone (means|when)\b",
    r"\bshould be able to\b",
    r"\b(so|such) that\b",
    r"\buntil\b",
    r"\bthe (end )?result should\b",
    r"\bit should (show|display|return|work|look|have|be)\b",
    r"\bexpected\b",
    r"\bsuccess(ful)?\b",
    r"\b(tests?|build|checks?) (pass|passes|passing|green)\b",
    r"\bpass(es)?\b",
    r"\bworks? when\b",
    r"\bacceptance\b",
    r"\bwhen (it'?s )?(done|finished)\b",
    r"\bverify (that|it)\b",
    r"\boutcome\b",
)
_FORMAT_RE = _rx(
    r"\b(as|in) a (list|table|bullet|paragraph|summary|diagram|chart|outline)\b",
    r"\bbullet(ed)? (points?|list)\b",
    r"\btable\b",
    r"\bjson\b",
    r"\bmarkdown\b",
    r"\bcsv\b",
    r"\byaml\b",
    r"\bformat(ted)?\b",
    r"\bstep[- ]by[- ]step\b",
    r"\b(short|brief|concise|one[- ]line|tl;?dr)\b",
    r"\bin (\d+|one|two|three|a few) (words|sentences|lines|bullets|paragraphs)\b",
    r"\b(code|diff) only\b",
    r"\bjust the (code|answer|command)\b",
    r"\btemplate\b",
)
_EXAMPLE_RE = _rx(
    r"\bfor (example|instance)\b",
    r"\be\.?g\.?\b",
    r"\bsuch as\b",
    r"\blike (this|that|the|our|my)\b",
    r"\bsimilar to\b",
    r"\blooks? like\b",
    r"\bsame as\b",
    r"\bhere'?s (an|a) (example|sample)\b",
    r"\bexample\b",
    r'"[^"\n]{12,}"',  # a quoted sample of the wording you want
)
_ERROR_RE = _rx(
    r"\b\w*(error|exception)\b:",
    r"\btraceback\b",
    r"\bline \d+\b",
    r"\bstatus (code )?\d{3}\b",
    r"\b(4\d\d|5\d\d) error\b",
)
_MENTION_RE = re.compile(r'@"[^"\n]+"|@[\w./-]+')
_PATH_RE = re.compile(
    r"\S*[/\\]\S+|\b[\w-]+\.(?:py|ts|tsx|js|jsx|json|md|css|html|sql|yml|yaml|toml|sh|swift|go|rs|java|rb|php|txt|env)\b"
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_CODE_RE = re.compile(r"`[^`\n]+`|```")
_IDENT_RE = re.compile(r"\b(?:[a-z]+[A-Z]\w*|[A-Z][a-z]+[A-Z]\w*|\w+_\w+)\b")


@dataclass(frozen=True)
class Feature:
    name: str
    label: str
    tip: str


FEATURES = (
    Feature("goal", "Goal", "Say what you want done: the action and the thing it acts on."),
    Feature("context", "Context", "Add why or where: which app or page, and what's happening now."),
    Feature("constraints", "Constraints", "State limits: what to keep, avoid, match, or must use."),
    Feature("done", "Done-definition", "Say how you'll know it worked (tests pass, page shows X)."),
    Feature(
        "format",
        "Output format",
        "Name the shape of answer you want (list, table, short, code only).",
    ),
    Feature(
        "example", "Example", "Show or point to an example (a screenshot, 'like the pricing page')."
    ),
    Feature(
        "reference",
        "Concrete reference",
        "Point at something exact: a file, error text, URL, or code.",
    ),
)

ROUTINE_VERBS = frozenset(
    "commit push merge continue proceed run restart retry deploy pull undo revert stop start "
    "redeploy rebuild reload open close approve archive".split()
)


def detect(own_text: str, has_paste: bool = False, has_image: bool = False) -> dict[str, bool]:
    """Which of the 7 rubric features a prompt shows."""
    text = own_text
    terms = kw.content_terms(text)
    style = kw.opening_style(text)
    has_verb = any(t in kw.ACTION_VERBS for t in kw.words(text))
    return {
        "goal": bool(terms) and (has_verb or style in ("request", "question", "command")),
        "context": bool(_CONTEXT_RE.search(text)) or has_paste,
        "constraints": bool(_CONSTRAINT_RE.search(text)),
        "done": bool(_DONE_RE.search(text)),
        "format": bool(_FORMAT_RE.search(text)),
        "example": bool(_EXAMPLE_RE.search(text)) or has_image,
        "reference": has_paste
        or any(
            p.search(text) for p in (_PATH_RE, _URL_RE, _CODE_RE, _MENTION_RE, _IDENT_RE, _ERROR_RE)
        ),
    }


def role(own_text: str) -> str:
    """task · reply · correction · routine · other. Only 'task' prompts get a score."""
    style = kw.opening_style(own_text)
    if style == "reply":
        return "reply"
    if len(own_text.split()) <= 12 and is_correction(own_text):
        return "correction"  # "try again", "no, the other one": they lean on earlier context
    if style == "other":
        return "other"
    toks = kw.words(own_text)
    if len(toks) <= 6 and kw.opening_verb(own_text) in ROUTINE_VERBS:
        return "routine"  # "commit and push", "continue", "start step 2"
    return "task"


def score_text(own_text: str, has_paste: bool = False, has_image: bool = False) -> dict:
    """Score one prompt (also used by `pompy check` and, later, Pre-flight)."""
    features = detect(own_text, has_paste, has_image)
    r = role(own_text)
    return {
        "role": r,
        "score": sum(features.values()) if r == "task" else None,
        "features": features,
        "missing": [f.name for f in FEATURES if not features[f.name]],
    }


# ── Friction: what happened right after a prompt ─────────────────────────────

_CORRECTION_START = re.compile(
    r"^(no|nope|nah|not that|not quite|wrong|wait|stop|actually|hold on|undo|revert|"
    r"that'?s not|thats not|i meant|i mean|you (didn'?t|missed|forgot|broke)|still)\b",
    re.IGNORECASE,
)
_CORRECTION_ANY = re.compile(
    r"\b(didn'?t work|doesn'?t work|does not work|not working|still (not|broken|the same|doesn'?t)|"
    r"same (error|issue|problem)|that'?s not what|not what i (asked|meant|wanted)|"
    r"you (didn'?t|missed|forgot|broke|ignored)|try again|that broke)\b",
    re.IGNORECASE,
)
# Your next prompt reports that something is broken or didn't do what you expected.
# Calibrated on real follow-ups ("why am I getting a 404", "xcode isn't responding").
_PROBLEM_RE = re.compile(
    r"\b(not|isn'?t|isnt|aren'?t|wasn'?t|doesn'?t|doesnt|didn'?t|didnt|won'?t|wont|can'?t|cant) "
    r"(working|work|responding|loading|showing|connected|connecting|updating|saving|rendering|"
    r"see|seeing|appear|appearing|load|show|open|opening|run|running|start|starting|compile)\b"
    r"|\bwhat'?s wrong\b|\bwhats wrong\b|\bwhat is wrong\b"
    r"|\bwhy (am i|is|isn'?t|isnt|are|does|doesn'?t|do|did|didn'?t|can'?t|won'?t) \w+ "
    r"(getting|get|seeing|see|not|broken|failing|showing|empty|blank|missing)\b"
    r"|\b(getting|got|get|seeing|see) (a|an|this|the|that) (\d{3}|error|warning|crash|"
    r"blank|white screen|exception)\b"
    r"|\b(crash(ed|es|ing)?|broke|broken|fail(ed|s|ing)?|hangs?|frozen|stuck)\b"
    r"|\bi thought (we|you|it)\b",
    re.IGNORECASE,
)
REPHRASE_WINDOW_MIN = 10
REPHRASE_SIMILARITY = 0.6


FRICTION_KINDS = ("correction", "problem", "interrupt", "rejected", "rephrase")


def is_correction(own_text: str) -> bool:
    text = own_text.strip()
    return bool(_CORRECTION_START.search(text) or _CORRECTION_ANY.search(text))


def _similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def friction(conn: sqlite3.Connection) -> dict[str, dict]:
    """For every typed prompt: did the next moments show friction?

    Looks between this prompt and your next prompt in the same session:
    - correction: your next prompt pushes back ("no, …", "try again")
    - problem: your next prompt reports something broken ("why am I getting a 404")
    - interrupt / rejected: you stopped Claude or rejected one of its tool calls
    - rephrase: your next prompt is nearly the same request again, within 10 minutes
    """
    conn.row_factory = sqlite3.Row
    prompts = conn.execute(
        "SELECT uuid, session_id, ts, kind, own_text FROM prompts WHERE ts IS NOT NULL "
        "ORDER BY session_id, ts"
    ).fetchall()
    events: dict[str, list] = defaultdict(list)
    for e in conn.execute("SELECT session_id, ts, kind FROM events WHERE ts IS NOT NULL"):
        events[e["session_id"]].append((e["ts"], e["kind"]))

    by_session: dict[str, list] = defaultdict(list)
    for p in prompts:
        by_session[p["session_id"]].append(p)

    result: dict[str, dict] = {}
    for sid, plist in by_session.items():
        for i, p in enumerate(plist):
            if p["kind"] != "typed":
                continue
            nxt = plist[i + 1] if i + 1 < len(plist) else None
            end = nxt["ts"] if nxt else "9999"
            between = [k for ts, k in events[sid] if p["ts"] <= ts < end]
            nxt_typed = bool(nxt and nxt["kind"] == "typed")
            correction = nxt_typed and is_correction(nxt["own_text"])
            problem = nxt_typed and not correction and bool(_PROBLEM_RE.search(nxt["own_text"]))
            rephrase = bool(
                nxt
                and nxt["kind"] == "typed"
                and (_ts(nxt["ts"]) - _ts(p["ts"])).total_seconds() <= REPHRASE_WINDOW_MIN * 60
                and len(set(kw.content_terms(p["own_text"]))) >= 3
                and _similarity(
                    set(kw.content_terms(p["own_text"])), set(kw.content_terms(nxt["own_text"]))
                )
                >= REPHRASE_SIMILARITY
            )
            flags = {
                "correction": correction,
                "problem": problem,
                "interrupt": "interrupt" in between,
                "rejected": "tool_rejected" in between,
                "rephrase": rephrase,
            }
            result[p["uuid"]] = {**flags, "any": any(flags.values())}
    return result


# ── Analysis: does specificity reduce friction for *you*? ───────────────────

LOW_N = 15  # below this many prompts, a rate is shown but flagged as thin evidence


def _rate(items: list[dict]) -> dict:
    n = len(items)
    hits = sum(1 for i in items if i["friction"]["any"])
    return {
        "n": n,
        "friction": hits,
        "rate": round(100 * hits / n, 1) if n else None,
        "low_n": n < LOW_N,
    }


def compute(conn: sqlite3.Connection, tz=None) -> dict:
    from pompy.stats import WORD_BUCKETS, parse_ts

    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT uuid, session_id, ts, own_text, text, has_paste, has_image FROM prompts "
        "WHERE kind = 'typed' AND ts IS NOT NULL ORDER BY ts"
    ).fetchall()
    first_in_session = {
        r["session_id"]: r["uuid"]
        for r in reversed(rows)  # reversed, so the earliest prompt per session wins
    }
    fr = friction(conn)
    none = {k: False for k in FRICTION_KINDS} | {"any": False}

    items = []
    for r in rows:
        s = score_text(r["own_text"], bool(r["has_paste"]), bool(r["has_image"]))
        items.append({**s, "uuid": r["uuid"], "row": r, "friction": fr.get(r["uuid"], none)})
    _save(conn, items)

    tasks = [i for i in items if i["role"] == "task"]
    roles: dict[str, int] = defaultdict(int)
    for i in items:
        roles[i["role"]] += 1
    dist = [0] * (len(FEATURES) + 1)
    for t in tasks:
        dist[t["score"]] += 1
    scores = sorted(t["score"] for t in tasks)

    def bucket(score: int) -> str:
        return "low (0–1)" if score <= 1 else "mid (2–3)" if score <= 3 else "high (4–7)"

    by_bucket: dict[str, list] = {"low (0–1)": [], "mid (2–3)": [], "high (4–7)": []}
    for t in tasks:
        by_bucket[bucket(t["score"])].append(t)

    by_feature = {}
    for f in FEATURES:
        with_f = [t for t in tasks if t["features"][f.name]]
        without = [t for t in tasks if not t["features"][f.name]]
        a, b = _rate(with_f), _rate(without)
        by_feature[f.name] = {
            "label": f.label,
            "present_pct": round(100 * len(with_f) / len(tasks), 1) if tasks else 0,
            "with": a,
            "without": b,
            # positive = prompts WITH this feature had less friction
            "friction_drop": round(b["rate"] - a["rate"], 1)
            if a["rate"] is not None and b["rate"] is not None
            else None,
        }

    by_length: dict[str, list] = {}
    for lo, hi in WORD_BUCKETS:
        label = f"{lo}–{hi}" if hi else f"{lo}+"
        by_length[label] = [
            t
            for t in tasks
            if len(t["row"]["own_text"].split()) >= lo
            and (hi is None or len(t["row"]["own_text"].split()) <= hi)
        ]

    by_month: dict[str, list] = defaultdict(list)
    for t in tasks:
        by_month[parse_ts(t["row"]["ts"], tz).strftime("%Y-%m")].append(t["score"])

    openers = [t for t in tasks if first_in_session.get(t["row"]["session_id"]) == t["uuid"]]
    followups = [t for t in tasks if t not in openers]

    def avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    examples = sorted(
        (t for t in tasks if t["friction"]["any"]), key=lambda t: (t["score"], t["row"]["ts"])
    )[:5]

    return {
        "rubric_version": RUBRIC_VERSION,
        "typed_prompts": len(items),
        "roles": dict(roles),
        "scored_prompts": len(tasks),
        "score": {
            "mean": avg(scores),
            "median": scores[len(scores) // 2] if scores else None,
            "distribution": dist,
            "session_openers_mean": avg([t["score"] for t in openers]),
            "follow_ups_mean": avg([t["score"] for t in followups]),
            "by_month": {m: avg(v) for m, v in sorted(by_month.items())},
        },
        "features": by_feature,
        "friction": {
            "overall": _rate(tasks),
            "all_typed": _rate(items),
            "kinds": {k: sum(1 for t in tasks if t["friction"][k]) for k in FRICTION_KINDS},
            "by_score": {k: _rate(v) for k, v in by_bucket.items()},
            "by_length": {k: _rate(v) for k, v in by_length.items()},
        },
        "examples": [
            {
                "uuid": t["uuid"],
                "session_id": t["row"]["session_id"],
                "score": t["score"],
                "snippet": " ".join(t["row"]["own_text"].split())[:140],
                "missing": t["missing"],
                "friction": [k for k in FRICTION_KINDS if t["friction"][k]],
            }
            for t in examples
        ],
    }


_N_COLUMNS = 5 + len(FEATURES) + len(FRICTION_KINDS) + 2  # ids/role/score, features, friction


def _save(conn: sqlite3.Connection, items: list[dict]) -> None:
    """Keep per-prompt results queryable (Dashboard, Pre-flight, later memory layers)."""
    from pompy.store import PROMPT_SCORES_SCHEMA

    conn.execute("DROP TABLE IF EXISTS prompt_scores")  # derived: recreate with current columns
    conn.executescript(PROMPT_SCORES_SCHEMA)
    conn.executemany(
        f"INSERT INTO prompt_scores VALUES ({','.join('?' * _N_COLUMNS)})",
        [
            (
                i["uuid"],
                i["row"]["session_id"],
                i["row"]["ts"],
                i["role"],
                i["score"],
                *(int(i["features"][f.name]) for f in FEATURES),
                *(int(i["friction"][k]) for k in FRICTION_KINDS),
                int(i["friction"]["any"]),
                RUBRIC_VERSION,
            )
            for i in items
        ],
    )
    conn.commit()


# ── Terminal output ──────────────────────────────────────────────────────────


def _fmt_rate(r: dict) -> str:
    if not r["n"]:
        return "n/a"
    thin = " (thin)" if r["low_n"] else ""
    return f"{r['rate']}% of {r['n']}{thin}"


def render(s: dict) -> str:
    out: list[str] = []
    add = out.append
    sc, fr = s["score"], s["friction"]
    add(f"POMPY · PROMPT SPECIFICITY (rubric v{s['rubric_version']}, all-time)")
    roles = s["roles"]
    add(
        f"{s['typed_prompts']} typed prompts → {s['scored_prompts']} tasks scored · "
        f"{roles.get('reply', 0)} replies · {roles.get('correction', 0)} corrections · "
        f"{roles.get('routine', 0)} routine · "
        f"{roles.get('other', 0)} other (not scored)"
    )
    add("")
    add(f"Score (0–7): mean {sc['mean']} · median {sc['median']}")
    top = max(sc["distribution"]) or 1
    for points, n in enumerate(sc["distribution"]):
        add(f"  {points} {'█' * round(24 * n / top):<24} {n}")
    add(
        f"  first prompt of a session: {sc['session_openers_mean']} · "
        f"follow-ups: {sc['follow_ups_mean']}"
    )
    add("  by month: " + " · ".join(f"{m} {v}" for m, v in sc["by_month"].items()))
    add("")
    add("Which features your task prompts include, and friction with vs without each")
    for f in sorted(s["features"].values(), key=lambda f: f["present_pct"]):
        drop = f["friction_drop"]
        effect = (
            ""
            if drop is None
            else (
                f"  → {drop:+.1f} pts less friction"
                if drop > 0
                else f"  → {abs(drop):.1f} pts more friction"
            )
        )
        add(
            f"  {f['label']:<19} in {f['present_pct']:>5}%   with: {_fmt_rate(f['with']):<18} "
            f"without: {_fmt_rate(f['without'])}{effect}"
        )
    add("")
    add(f"Friction after task prompts: {_fmt_rate(fr['overall'])}")
    add("  " + " · ".join(f"{k} {v}" for k, v in fr["kinds"].items()))
    add("  by score:  " + " · ".join(f"{k} {_fmt_rate(v)}" for k, v in fr["by_score"].items()))
    add("  by length: " + " · ".join(f"{k}w {_fmt_rate(v)}" for k, v in fr["by_length"].items()))
    if s["examples"]:
        add("")
        add("Low-scoring prompts that led to friction")
        for e in s["examples"]:
            add(f'  [{e["score"]}/7] "{e["snippet"][:90]}"')
            add(f"        → {', '.join(e['friction'])} · missing: {', '.join(e['missing'])}")
    return "\n".join(out)


def render_check(text: str, result: dict) -> str:
    out = []
    if result["role"] != "task":
        out.append(
            f"Pompy: this reads as a {result['role']} prompt, so it isn't scored. Send away."
        )
        return "\n".join(out)
    out.append(f"Pompy score: {result['score']}/7")
    for f in FEATURES:
        mark = "✓" if result["features"][f.name] else "·"
        out.append(f"  {mark} {f.label}")
    tips = [f for f in FEATURES if not result["features"][f.name]]
    if tips:
        out.append("Consider adding:")
        for f in tips[:3]:
            out.append(f"  - {f.tip}")
    return "\n".join(out)
