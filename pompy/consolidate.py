"""`pompy consolidate`: keep long-term memory true as new episodes arrive.

One pass:
  1. re-check every statistical pattern against all episodes (`pompy mine`);
  2. refresh project facts with the small model, when asked and an API key is available;
  3. retire agent hypotheses no later run re-checked within 180 days;
  4. fade every supported pattern and fact by time since it was last confirmed, and retire
     the ones that fell below the floor;
then report what is new, what strengthened, what weakened and what retired.

Memory concept: consolidation and forgetting. Long-term memory that only grows becomes
wrong; facts have to be re-earned or let go.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, tzinfo

from pompy import mine, patterns

AGENT_HYPOTHESIS_DAYS = 180
CHANGE = 0.05  # a confidence move smaller than this is not reported


def snapshot(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    return {
        p["key"]: (p["status"], p["confidence_now"], p["statement"])
        for p in patterns.load(conn, now=now)
    }


def consolidate(
    conn: sqlite3.Connection,
    tz: tzinfo | None = None,
    facts_client=None,
    facts_model: str | None = None,
    now: datetime | None = None,
) -> dict:
    before = snapshot(conn, now)
    mined = mine.mine(conn, tz, now)
    facts_result = None
    if facts_client is not None:
        from pompy import facts

        facts_result = facts.refresh(conn, facts_client, facts_model or facts.DEFAULT_MODEL, now)
    patterns.retire_unconfirmed_hypotheses(conn, AGENT_HYPOTHESIS_DAYS, now)
    patterns.retire_stale(conn, now)
    after = snapshot(conn, now)

    changes = {"new": [], "strengthened": [], "weakened": [], "retired": []}
    for key, (status, conf, statement) in after.items():
        if key not in before:
            changes["new"].append({"key": key, "statement": statement, "status": status})
            continue
        old_status, old_conf, _ = before[key]
        if status == "retired" and old_status != "retired":
            changes["retired"].append({"key": key, "statement": statement, "was": old_status})
        elif status != "retired" and conf - old_conf >= CHANGE:
            changes["strengthened"].append(
                {"key": key, "statement": statement, "from": old_conf, "to": conf}
            )
        elif status != "retired" and old_conf - conf >= CHANGE:
            changes["weakened"].append(
                {"key": key, "statement": statement, "from": old_conf, "to": conf}
            )
    counts = {s: sum(1 for v in after.values() if v[0] == s)
              for s in ("supported", "hypothesis", "contradicted", "retired")}  # fmt: skip
    return {
        "changes": changes,
        "counts": counts,
        "mined": {k: mined[k] for k in ("tested", "supported", "contradicted")},
        "facts": facts_result,
    }


def render(result: dict) -> str:
    c = result["counts"]
    out = [
        f"Long-term memory: {c['supported']} supported, {c['hypothesis']} hypotheses, "
        f"{c['contradicted']} contradicted, {c['retired']} retired.",
        f"Re-checked {result['mined']['tested']} statistical claims "
        f"({result['mined']['supported']} hold).",
    ]
    if result["facts"]:
        u = result["facts"]["usage"]
        out.append(
            f"Project facts refreshed for {len(result['facts']['projects'])} projects "
            f"({result['facts']['model']}, {u['input']} tokens in, {u['output']} out)."
        )
    labels = {"new": "New", "strengthened": "Strengthened", "weakened": "Weakened",
              "retired": "Retired"}  # fmt: skip
    for kind, items in result["changes"].items():
        if not items:
            continue
        out += ["", f"{labels[kind]} ({len(items)}):"]
        for it in items[:12]:
            move = f"  {it['from']:.2f} → {it['to']:.2f}" if "to" in it else ""
            out.append(f"  · {it['statement'][:110]}{move}")
    if not any(result["changes"].values()):
        out += ["", "Nothing changed since the last pass."]
    return "\n".join(out)
