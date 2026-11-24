"""Long-term memory: patterns with their evidence (layer 8 of the RAG architecture).

A pattern is a claim about how you work with Claude ("prompts without a done-definition go
wrong more often"), plus:
  - its evidence on both sides: excerpts that support it and excerpts that contradict it;
  - its numbers: effect size, samples, p-value and q-value (corrected for testing many claims);
  - a status: hypothesis → supported, or contradicted, or retired;
  - a confidence that is set when the pattern is checked and fades with time since it was
    last confirmed (a fact that stops showing up should stop being trusted);
  - its history: every check is recorded, so you can watch it form, strengthen and fade.

The same claim checked again updates the same row (its `key`), which is what consolidation
means here: many episodes, re-read over time, become one durable fact or none.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

SUPPORTED_Q = 0.10  # q-value below which a pattern counts as supported
MIN_N = 15  # each side needs at least this many prompts to support or contradict
HALF_LIFE_DAYS = 90  # confidence halves for every 90 days without confirmation
RETIRE_BELOW = 0.15  # a decayed confidence under this retires the pattern

SCHEMA = """
CREATE TABLE IF NOT EXISTS patterns (
    id             INTEGER PRIMARY KEY,
    key            TEXT NOT NULL UNIQUE,   -- stable id of the claim, e.g. "lacks:done@all"
    statement      TEXT NOT NULL,
    scope          TEXT NOT NULL,          -- "all", "theme: login · bar · page", ...
    kind           TEXT NOT NULL,          -- risk | habit | preference | goal
    source         TEXT NOT NULL,          -- miner | agent | you
    status         TEXT NOT NULL,          -- hypothesis | supported | contradicted | retired
    confidence     REAL NOT NULL,          -- 0..1 when last checked
    stats          TEXT NOT NULL,          -- JSON: effect, rates, samples, p, q
    first_seen     TEXT NOT NULL,
    last_checked   TEXT NOT NULL,
    last_confirmed TEXT                    -- last check at which it was supported
);
CREATE TABLE IF NOT EXISTS pattern_evidence (
    pattern_id  INTEGER NOT NULL,
    prompt_uuid TEXT NOT NULL,             -- an excerpt: the evidence itself
    stance      TEXT NOT NULL,             -- supports | contradicts
    PRIMARY KEY (pattern_id, prompt_uuid)
);
CREATE TABLE IF NOT EXISTS pattern_history (
    pattern_id INTEGER NOT NULL,
    at         TEXT NOT NULL,
    status     TEXT NOT NULL,
    confidence REAL NOT NULL
);
"""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def judge(effect: float, n_a: int, n_b: int, q: float) -> tuple[str, float]:
    """Status and confidence from the numbers. `effect` > 0 means the claim's direction holds."""
    enough = min(n_a, n_b) >= MIN_N
    if enough and effect > 0 and q < SUPPORTED_Q:
        status = "supported"
    elif enough and effect < 0 and q < SUPPORTED_Q:
        status = "contradicted"
    else:
        status = "hypothesis"
    # Confidence: how unlikely chance is (1 - q), scaled down while samples are small.
    confidence = 0.0 if effect <= 0 else (1 - q) * min(1.0, min(n_a, n_b) / (2 * MIN_N))
    return status, round(confidence, 3)


def record(
    conn: sqlite3.Connection,
    *,
    key: str,
    statement: str,
    scope: str,
    kind: str,
    source: str,
    stats: dict,
    supports: list[str],
    contradicts: list[str],
    now: datetime | None = None,
) -> dict:
    """Store or re-check one pattern; replace its evidence; append to its history."""
    conn.executescript(SCHEMA)
    at = _now(now).isoformat(timespec="seconds")
    status, confidence = judge(stats["effect"], stats["n_a"], stats["n_b"], stats["q"])
    old = conn.execute(
        "SELECT id, first_seen, last_confirmed FROM patterns WHERE key = ?", (key,)
    ).fetchone()
    confirmed = at if status == "supported" else (old[2] if old else None)
    if old:
        pid = old[0]
        conn.execute(
            "UPDATE patterns SET statement=?, scope=?, kind=?, source=?, status=?, confidence=?, "
            "stats=?, last_checked=?, last_confirmed=? WHERE id=?",
            (
                statement,
                scope,
                kind,
                source,
                status,
                confidence,
                json.dumps(stats),
                at,
                confirmed,
                pid,
            ),
        )
    else:
        pid = conn.execute(
            "INSERT INTO patterns (key, statement, scope, kind, source, status, confidence, stats, "
            "first_seen, last_checked, last_confirmed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                statement,
                scope,
                kind,
                source,
                status,
                confidence,
                json.dumps(stats),
                at,
                at,
                confirmed,
            ),
        ).lastrowid
    conn.execute("DELETE FROM pattern_evidence WHERE pattern_id = ?", (pid,))
    conn.executemany(
        "INSERT OR IGNORE INTO pattern_evidence (pattern_id, prompt_uuid, stance) VALUES (?,?,?)",
        [(pid, u, "supports") for u in supports] + [(pid, u, "contradicts") for u in contradicts],
    )
    conn.execute(
        "INSERT INTO pattern_history (pattern_id, at, status, confidence) VALUES (?,?,?,?)",
        (pid, at, status, confidence),
    )
    conn.commit()
    return {"id": pid, "status": status, "confidence": confidence}


def decayed(confidence: float, last_confirmed: str | None, now: datetime | None = None) -> float:
    """Confidence today: halves for every HALF_LIFE_DAYS since the pattern was last confirmed."""
    if not last_confirmed:
        return confidence
    days = (_now(now) - datetime.fromisoformat(last_confirmed)).total_seconds() / 86400
    return round(confidence * 0.5 ** (max(days, 0) / HALF_LIFE_DAYS), 3)


def retire_stale(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    """Retire supported patterns whose confidence has faded below the floor. Returns their keys."""
    conn.executescript(SCHEMA)
    at = _now(now).isoformat(timespec="seconds")
    retired = []
    for pid, key, conf, confirmed in conn.execute(
        "SELECT id, key, confidence, last_confirmed FROM patterns WHERE status = 'supported'"
    ).fetchall():
        now_conf = decayed(conf, confirmed, now)
        if now_conf < RETIRE_BELOW:
            conn.execute(
                "UPDATE patterns SET status='retired', last_checked=? WHERE id=?", (at, pid)
            )
            conn.execute(
                "INSERT INTO pattern_history (pattern_id, at, status, confidence) VALUES (?,?,?,?)",
                (pid, at, "retired", now_conf),
            )
            retired.append(key)
    conn.commit()
    return retired


def load(
    conn: sqlite3.Connection, status: str | None = None, now: datetime | None = None
) -> list[dict]:
    """Patterns with today's (decayed) confidence, evidence and history, strongest first."""
    conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    sql, params = "SELECT * FROM patterns", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    out = []
    for r in conn.execute(sql, params).fetchall():
        p = dict(r)
        p["stats"] = json.loads(p["stats"])
        p["confidence_now"] = decayed(p["confidence"], p["last_confirmed"], now)
        ev = conn.execute(
            "SELECT pe.stance, e.prompt_uuid, e.prompt, e.outcome, e.ts FROM pattern_evidence pe "
            "JOIN excerpts e ON e.prompt_uuid = pe.prompt_uuid "
            "WHERE pe.pattern_id = ? ORDER BY e.ts DESC",
            (p["id"],),
        ).fetchall()
        p["supports"] = [dict(e) for e in ev if e["stance"] == "supports"]
        p["contradicts"] = [dict(e) for e in ev if e["stance"] == "contradicts"]
        p["history"] = [
            dict(h)
            for h in conn.execute(
                "SELECT at, status, confidence FROM pattern_history "
                "WHERE pattern_id = ? ORDER BY at",
                (p["id"],),
            )
        ]
        out.append(p)
    out.sort(key=lambda p: (p["status"] != "supported", -p["confidence_now"]))
    return out
