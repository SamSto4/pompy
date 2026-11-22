"""Step 4: usage analytics. The first pattern family: *how and when* you use Claude.

Everything is computed from the local store, all-time by default. Timestamps are stored
in UTC and converted to your local time zone here, because "you work late at night" is
only true in your time zone.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, tzinfo

from pompy.working_set import WorkingSet

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WORD_BUCKETS = [(1, 5), (6, 20), (21, 50), (51, 150), (151, None)]
IDLE_GAP_MINUTES = 30  # a pause longer than this ends an "active stretch" of a session


def parse_ts(ts: str, tz: tzinfo | None) -> datetime:
    """UTC ISO-8601 from Claude Code → aware datetime in `tz` (system local time if None)."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz)


def compute(conn: sqlite3.Connection, ws: WorkingSet | None = None) -> dict:
    """Usage analytics over the working set's window (default: all-time)."""
    ws = ws or WorkingSet()
    with ws.lens(conn):
        result = _compute(conn, ws.tz)
    result["window"] = ws.window.label
    return result


def _compute(conn: sqlite3.Connection, tz: tzinfo | None = None) -> dict:
    conn.row_factory = sqlite3.Row
    prompts = conn.execute(
        "SELECT p.*, s.project_name, s.project_key, s.entrypoint FROM prompts p "
        "JOIN sessions s USING (session_id) WHERE p.ts IS NOT NULL ORDER BY p.ts"
    ).fetchall()
    turns = conn.execute("SELECT * FROM turns WHERE ts IS NOT NULL ORDER BY ts").fetchall()
    sessions = conn.execute("SELECT * FROM sessions").fetchall()
    tz_name = str(tz) if tz is not None else (datetime.now().astimezone().tzname() or "local")

    return {
        "timezone": tz_name,
        "totals": _totals(conn, prompts, turns, sessions),
        "models": _models(turns, tz),
        "effort": _effort(turns),
        "time": _time(prompts, tz),
        "sessions": _sessions(conn, sessions),
        "prompt_length": _lengths(prompts),
        "surfaces": dict(Counter(p["entrypoint"] or "unknown" for p in prompts).most_common()),
        "projects": _projects(prompts),
        "commands": dict(
            Counter(p["text"].split()[0] for p in prompts if p["kind"] == "command").most_common(15)
        ),
        "tools": dict(
            conn.execute(
                "SELECT name, COUNT(*) FROM tool_uses GROUP BY name ORDER BY 2 DESC LIMIT 15"
            ).fetchall()
        ),
    }


def _totals(conn, prompts, turns, sessions) -> dict:
    kinds = Counter(p["kind"] for p in prompts)
    return {
        "prompts": len(prompts),
        "prompts_by_kind": dict(kinds.most_common()),
        "sessions": len(sessions),
        "projects": len({s["project_key"] for s in sessions if s["project_key"]}),
        "assistant_messages": len(turns),
        "tool_calls": conn.execute("SELECT COUNT(*) FROM tool_uses").fetchone()[0],
        "first_prompt": prompts[0]["ts"] if prompts else None,
        "last_prompt": prompts[-1]["ts"] if prompts else None,
    }


def _models(turns, tz) -> dict:
    by_messages = Counter(t["model"] for t in turns if t["model"])
    output_tokens: Counter = Counter()
    by_month: dict[str, Counter] = defaultdict(Counter)
    for t in turns:
        if not t["model"]:
            continue
        output_tokens[t["model"]] += t["output_tokens"] or 0
        by_month[parse_ts(t["ts"], tz).strftime("%Y-%m")][t["model"]] += 1

    # Which model "led" each session: the one that wrote most of its replies.
    per_session: dict[str, Counter] = defaultdict(Counter)
    for t in turns:
        if t["model"]:
            per_session[t["session_id"]][t["model"]] += 1
    main_model = Counter(c.most_common(1)[0][0] for c in per_session.values())

    return {
        "by_messages": dict(by_messages.most_common()),
        "share": _shares(by_messages),
        "main_model_per_session": dict(main_model.most_common()),
        "output_tokens": dict(output_tokens.most_common()),
        "by_month": {m: dict(c.most_common()) for m, c in sorted(by_month.items())},
        "switching_sessions": sum(1 for c in per_session.values() if len(c) > 1),
    }


def _effort(turns) -> dict:
    counts = Counter(t["effort"] or "unset" for t in turns)
    return {"by_messages": dict(counts.most_common()), "share": _shares(counts)}


def _time(prompts, tz) -> dict:
    hours = [0] * 24
    weekdays = [0] * 7
    grid = [[0] * 24 for _ in range(7)]  # weekday × hour, for the Dashboard heatmap
    months: Counter = Counter()
    days: set[str] = set()
    for p in prompts:
        local = parse_ts(p["ts"], tz)
        hours[local.hour] += 1
        weekdays[local.weekday()] += 1
        grid[local.weekday()][local.hour] += 1
        months[local.strftime("%Y-%m")] += 1
        days.add(local.date().isoformat())

    def part_of_day(h: int) -> str:
        return (
            "night (0–6)"
            if h < 6
            else "morning (6–12)"
            if h < 12
            else "afternoon (12–18)"
            if h < 18
            else "evening (18–24)"
        )

    parts: Counter = Counter()
    for h, n in enumerate(hours):
        parts[part_of_day(h)] += n
    return {
        "by_hour": hours,
        "by_weekday": dict(zip(WEEKDAYS, weekdays, strict=True)),
        "weekday_hour_grid": grid,
        "by_part_of_day": dict(parts.most_common()),
        "peak_hour": max(range(24), key=hours.__getitem__) if prompts else None,
        "peak_weekday": WEEKDAYS[max(range(7), key=weekdays.__getitem__)] if prompts else None,
        "by_month": dict(sorted(months.items())),
        "active_days": len(days),
    }


def _sessions(conn, sessions) -> dict:
    """Session shape: prompts per session and *active* minutes (pauses > 30 min don't count)."""
    stamps: dict[str, list[datetime]] = defaultdict(list)
    for sid, ts in conn.execute(
        "SELECT session_id, ts FROM prompts WHERE ts IS NOT NULL "
        "UNION ALL SELECT session_id, ts FROM turns WHERE ts IS NOT NULL"
    ):
        stamps[sid].append(parse_ts(ts, None))
    prompts_per = dict(conn.execute("SELECT session_id, COUNT(*) FROM prompts GROUP BY 1"))

    active = []
    for times in stamps.values():
        times.sort()
        minutes = sum(
            gap
            for a, b in zip(times, times[1:], strict=False)
            if (gap := (b - a).total_seconds() / 60) <= IDLE_GAP_MINUTES
        )
        active.append(minutes)
    counts = [prompts_per.get(s["session_id"], 0) for s in sessions]
    with_prompts = [c for c in counts if c]
    return {
        "count": len(sessions),
        "with_prompts": len(with_prompts),
        "prompts_per_session": _describe(with_prompts),
        "active_minutes": _describe(active),
        "with_subagents": sum(1 for s in sessions if s["subagent_files"]),
    }


def _lengths(prompts) -> dict:
    typed = [p["word_len"] for p in prompts if p["kind"] == "typed"]
    buckets = {(f"{lo}–{hi}" if hi else f"{lo}+"): 0 for lo, hi in WORD_BUCKETS}
    for n in typed:
        for (lo, hi), label in zip(WORD_BUCKETS, buckets, strict=True):
            if n >= lo and (hi is None or n <= hi):
                buckets[label] += 1
                break
    return {
        "words": _describe(typed),
        "buckets": buckets,
        "with_paste": sum(1 for p in prompts if p["has_paste"]),
        "with_image": sum(1 for p in prompts if p["has_image"]),
    }


def _projects(prompts) -> list[dict]:
    per: dict[str, dict] = {}
    for p in prompts:
        key = p["project_key"] or "unknown"
        entry = per.setdefault(key, {"key": key, "name": p["project_name"], "prompts": 0})
        entry["prompts"] += 1
        entry["last_prompt"] = p["ts"]
    return sorted(per.values(), key=lambda e: -e["prompts"])


def _describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(values),
        "median": round(statistics.median(ordered), 1),
        "mean": round(statistics.fmean(ordered), 1),
        "p90": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 1),
        "max": round(ordered[-1], 1),
    }


def _shares(counts: Counter) -> dict:
    total = sum(counts.values()) or 1
    return {k: round(100 * v / total, 1) for k, v in counts.most_common()}


# ── Terminal report ──────────────────────────────────────────────────────────

_BLOCKS = " ▁▂▃▄▅▆▇█"


def _bar(n: float, top: float, width: int = 24) -> str:
    return "█" * max(1 if n else 0, round(width * n / top)) if top else ""


def _spark(values: list[int]) -> str:
    top = max(values) or 1
    return "".join(_BLOCKS[round(8 * v / top)] for v in values)


def render(s: dict) -> str:
    t, tm = s["totals"], s["time"]
    out: list[str] = []
    add = out.append
    add("POMPY · USAGE PATTERNS (all-time)")
    add(
        f"{t['prompts']} prompts · {t['sessions']} sessions · {t['projects']} projects · "
        f"{tm['active_days']} active days · {t['first_prompt'][:10]} → {t['last_prompt'][:10]}"
    )
    add("")
    add("Models (share of Claude's replies)")
    for model, pct in s["models"]["share"].items():
        add(f"  {model:<28} {_bar(pct, 100)} {pct}%")
    add(
        f"  {s['models']['switching_sessions']} sessions used more than one model · "
        f"main model per session: "
        + ", ".join(f"{m} {n}" for m, n in s["models"]["main_model_per_session"].items())
    )
    add("")
    add("Effort (share of replies)")
    add("  " + " · ".join(f"{e} {p}%" for e, p in s["effort"]["share"].items()))
    add("")
    add(f"When you prompt ({s['timezone']})")
    add(f"  by hour   0h {_spark(tm['by_hour'])} 23h   peak: {tm['peak_hour']}:00")
    wd = tm["by_weekday"]
    top_wd = max(wd.values()) or 1
    for day, n in wd.items():
        add(f"  {day}  {_bar(n, top_wd, 20):<20} {n}")
    add("  " + " · ".join(f"{part} {n}" for part, n in tm["by_part_of_day"].items()))
    top_hours = sorted(range(24), key=lambda h: -tm["by_hour"][h])[:3]
    add("  busiest hours: " + ", ".join(f"{h}:00 ({tm['by_hour'][h]})" for h in top_hours))
    add("  by month: " + " · ".join(f"{m} {n}" for m, n in tm["by_month"].items()))
    add("")
    se, pl = s["sessions"], s["prompt_length"]
    add("Sessions")
    add(
        f"  prompts per session: median {se['prompts_per_session'].get('median')} · "
        f"p90 {se['prompts_per_session'].get('p90')} · max {se['prompts_per_session'].get('max')}"
    )
    add(
        f"  active minutes: median {se['active_minutes'].get('median')} · "
        f"p90 {se['active_minutes'].get('p90')}   ({se['with_subagents']} sessions used sub-agents)"
    )
    add("")
    add("Prompt length (typed prompts, words)")
    w = pl["words"]
    add(
        f"  median {w.get('median')} · mean {w.get('mean')} · "
        f"p90 {w.get('p90')} · max {w.get('max')}"
    )
    top_b = max(pl["buckets"].values()) or 1
    for label, n in pl["buckets"].items():
        add(f"  {label:>7} words {_bar(n, top_b, 20):<20} {n}")
    add(f"  {pl['with_paste']} with pasted text · {pl['with_image']} with images")
    add("")
    add("Where you use Claude Code")
    add("  " + " · ".join(f"{k} {v}" for k, v in s["surfaces"].items()))
    if s["commands"]:
        add("Top slash commands")
        add("  " + " · ".join(f"{k} {v}" for k, v in list(s["commands"].items())[:8]))
    if s["tools"]:
        add("Tools Claude used most")
        add("  " + " · ".join(f"{k} {v}" for k, v in list(s["tools"].items())[:8]))
    return "\n".join(out)
