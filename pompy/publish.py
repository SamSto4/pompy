"""`pompy publish`: build the Pompy Dashboard page and its data bundle in out/dashboard/.

This only builds files on your Mac. Putting them on your private artifact is a separate step
(Claude publishes the folder for you), so nothing leaves the machine until you ask.

The bundle is the dashboard's long-term memory snapshot: all-time results plus one row per
prompt, with every per-prompt fact (role, score, friction, local date) decided here in Python.
The page only aggregates those rows and scores new drafts with the same rules (rules are
exported from Python, so there is one source of truth; tests check the browser agrees).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pompy import __version__, compare, config, keywords, score, stats
from pompy.working_set import WorkingSet, before, parse_period

TEMPLATE_DIR = Path(__file__).parent / "dashboard"
TEXT_LIMIT = 600  # characters of each prompt shown in the dashboard
QUESTION_LIMIT = 200  # characters of Claude's clarifying question, for prompts flagged "clarify"

# ── Redaction: a last guard before text leaves the database ─────────────────
# Only your own words are bundled (pasted blocks are already excluded), but people sometimes
# type a key or an address straight into a prompt.
_REDACTIONS = [
    (re.compile(r"\b(sk|pk|rk|ghp|gho|xox[abp]|AKIA)[-_A-Za-z0-9]{12,}\b"), "[key]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|auth)\b(\s*[:=]\s*)['\"]?[^\s'\"]{8,}"
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1[redacted]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"/(Users|home)/[^/\s\"']+"), "~"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "[id]"),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── Rules for the in-page scorer (one source of truth: the Python rules) ────


def _rx(p: re.Pattern) -> dict:
    flags = ("i" if p.flags & re.IGNORECASE else "") + ("s" if p.flags & re.DOTALL else "")
    return {"source": p.pattern, "flags": flags}


def export_rules() -> dict:
    """Everything the browser needs to score a draft exactly like `pompy check`."""
    s, k = score, keywords
    return {
        "rubric_version": score.RUBRIC_VERSION,
        "features": [{"name": f.name, "label": f.label, "tip": f.tip} for f in score.FEATURES],
        "regex": {
            "context": _rx(s._CONTEXT_RE),
            "constraints": _rx(s._CONSTRAINT_RE),
            "done": _rx(s._DONE_RE),
            "format": _rx(s._FORMAT_RE),
            "example": _rx(s._EXAMPLE_RE),
            "error": _rx(s._ERROR_RE),
            "ref_mention": _rx(s._MENTION_RE),
            "ref_path": _rx(s._PATH_RE),
            "ref_url": _rx(s._URL_RE),
            "ref_code": _rx(s._CODE_RE),
            "ref_ident": _rx(s._IDENT_RE),
            "correction_start": _rx(s._CORRECTION_START),
            "correction_any": _rx(s._CORRECTION_ANY),
            # tokenizer (keywords.clean_for_words), applied in this order
            "dotted_acronym": _rx(k._DOTTED_ACRONYM_RE),
            "clean": [
                _rx(p)
                for p in (
                    k._CODE_FENCE_RE,
                    k._INLINE_CODE_RE,
                    k._URL_RE,
                    k._MENTION_RE,
                    k._PATH_RE,
                    k._JSON_KEY_RE,
                )
            ],
            "identifier": _rx(k._IDENTIFIER_RE),
            "word": _rx(k._WORD_RE),
        },
        "words": {
            "stopwords": sorted(k.STOPWORDS),
            "action_verbs": sorted(k.ACTION_VERBS),
            "opening_filler": sorted(k.OPENING_FILLER),
            "shell_words": sorted(k.SHELL_WORDS),
            "request_openers": list(k.REQUEST_OPENERS),
            "question_openers": sorted(k.QUESTION_OPENERS),
            "reply_words": sorted(k.REPLY_WORDS),
            "short_tokens": sorted(k.SHORT_TOKENS),
            "routine_verbs": sorted(score.ROUTINE_VERBS),
        },
    }


# ── Habits: the top 3 things to work on ──────────────────────────────────────


def habits(sc: dict, rows: list[dict], cmp_rows: dict, top: int = 3) -> list[dict]:
    """Rank rubric features by how rarely your task prompts include them.

    Rarity is the ranking signal because it is solid (hundreds of prompts). The friction
    difference is shown as evidence but doesn't rank: those rates rest on few prompts.
    """
    tasks = [r for r in rows if r["r"] == "task"]
    ranked = sorted(
        (f for f in score.FEATURES if f.name != "goal"),
        key=lambda f: sc["features"][f.name]["present_pct"],
    )
    out = []
    for f in ranked[:top]:
        stat = sc["features"][f.name]
        missing = [r for r in tasks if f.name not in r["f"] and 6 <= r["w"] <= 60]
        # A real example: prefer one that led to friction, then the most recent.
        missing.sort(key=lambda r: (bool(r["fr"]), r["d"], r["hh"]), reverse=True)
        trend = cmp_rows.get(f"has_{f.name}")
        out.append(
            {
                "feature": f.name,
                "label": f.label,
                "tip": f.tip,
                "present_pct": stat["present_pct"],
                "with": stat["with"],
                "without": stat["without"],
                "friction_drop": stat["friction_drop"],
                "this_month": trend["a"] if trend else None,
                "before": trend["b"] if trend else None,
                "examples": [r["u"] for r in missing[:3]],
            }
        )
    return out


# ── The bundle ───────────────────────────────────────────────────────────────


NEIGHBOURS = 3  # similar prompts per prompt, by meaning, precomputed for the page


def _neighbours(conn: sqlite3.Connection, uuids: set[str]) -> dict[str, list[str]]:
    """For each prompt, the prompts closest in meaning (from other chats when possible).

    The page can't run the embedding model, so this is precomputed at publish time from the
    meaning index (the model with the most vectors). Empty without embeddings or NumPy.
    """
    try:
        import numpy as np
    except ImportError:
        return {}
    try:
        row = conn.execute(
            "SELECT model FROM embeddings WHERE model NOT LIKE '%#%' "
            "GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return {}
    if not row:
        return {}
    vecs = conn.execute(
        "SELECT m.prompt_uuid, e.session_id, m.vector FROM embeddings m "
        "JOIN excerpts e ON e.prompt_uuid = m.prompt_uuid AND e.text_hash = m.text_hash "
        "WHERE m.model = ?",
        (row[0],),
    ).fetchall()
    vecs = [v for v in vecs if v[0] in uuids]
    if len(vecs) < 2:
        return {}
    ids = [v[0] for v in vecs]
    chats = [v[1] for v in vecs]
    matrix = np.stack([np.frombuffer(v[2], dtype=np.float32) for v in vecs])
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -1.0)
    out = {}
    for i, uuid in enumerate(ids):
        order = np.argsort(-sims[i])
        other_chat = [j for j in order if chats[j] != chats[i]]
        same_chat = [j for j in order if chats[j] == chats[i] and j != i]
        out[uuid] = [ids[j] for j in (other_chat + same_chat)[:NEIGHBOURS]]
    return out


def _prompt_rows(conn: sqlite3.Connection, tz) -> list[dict]:
    conn.row_factory = sqlite3.Row
    scores = {r["uuid"]: r for r in conn.execute("SELECT * FROM prompt_scores")}
    fr_all = score.friction(conn)
    openers = {
        sid: uuid
        for sid, uuid in reversed(
            conn.execute(
                "SELECT session_id, uuid FROM prompts WHERE kind = 'typed' AND ts IS NOT NULL "
                "ORDER BY ts"
            ).fetchall()
        )
    }
    questions = _clarifying_questions(conn)
    session_no: dict[str, int] = {}  # a small number per chat, instead of its real id
    rows = []
    for p in conn.execute(
        "SELECT p.uuid, p.session_id, p.ts, p.kind, p.own_text, p.word_len, p.has_paste, "
        "p.has_image, s.project_name FROM prompts p JOIN sessions s USING (session_id) "
        "WHERE p.ts IS NOT NULL ORDER BY p.ts"
    ):
        local = stats.parse_ts(p["ts"], tz)
        sc = scores.get(p["uuid"])
        fr = fr_all.get(p["uuid"], {})
        row = {
            "u": p["uuid"],
            "d": local.strftime("%Y-%m-%d"),
            "hh": local.strftime("%H:%M"),
            "h": local.hour,
            "wd": local.weekday(),
            "p": p["project_name"] or "unknown",
            "si": session_no.setdefault(p["session_id"], len(session_no) + 1),
            "k": p["kind"],
            "t": _clip(redact(p["own_text"]), TEXT_LIMIT),
            "w": p["word_len"] or 0,
            "pa": bool(p["has_paste"]),
            "im": bool(p["has_image"]),
            "r": sc["role"] if sc else None,
            "s": sc["score"] if sc else None,
            "f": [f.name for f in score.FEATURES if sc and sc[f"f_{f.name}"]],
            "fr": [k for k in score.FRICTION_KINDS if fr.get(k)],
            "o": openers.get(p["session_id"]) == p["uuid"],
        }
        if "clarify" in row["fr"] and p["uuid"] in questions:
            row["q"] = _clip(redact(questions[p["uuid"]]), QUESTION_LIMIT)
        rows.append(row)
    return rows


def _clarifying_questions(conn: sqlite3.Connection) -> dict[str, str]:
    """For each typed prompt, the question Claude's last reply ended on (if any)."""
    replies: dict[str, list] = {}
    for r in conn.execute("SELECT session_id, ts, text FROM replies ORDER BY session_id, ts"):
        replies.setdefault(r[0], []).append((r[1], r[2]))
    prompts = conn.execute(
        "SELECT uuid, session_id, ts FROM prompts WHERE ts IS NOT NULL ORDER BY session_id, ts"
    ).fetchall()
    out = {}
    for i, (uuid, sid, ts) in enumerate(prompts):
        nxt = prompts[i + 1] if i + 1 < len(prompts) and prompts[i + 1][1] == sid else None
        end = nxt[2] if nxt else "9999"
        said = [t for rts, t in replies.get(sid, []) if ts <= rts < end]
        q = score.final_question(said[-1]) if said else None
        if q:
            out[uuid] = q
    return out


EVIDENCE_SHOWN = 4  # excerpts per side of each learned pattern
EVIDENCE_CHARS = 180


def learned(conn: sqlite3.Connection, tz=None, now: datetime | None = None) -> dict:
    """Long-term memory for the dashboard: each pattern, its confidence and evidence, its history.

    Retired patterns are counted, not shown: they are what Pompy has let go of.
    """
    from pompy import patterns

    try:
        held = patterns.load(conn, now=now)
    except sqlite3.Error:
        return {"patterns": [], "retired": 0}

    def ev(e: dict) -> dict:
        return {
            "t": _clip(redact(e["prompt"]), EVIDENCE_CHARS),
            "o": _clip(redact(e["outcome"] or ""), 80),
            "d": stats.parse_ts(e["ts"], tz).strftime("%Y-%m-%d"),
        }

    out = []
    for p in held:
        if p["status"] == "retired":
            continue
        st = p["stats"]
        tested = p["key"].startswith(("lacks:", "late-night", "very-short", "follow-up"))
        out.append(
            {
                "key": p["key"],
                "s": redact(p["statement"]),
                "scope": redact(p["scope"]),
                "kind": p["kind"],
                "source": "facts" if p["kind"] in ("goal", "preference") else p["source"],
                "status": p["status"],
                "c": round(p["confidence_now"], 3),
                "tested": tested,
                "rates": [round(st["rate_a"], 3), round(st["rate_b"], 3)] if tested else None,
                "n": [st["n_a"], st["n_b"]] if tested else None,
                "q": round(st["q"], 3) if tested else None,
                "for": [ev(e) for e in p["supports"][:EVIDENCE_SHOWN]],
                "against": [ev(e) for e in p["contradicts"][:EVIDENCE_SHOWN]],
                "n_for": len(p["supports"]),
                "n_against": len(p["contradicts"]),
                "since": p["first_seen"][:10],
                "last_confirmed": (p["last_confirmed"] or "")[:10] or None,
                "history": [
                    [x["at"][:10], round(x["confidence"], 3), x["status"]] for x in p["history"]
                ],  # fmt: skip
            }
        )
    retired = sum(1 for p in held if p["status"] == "retired")
    return {
        "patterns": out,
        "retired": retired,
        "half_life_days": patterns.HALF_LIFE_DAYS,
        "retire_below": patterns.RETIRE_BELOW,
    }


def build_bundle(conn: sqlite3.Connection, tz=None, now: datetime | None = None) -> dict:
    ws = WorkingSet(tz=tz)
    st = stats.compute(conn, ws)
    kws = keywords.compute(conn, ws)
    sc = score.compute(conn, ws)  # all-time: also refreshes prompt_scores
    rows = _prompt_rows(conn, tz)
    # Among task prompts only: a bare "yes" or "commit" is no useful neighbour.
    neighbours = _neighbours(conn, {r["u"] for r in rows if r["r"] == "task"})
    for r in rows:
        if r["u"] in neighbours:
            r["nb"] = neighbours[r["u"]]

    month = WorkingSet(window=parse_period("this-month", tz, now=now), tz=tz)
    try:
        prior = WorkingSet(window=before(month.window), tz=tz)
        cmp = compare.compute(conn, month, prior)
    except ValueError:
        cmp = None
    cmp_rows = {r["key"]: r for r in cmp["rows"]} if cmp else {}

    themes = None
    th_file = config.out_dir() / "themes.json"
    if th_file.exists():
        th = json.loads(th_file.read_text())
        themes = [
            {
                "label": t["label"],
                "size": t["size"],
                "share_pct": t["share_pct"],
                "went_wrong_pct": t["went_wrong_pct"],
                "mean_score": t["mean_score"],
                "top_friction": t["top_friction"],
                "example": _clip(redact(t["examples"][0]["prompt"]), 160) if t["examples"] else "",
            }
            for t in th.get("themes", [])
        ]

    working_set = None
    ws_file = config.out_dir() / "working_set.json"
    if ws_file.exists():
        working_set = json.loads(ws_file.read_text())
        for s in working_set.get("snippets", []):
            for k in ("text", "reply", "outcome"):
                if s.get(k):
                    s[k] = redact(s[k])
            s.pop("session_id", None)

    tz_name = st["timezone"]
    return {
        "meta": {
            "pompy_version": __version__,
            "generated_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "today": stats.parse_ts(
                (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), tz
            ).strftime("%Y-%m-%d"),
            "timezone": tz_name,
            "rubric_version": score.RUBRIC_VERSION,
            "prompts": len(rows),
            "first_day": rows[0]["d"] if rows else None,
            "last_day": rows[-1]["d"] if rows else None,
        },
        "stats": {
            "totals": st["totals"],
            "models": {k: st["models"][k] for k in ("share", "by_messages", "by_month")},
            "effort": st["effort"],
            "time": st["time"],
            "sessions": st["sessions"],
            "prompt_length": st["prompt_length"],
            "projects": st["projects"],
            "surfaces": st["surfaces"],
        },
        "keywords": {
            k: kws[k]
            for k in ("top_terms", "top_phrases", "action_verbs", "opening_style", "style_markers")
            if k in kws
        }
        | {"by_month": kws.get("by_month")},
        "score": {k: sc[k] for k in ("roles", "score", "features", "friction", "scored_prompts")},
        "habits": habits(sc, rows, cmp_rows),
        "compare": cmp,
        "working_set": working_set,
        "rules": export_rules(),
        "prompts": rows,
        "themes": themes,
        "learned": learned(conn, tz, now),
    }


def build(conn: sqlite3.Connection, out: Path, tz=None) -> dict:
    """Write out/dashboard/: the page, its scorer and the data bundle."""
    out.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle(conn, tz)
    for src in sorted(TEMPLATE_DIR.iterdir()):
        if src.suffix in (".html", ".js"):
            shutil.copyfile(src, out / src.name)
    (out / "data.json").write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")))
    return bundle
