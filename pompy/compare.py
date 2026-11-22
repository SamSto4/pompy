"""`pompy compare`: two working sets side by side, to see whether you're improving.

Each side is a WorkingSet with its own window (e.g. "this-month" vs everything before it).
The same insight functions run on both; only the window differs.
"""

from __future__ import annotations

import sqlite3

from pompy import score, stats
from pompy.score import FEATURES, LOW_N
from pompy.working_set import WorkingSet

# (key, label, higher_is_better). None means neither direction is "better".
METRICS = [
    ("typed", "Typed prompts", None),
    ("median_words", "Median words you wrote", None),
    ("score_mean", "Specificity, mean (0–7)", True),
    ("openers_mean", "  session openers", True),
    ("follow_ups_mean", "  follow-ups", True),
    *((f"has_{f.name}", f"  with {f.label.lower()} (%)", True) for f in FEATURES),
    ("friction_rate", "Friction after tasks (%)", False),
    ("clarify_rate", "  Claude had to ask (%)", False),
    ("top_model", "Top model", None),
    ("peak_hour", "Peak hour", None),
]


def headline(conn: sqlite3.Connection, ws: WorkingSet) -> dict:
    """The few numbers worth comparing between periods."""
    st = stats.compute(conn, ws)
    sc = score.compute(conn, ws)
    tasks = sc.get("scored_prompts", 0)
    fr = sc.get("friction", {})
    share = st["models"]["share"]
    top = max(share, key=share.get) if share else None
    return {
        "window": ws.window.label,
        "n_typed": sc.get("typed_prompts", 0),
        "n_tasks": tasks,
        "typed": sc.get("typed_prompts", 0),
        "median_words": st["prompt_length"]["words"].get("median"),
        "score_mean": sc.get("score", {}).get("mean"),
        "openers_mean": sc.get("score", {}).get("session_openers_mean"),
        "follow_ups_mean": sc.get("score", {}).get("follow_ups_mean"),
        **{
            f"has_{f.name}": sc.get("features", {}).get(f.name, {}).get("present_pct")
            for f in FEATURES
        },
        "friction_rate": fr.get("overall", {}).get("rate"),
        "clarify_rate": round(100 * fr["kinds"]["clarify"] / tasks, 1) if tasks else None,
        "top_model": f"{top.removeprefix('claude-')} {share[top]:g}%" if top else None,
        "peak_hour": st["time"].get("peak_hour") if st["totals"]["prompts"] else None,
    }


def compute(conn: sqlite3.Connection, a: WorkingSet, b: WorkingSet) -> dict:
    left, right = headline(conn, a), headline(conn, b)
    rows = []
    for key, label, better in METRICS:
        x, y = left.get(key), right.get(key)
        numbers = isinstance(x, int | float) and isinstance(y, int | float)
        delta = round(x - y, 2) if numbers else None
        verdict = None
        if delta and better is not None:
            verdict = "better" if (delta > 0) == better else "worse"
        rows.append(
            {"key": key, "label": label, "a": x, "b": y, "delta": delta, "verdict": verdict}
        )
    return {"a": _side(left), "b": _side(right), "rows": rows}


def _side(h: dict) -> dict:
    return {"window": h["window"], "tasks": h["n_tasks"], "thin": h["n_tasks"] < LOW_N}


def _fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def render(c: dict) -> str:
    a, b = c["a"], c["b"]
    head_a, head_b = a["window"][:22], b["window"][:22]
    out = [f"{'':30} {head_a:>22} {head_b:>22}   change"]
    for r in c["rows"]:
        arrow = {"better": "▲ better", "worse": "▼ worse"}.get(r["verdict"], "")
        change = f"{r['delta']:+g} {arrow}".rstrip() if r["delta"] else ""
        out.append(f"{r['label']:30} {_fmt(r['a']):>22} {_fmt(r['b']):>22}   {change}")
    for side in (a, b):
        if side["thin"]:
            out.append(
                f"\n⚠ {side['window']}: only {side['tasks']} task prompts (under {LOW_N}), "
                "so its rates are thin evidence."
            )
    return "\n".join(out)
