"""`pompy mine`: form hypotheses from your episodes, test them, and keep the ones that hold.

Each candidate claim splits your task prompts into two groups, A (the claim's condition) and
B (the rest), and asks whether A went wrong more often than B:
  - for each rubric part, overall and inside each big theme: A = prompts without that part;
  - late night: A = prompts sent between midnight and 5 am (local time);
  - very short: A = prompts of 6 words or fewer;
  - follow-ups: A = prompts that did not open their chat.

Each claim is tested with a two-proportion z-test. Testing ~70 claims at once means a few
will look significant by chance alone, so every p-value is corrected with Benjamini–Hochberg
into a q-value (the expected share of false findings), and only q < 0.10 counts as supported.

Evidence, both sides: prompts in A that went wrong support the claim; prompts in A that went
fine, and prompts in B that still went wrong, contradict it. The most recent few are cited.

Running it again is consolidation: the same claim (same key) is re-checked, confirmed,
weakened or reversed, and its history grows.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, tzinfo

from pompy import config, patterns, score
from pompy.stats import parse_ts
from pompy.themes import task_prompts

MIN_GROUP = 8  # a claim is tested only if both groups have at least this many prompts
CITE_SUPPORT = 5
CITE_CONTRADICT = 5
THEME_MIN_SIZE = 40


def two_proportion(wrong_a: int, n_a: int, wrong_b: int, n_b: int) -> tuple[float, float]:
    """Effect (rate A - rate B) and its two-sided p-value."""
    ra, rb = wrong_a / n_a, wrong_b / n_b
    pooled = (wrong_a + wrong_b) / (n_a + n_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if se == 0:
        return ra - rb, 1.0
    z = (ra - rb) / se
    return ra - rb, math.erfc(abs(z) / math.sqrt(2))


def benjamini_hochberg(ps: list[float]) -> list[float]:
    """q-values for a list of p-values (step-up, monotone)."""
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, ps[i] * m / rank)
        q[i] = running
    return q


def _rows(conn: sqlite3.Connection, tz: tzinfo | None) -> list[dict]:
    openers = {
        sid: uuid
        for sid, uuid in conn.execute(
            "SELECT session_id, uuid FROM main.prompts WHERE kind = 'typed' "
            "AND ts = (SELECT MIN(ts) FROM main.prompts p2 "
            "WHERE p2.session_id = prompts.session_id AND p2.kind = 'typed')"
        )
    }
    out = []
    for p in task_prompts(conn):
        feats = score.detect(p["prompt"], bool(p["has_paste"]), bool(p["has_image"]))
        out.append(
            {
                **p,
                "feats": feats,
                "wrong": bool(p["friction"]),
                "hour": parse_ts(p["ts"], tz).hour,
                "words": len(p["prompt"].split()),
                "opener": openers.get(p["session_id"]) == p["prompt_uuid"],
            }
        )
    return out


def _themes() -> dict[str, set[str]]:
    path = config.out_dir() / "themes.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {
        " · ".join(t["label"]) or "mixed": set(t["members"])
        for t in data.get("themes", [])
        if t["size"] >= THEME_MIN_SIZE and t["label"]
    }


def candidates(rows: list[dict], themes: dict[str, set[str]]) -> list[dict]:
    """Every claim to test: key, statement, scope, and its A/B split."""
    labels = {f.name: f.label.replace("-", " ").lower() for f in score.FEATURES}
    scopes = [("all", "all", rows)] + [
        (f"theme:{name}", f"theme: {name}", [r for r in rows if r["prompt_uuid"] in members])
        for name, members in themes.items()
    ]
    out = []
    for key_scope, scope, subset in scopes:
        where = "" if key_scope == "all" else f" when you ask about {scope[7:]}"
        for f in score.FEATURES:
            if f.name == "goal":
                continue  # almost every task prompt has one: nothing to compare
            out.append(
                {
                    "key": f"lacks:{f.name}@{key_scope}",
                    "statement": f"Prompts without {labels[f.name]} go wrong more often{where}",
                    "scope": scope,
                    "a": [r for r in subset if not r["feats"][f.name]],
                    "b": [r for r in subset if r["feats"][f.name]],
                }
            )
    out += [
        {
            "key": "late-night@all",
            "statement": "Prompts sent between midnight and 5 am go wrong more often",
            "scope": "all",
            "a": [r for r in rows if r["hour"] < 5],
            "b": [r for r in rows if r["hour"] >= 5],
        },
        {
            "key": "very-short@all",
            "statement": "Very short task prompts (6 words or fewer) go wrong more often",
            "scope": "all",
            "a": [r for r in rows if r["words"] <= 6],
            "b": [r for r in rows if r["words"] > 6],
        },
        {
            "key": "follow-up@all",
            "statement": "Follow-up prompts go wrong more often than the prompt that opens a chat",
            "scope": "all",
            "a": [r for r in rows if not r["opener"]],
            "b": [r for r in rows if r["opener"]],
        },
    ]
    return [c for c in out if len(c["a"]) >= MIN_GROUP and len(c["b"]) >= MIN_GROUP]


def mine(conn: sqlite3.Connection, tz: tzinfo | None = None, now: datetime | None = None) -> dict:
    rows = _rows(conn, tz)
    tests = candidates(rows, _themes())
    for c in tests:
        wa = sum(r["wrong"] for r in c["a"])
        wb = sum(r["wrong"] for r in c["b"])
        c["effect"], c["p"] = two_proportion(wa, len(c["a"]), wb, len(c["b"]))
        c["rate_a"], c["rate_b"] = wa / len(c["a"]), wb / len(c["b"])
    for c, q in zip(tests, benjamini_hochberg([c["p"] for c in tests]), strict=True):
        c["q"] = q
    recent = lambda rs: [r["prompt_uuid"] for r in sorted(rs, key=lambda r: r["ts"], reverse=True)]  # noqa: E731
    results = []
    for c in tests:
        supports = recent([r for r in c["a"] if r["wrong"]])[:CITE_SUPPORT]
        contradicts = (
            recent([r for r in c["a"] if not r["wrong"]])[: CITE_CONTRADICT - 2]
            + recent([r for r in c["b"] if r["wrong"]])[:2]
        )
        stats = {
            "effect": round(c["effect"], 4),
            "rate_a": round(c["rate_a"], 4),
            "rate_b": round(c["rate_b"], 4),
            "n_a": len(c["a"]),
            "n_b": len(c["b"]),
            "p": round(c["p"], 5),
            "q": round(c["q"], 5),
        }
        stored = patterns.record(
            conn,
            key=c["key"],
            statement=c["statement"],
            scope=c["scope"],
            kind="risk",
            source="miner",
            stats=stats,
            supports=supports,
            contradicts=contradicts,
            now=now,
        )
        results.append({"key": c["key"], "statement": c["statement"], **stats, **stored})
    retired = patterns.retire_stale(conn, now)
    results.sort(key=lambda r: r["q"])
    return {
        "task_prompts": len(rows),
        "tested": len(results),
        "supported": sum(r["status"] == "supported" for r in results),
        "contradicted": sum(r["status"] == "contradicted" for r in results),
        "retired": retired,
        "results": results,
    }


def render(result: dict, show: int = 12) -> str:
    out = [
        f"Tested {result['tested']} claims over {result['task_prompts']} task prompts: "
        f"{result['supported']} supported, {result['contradicted']} contradicted, "
        f"the rest unproven (q-values are corrected for testing many claims).",
        "",
    ]
    for r in result["results"][:show]:
        mark = {"supported": "✓", "contradicted": "✕"}.get(r["status"], "?")
        out.append(f" {mark} {r['statement']}")
        out.append(
            f"     {r['rate_a']:.0%} vs {r['rate_b']:.0%} went wrong "
            f"(n = {r['n_a']} vs {r['n_b']}) · p {r['p']:.3f} · q {r['q']:.3f} · "
            f"confidence {r['confidence']:.2f} · {r['status']}"
        )
    if result["retired"]:
        out += ["", "Retired (faded without confirmation): " + ", ".join(result["retired"])]
    return "\n".join(out)
