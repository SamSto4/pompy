"""Hybrid retrieval: keyword and meaning results merged into one ranking (layers 4 and 5).

Reciprocal Rank Fusion: each result list votes 1 / (k + rank) for every exchange it found,
and the votes add up. Nothing is tuned: an exchange ranked high by either search rises, and
one found by both rises most. k = 60 is the usual constant (it softens the gap between
rank 1 and rank 2 so one list can't dominate).

Optional blend (the Generative Agents retrieval formula, simplified): multiply by recency
(newer exchanges weigh a little more) and importance (exchanges where something went wrong
weigh more, because they are the ones worth learning from).

Every hit keeps where it came from: its keyword rank, its meaning rank, and its source
reference (session + prompt uuid), so an answer can always be traced to the original.
"""

from __future__ import annotations

import importlib.util
import math
import sqlite3
from datetime import UTC, datetime, tzinfo

from pompy import embed, excerpts

RRF_K = 60
# How many results each half contributes before fusion. Measured with `pompy eval`: at 30, many
# weak matches found by both halves (#12 + #15) outvoted one half's #1 and pushed right answers
# out of the top 5; at 10 the fusion beat both halves (hit@5 0.72, MRR 0.55 vs 0.50 / 0.37).
CANDIDATES = 10
RECENCY_HALF_LIFE_DAYS = 90
IMPORTANCE_BOOST = 0.5  # +50% for an exchange where friction followed


def rrf(*ranked: list[str], k: int = RRF_K) -> dict[str, float]:
    """Fuse ranked lists of ids into one score per id."""
    scores: dict[str, float] = {}
    for ids in ranked:
        for rank, i in enumerate(ids, 1):
            scores[i] = scores.get(i, 0.0) + 1.0 / (k + rank)
    return scores


def default_embedder(conn: sqlite3.Connection):
    """The real model, when the meaning index exists and fastembed is installed; else None."""
    if not embed.count(conn) or importlib.util.find_spec("fastembed") is None:
        return None
    return embed.FastEmbedder()


def search(
    conn: sqlite3.Connection,
    query: str,
    embedder=None,
    limit: int = 10,
    project: str | None = None,
    tz: tzinfo | None = None,
    blend: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    """Keyword + meaning over exchange excerpts, fused. Falls back to keyword-only without
    an embedder. Respects a WorkingSet lens."""
    kw = excerpts.search(conn, query, limit=CANDIDATES, project=project, tz=tz, match_any=True)
    mn = (
        embed.search(conn, query, embedder, limit=CANDIDATES, project=project, tz=tz)
        if embedder is not None
        else []
    )
    rows = {h["uuid"]: h for h in mn}
    rows.update({h["uuid"]: {**rows.get(h["uuid"], {}), **h} for h in kw})
    kw_rank = {h["uuid"]: r for r, h in enumerate(kw, 1)}
    mn_rank = {h["uuid"]: r for r, h in enumerate(mn, 1)}
    fused = rrf([h["uuid"] for h in kw], [h["uuid"] for h in mn])

    if blend:
        now = now or datetime.now(UTC)
        for uuid, score in fused.items():
            ts = rows[uuid]["ts"]
            age = (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).days if ts else 365
            recency = 0.5 ** (max(age, 0) / RECENCY_HALF_LIFE_DAYS)
            importance = 1 + (IMPORTANCE_BOOST if rows[uuid]["friction"] else 0)
            fused[uuid] = score * importance * (0.5 + 0.5 * recency)

    order = sorted(fused, key=lambda u: (-fused[u], kw_rank.get(u, math.inf)))[:limit]
    out = []
    for u in order:
        r = dict(rows[u])
        r.pop("score", None)
        r.update(
            rrf=round(fused[u], 5),
            keyword_rank=kw_rank.get(u),
            meaning_rank=mn_rank.get(u),
        )
        out.append(r)
    return out
