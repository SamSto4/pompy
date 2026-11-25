"""Closing the loop (Phase 4): log the advice Pompy gives, measure it, learn which tips you use.

After you send a task prompt, the notch runs `pompy similar` and may show a tip: a lesson from
past exchanges like it ("These often broke afterwards. Say how you'll check that it works."),
a pattern Pompy has learned, or a reminder about this project's goal. This module turns those
tips into something Pompy can learn from.

Step 21, the advice log: every tip Pompy could have shown, and the ones it did show, are
appended to `data/advice.jsonl`. Never the prompt text: only a fingerprint of it (a hash),
its score and missing parts, and the tip ids. Later, after `pompy ingest` has stored that
prompt, the fingerprint finds it again.
  Memory concept: episodic memory of the agent's OWN actions. Pompy already remembers what you
  did; now it also remembers what it told you, so it can see what happened next.

Step 22, did it help (`pompy impact`): a tip is shown after a prompt, so it can only change
your NEXT prompt. For each tip, Pompy looks at the next task prompt in the same chat: did it
include the part the tip asked for (you acted on it), and did that exchange go wrong? About 1
prompt in 5 is a holdout: the tips are worked out and logged but not shown. Comparing shown
with held back is a fair test, because the only difference between the two groups is the tip.
  Memory concept: a feedback signal. Memory that never checks its own advice cannot improve.
  The holdout is the counterfactual: what you would have done without the tip.

Step 23, procedural memory (`out/advice.json`): for each kind of tip, how often you act on it
(and how often you would have anyway, from the holdout). Tips you act on are shown first;
tips you keep ignoring go quiet, with an occasional retry so they can earn their place back.
Only the last 90 days count, so a habit that changes is noticed.
  Memory concept: procedural memory, "how to act", learned from outcomes. Episodic memory says
  what happened, semantic memory says what is true, procedural memory says what to do.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pompy import score

LOG_NAME = "advice.jsonl"  # in data/: private, git-ignored
PROFILE_NAME = "advice.json"  # in out/: what the notch reads to pick tips
HOLDOUT_SHARE = 0.2  # about 1 prompt in 5 gets no tip (the fair comparison)
LINK_WINDOW_S = 15 * 60  # a log line and a stored prompt must be this close in time
FOLLOW_WINDOW_S = 30 * 60  # the next task prompt counts only if it comes this soon after
WINDOW_DAYS = 90  # procedural memory reads only recent tips
MIN_TRIES = 8  # a tip is judged only after it has been shown this many times
QUIET_BELOW = 0.15  # acted on less often than this: the tip goes quiet
RETRY_EVERY = 10  # a quiet tip still shows on about 1 prompt in 10

# What each lesson asks you to do next time, as a rubric part (see similar.LESSONS).
LESSON_ASKS = {
    "clarify": "reference",  # "Name the page, file or error up front."
    "correction": "constraints",  # "Say what to keep or avoid."
    "problem": "done",  # "Say how you'll check that it works."
    "interrupt": "constraints",  # "Give the limits up front."
    "rejected": "constraints",  # "Say which files it may touch."
    "rephrase": "score",  # "Be specific the first time.": any rise in score
}


def digest(text: str) -> str:
    """A fingerprint of a prompt: finds it again after ingest without storing its words."""
    return hashlib.sha1(" ".join(text.split()).encode()).hexdigest()[:16]


def asks(tip: str) -> str | None:
    """What acting on a tip looks like in your next prompt: a rubric part, "score" (a higher
    score), "longer" (more than 6 words), or None when it can't be seen in a prompt."""
    kind, _, rest = tip.partition(":")
    if kind == "lesson":
        return LESSON_ASKS.get(rest)
    if kind == "misses":  # "misses:reference": the part similar misses lacked (outcomes.py)
        return rest if rest in {f.name for f in score.FEATURES} else None
    if kind == "project":  # "project:done": this project's usual gap; "project:reminder": none
        return rest if rest in {f.name for f in score.FEATURES} else None
    if kind == "learned":
        if rest.startswith("lacks:") and rest.endswith("@all"):
            return rest[len("lacks:") : -len("@all")]
        if rest == "very-short@all":
            return "longer"
    return None  # late night, or an agent's pattern about a situation


def acted(tip: str, before: dict, after: dict) -> bool | None:
    """Did the next prompt do what the tip asked? None when the tip can't be checked."""
    want = asks(tip)
    if want is None:
        return None
    if want == "score":
        return after["score"] > before["score"]
    if want == "longer":
        return after["words"] > 6
    return bool(after["features"].get(want))


# ── Step 21: deciding what to show, and logging it ────────────────────────────


def holdout_share() -> float:
    try:
        return min(1.0, max(0.0, float(os.environ.get("POMPY_HOLDOUT", HOLDOUT_SHARE))))
    except ValueError:
        return HOLDOUT_SHARE


def held_back(fingerprint: str, share: float | None = None) -> bool:
    """The holdout. Decided by the fingerprint, so the same prompt always gets the same answer."""
    share = holdout_share() if share is None else share
    return int(fingerprint[:8], 16) % 1000 < share * 1000


def _retry(fingerprint: str) -> bool:
    return int(fingerprint[8:12], 16) % RETRY_EVERY == 0


def decide(candidates: list[str], fingerprint: str, profile: dict | None) -> dict:
    """Which of the candidate tips to show, using procedural memory to order and quieten them."""
    learned = (profile or {}).get("tips", {})
    if candidates and held_back(fingerprint):
        return {"shown": [], "quiet": [], "held_back": True}
    quiet = [t for t in candidates if learned.get(t, {}).get("status") == "quiet"]
    if quiet and _retry(fingerprint):
        quiet = []  # the occasional retry: a quiet tip gets another chance
    keep = [t for t in candidates if t not in quiet]
    keep.sort(key=lambda t: -learned.get(t, {}).get("weight", 0.0))  # stable: ties keep order
    return {"shown": keep, "quiet": quiet, "held_back": False}


def log(data_dir: Path, row: dict) -> None:
    """Append one line to the advice log. Never raises: logging must not break the notch."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with (data_dir / LOG_NAME).open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def read_log(data_dir: Path) -> list[dict]:
    path = data_dir / LOG_NAME
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a half-written line from a crash: skip it
    return rows


def load_profile(out_dir: Path) -> dict | None:
    try:
        return json.loads((out_dir / PROFILE_NAME).read_text())
    except (OSError, ValueError):
        return None


# ── Step 22: linking tips to what happened next ───────────────────────────────


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _prompts(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Every scored typed prompt, by uuid, and each chat's task prompts in time order."""
    conn.row_factory = sqlite3.Row
    names = [f.name for f in score.FEATURES]
    rows = conn.execute(
        "SELECT p.uuid, p.session_id, p.ts, p.text, p.own_text, s.role, s.score, s.friction, "
        + ", ".join(f"s.f_{n}" for n in names)
        + " FROM prompts p JOIN prompt_scores s ON s.uuid = p.uuid "
        "WHERE p.kind = 'typed' AND p.ts IS NOT NULL ORDER BY p.ts"
    ).fetchall()
    by_uuid, tasks = {}, defaultdict(list)
    for r in rows:
        p = {
            "uuid": r["uuid"],
            "session": r["session_id"],
            "at": _epoch(r["ts"]),
            "role": r["role"],
            "score": r["score"] or 0,
            "wrong": bool(r["friction"]),
            "words": len((r["own_text"] or "").split()),
            "features": {n: bool(r[f"f_{n}"]) for n in names},
            "digests": {digest(r["text"] or ""), digest(r["own_text"] or "")},
        }
        by_uuid[p["uuid"]] = p
        if p["role"] == "task":
            tasks[p["session"]].append(p)
    return by_uuid, tasks


def link(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Each log line joined to the prompt it was about and the next task prompt in that chat.

    Lines that match no stored prompt (a test run in the terminal, a chat not ingested yet) are
    left out. A prompt logged twice keeps its first line.
    """
    by_uuid, tasks = _prompts(conn)
    by_digest = defaultdict(list)
    for p in by_uuid.values():
        for d in p["digests"]:
            by_digest[d].append(p)
    out, seen = [], set()
    for row in sorted(rows, key=lambda r: r.get("at", 0)):
        near = [p for p in by_digest.get(row.get("digest"), [])
                if abs(p["at"] - row["at"]) <= LINK_WINDOW_S]  # fmt: skip
        if not near:
            continue
        prompt = min(near, key=lambda p: abs(p["at"] - row["at"]))
        if prompt["uuid"] in seen:
            continue
        seen.add(prompt["uuid"])
        nxt = next(
            (q for q in tasks.get(prompt["session"], [])
             if prompt["at"] < q["at"] <= prompt["at"] + FOLLOW_WINDOW_S),
            None,
        )  # fmt: skip
        out.append({**row, "prompt": prompt, "next": nxt})
    return out


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 3) if n else None


def impact(conn: sqlite3.Connection, rows: list[dict], now: float | None = None) -> dict:
    """Did the advice help? Shown vs held back, per tip and overall, plus the trend over time."""
    from pompy.mine import two_proportion

    now = time.time() if now is None else now
    linked = link(conn, rows)
    counters = ("shown", "shown_acted", "held", "held_acted")
    per_tip: dict[str, dict] = defaultdict(
        lambda: {**{k: 0 for k in counters}, **{f"recent_{k}": 0 for k in counters},
                 "checkable": True}
    )  # fmt: skip
    groups = {"shown": [], "held": []}  # next prompts after a tip vs after a held-back tip
    since = now - WINDOW_DAYS * 86400
    for row in linked:
        tips = row.get("tips") or []
        if not tips or row["next"] is None:
            continue
        shown = set(row.get("shown") or [])
        group = "held" if row.get("held_back") else "shown" if shown else None
        if group:
            groups[group].append(row["next"])
        for tip in tips:
            did = acted(tip, row["prompt"], row["next"])
            t = per_tip[tip]
            if did is None:
                t["checkable"] = False
            side = "shown" if tip in shown else "held" if row.get("held_back") else None
            if side is None:
                continue  # quietened by procedural memory: neither shown nor a fair baseline
            t[side] += 1
            t[f"{side}_acted"] += bool(did)
            if row["at"] >= since:
                t[f"recent_{side}"] += 1
                t[f"recent_{side}_acted"] += bool(did)

    def side(items):
        n = len(items)
        mean = round(sum(p["score"] for p in items) / n, 2) if n else None
        return {"n": n, "went_wrong": _rate(sum(p["wrong"] for p in items), n), "mean_score": mean}

    overall = {"shown": side(groups["shown"]), "held": side(groups["held"])}
    a, b = groups["shown"], groups["held"]
    if a and b:
        effect, p = two_proportion(sum(x["wrong"] for x in a), len(a),
                                   sum(x["wrong"] for x in b), len(b))  # fmt: skip
        overall["effect"], overall["p"] = round(effect, 3), round(p, 3)
    tips = {}
    for tip, t in sorted(per_tip.items()):
        tips[tip] = {
            **{k: v for k, v in t.items() if not k.startswith("recent")},
            "asks": asks(tip),
            "acted_rate": _rate(t["shown_acted"], t["shown"]) if t["checkable"] else None,
            "baseline_rate": _rate(t["held_acted"], t["held"]) if t["checkable"] else None,
            **procedure(t),
        }
    return {
        "logged": len(rows),
        "linked": len(linked),
        "with_follow_up": sum(1 for r in linked if r["next"] is not None),
        "held_back": sum(1 for r in linked if r.get("held_back")),
        "overall": overall,
        "tips": tips,
        "trend": trend(conn, rows),
        "window_days": WINDOW_DAYS,
    }


# ── Step 23: procedural memory ────────────────────────────────────────────────


def procedure(t: dict) -> dict:
    """What Pompy should do with one tip, from the last 90 days of showing it.

    weight: how much more often you do what it asks when you see it than when you don't (the
    holdout), or, until the holdout has enough cases, how often you do it at all, smoothed
    toward 50% while there are few cases. A tip that can't be checked keeps a neutral weight.
    """
    if not t["checkable"]:
        return {"status": "unmeasured", "weight": 0.0}
    n, hits = t["recent_shown"], t["recent_shown_acted"]
    rate = (hits + 1) / (n + 2)  # Laplace: 50% with no evidence, moves as cases arrive
    hn = t["recent_held"]
    base = (t["recent_held_acted"] + 1) / (hn + 2) if hn >= MIN_TRIES else None
    weight = rate - base if base is not None else rate - 0.5
    if n < MIN_TRIES:
        status = "learning"
    elif hits / n < QUIET_BELOW or (base is not None and rate <= base):
        status = "quiet"  # you rarely act on it, or no more than you would anyway
    else:
        status = "works"
    return {"status": status, "weight": round(weight, 3)}


def profile(result: dict, now: float | None = None) -> dict:
    """The compiled procedural memory the notch reads: one small file, no database."""
    now = time.time() if now is None else now
    return {
        "updated": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "tips": {
            k: {"status": v["status"], "weight": v["weight"]} for k, v in result["tips"].items()
        },  # fmt: skip
    }


def write_profile(prof: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / PROFILE_NAME
    path.write_text(json.dumps(prof, indent=1))
    return path


# ── The trend: before advice started vs since ─────────────────────────────────


def trend(conn: sqlite3.Connection, rows: list[dict], days: int = 30) -> dict | None:
    """Task prompts in the `days` before the first logged tip vs everything since.

    Not a fair test (other things change over time too), so it is reported next to the
    holdout comparison, never instead of it.
    """
    if not rows:
        return None
    start = min(r["at"] for r in rows) - 60  # the notch logs a few seconds after you send
    by_uuid, _ = _prompts(conn)
    tasks = [p for p in by_uuid.values() if p["role"] == "task"]
    before = [p for p in tasks if start - days * 86400 <= p["at"] < start]
    after = [p for p in tasks if p["at"] >= start]

    def stats(items):
        n = len(items)
        return {"n": n,
                "mean_score": round(sum(p["score"] for p in items) / n, 2) if n else None,
                "went_wrong": _rate(sum(p["wrong"] for p in items), n)}  # fmt: skip

    started = datetime.fromtimestamp(start).astimezone().date().isoformat()
    return {"started": started, "days_before": days, "before": stats(before),
            "since": stats(after)}  # fmt: skip


# ── Report ────────────────────────────────────────────────────────────────────


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def render(r: dict) -> str:
    if not r["logged"]:
        return ("No advice logged yet. The notch logs each tip it shows from now on; "
                "come back after a few days of work.")  # fmt: skip
    out = [
        f"Advice log: {r['logged']} lines, {r['linked']} matched to prompts Pompy has stored, "
        f"{r['with_follow_up']} with a next prompt in the same chat, "
        f"{r['held_back']} held back (no tip shown, for a fair comparison).",
        "",
        "Did the next exchange go wrong less often after a tip?",
    ]
    o = r["overall"]
    for name, label in (("shown", "after a tip"), ("held", "tip held back")):
        s = o[name]
        mean = "n/a" if s["mean_score"] is None else s["mean_score"]
        out.append(f"  {label:<15} {s['n']:>4} next prompts · went wrong {_pct(s['went_wrong'])}"
                   f" · mean score {mean}")  # fmt: skip
    if "effect" in o:
        verdict = "a real difference" if o["p"] < 0.05 else "could be chance so far"
        out.append(f"  difference {o['effect'] * 100:+.0f} points (p {o['p']}): {verdict}")
    else:
        out.append("  not enough on both sides to compare yet")
    out += ["", "Tips (procedural memory, last 90 days):"]
    if not r["tips"]:
        out.append("  none with a follow-up prompt yet")
    for tip, t in sorted(r["tips"].items(), key=lambda kv: -kv[1]["weight"]):
        if t["status"] == "unmeasured":
            out.append(f"  {tip:<28} shown {t['shown']:>3} · can't be checked in a prompt")
            continue
        base = f" · without it {_pct(t['baseline_rate'])}" if t["held"] else ""
        out.append(f"  {tip:<28} shown {t['shown']:>3} · acted on {_pct(t['acted_rate'])}"
                   f"{base} · {t['status']}")  # fmt: skip
    tr = r["trend"]
    if tr:
        b, s = tr["before"], tr["since"]
        b_mean = "n/a" if b["mean_score"] is None else b["mean_score"]
        s_mean = "n/a" if s["mean_score"] is None else s["mean_score"]
        out += [
            "",
            f"Trend since advice started ({tr['started']}; not a fair test, other things change):",
            f"  {tr['days_before']} days before: {b['n']} task prompts · mean score "
            f"{b_mean} · went wrong {_pct(b['went_wrong'])}",
            f"  since:          {s['n']} task prompts · mean score {s_mean} · "
            f"went wrong {_pct(s['went_wrong'])}",
        ]
    return "\n".join(out)
