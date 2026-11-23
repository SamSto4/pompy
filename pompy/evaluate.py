"""`pompy eval`: measure retrieval before trusting it.

An eval set is a list of questions, each with the exchanges a good search must find:
    [{"query": "save my work to github", "relevant": ["<prompt uuid>", ...]}, ...]
Each search mode answers every question; two numbers say how well:
  - hit@k: share of questions with at least one right exchange in the top k
  - MRR (mean reciprocal rank): 1 / the rank of the first right exchange, averaged;
    1.0 means it was always first, 0.5 means typically second
Real eval sets hold your own prompts, so they live in data/ (git-ignored), never in the repo.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pompy import embed, excerpts, hybrid

MODES = ("keyword", "meaning", "hybrid")


def load(path: Path) -> list[dict]:
    cases = json.loads(path.read_text())
    for c in cases:
        if not c.get("query") or not c.get("relevant"):
            raise ValueError(f"each case needs a query and relevant uuids: {c!r}")
    return cases


def run(conn: sqlite3.Connection, cases: list[dict], embedder, k: int = 5) -> dict:
    """Score every mode on every case. Without an embedder, only keyword is scored."""
    modes = MODES if embedder is not None else ("keyword",)
    per_mode: dict[str, dict] = {}
    for mode in modes:
        hits, rr, rows = 0, 0.0, []
        for c in cases:
            if mode == "keyword":
                found = excerpts.search(conn, c["query"], limit=k, match_any=True)
            elif mode == "meaning":
                found = embed.search(conn, c["query"], embedder, limit=k)
            else:
                found = hybrid.search(conn, c["query"], embedder, limit=k)
            ranked = [h["uuid"] for h in found]
            first = next((i for i, u in enumerate(ranked, 1) if u in c["relevant"]), None)
            hits += first is not None
            rr += 1 / first if first else 0
            rows.append({"query": c["query"], "first_right_rank": first})
        n = len(cases) or 1
        per_mode[mode] = {
            f"hit@{k}": round(hits / n, 3),
            "mrr": round(rr / n, 3),
            "cases": rows,
        }
    return {"k": k, "cases": len(cases), "modes": per_mode}


def render(result: dict) -> str:
    k = result["k"]
    out = [f"Retrieval eval: {result['cases']} questions, top {k}", ""]
    out.append(f"  {'mode':<10}{'hit@' + str(k):>8}{'MRR':>8}")
    for mode, m in result["modes"].items():
        out.append(f"  {mode:<10}{m[f'hit@{k}']:>8}{m['mrr']:>8}")
    out += ["", "  rank of the first right exchange per question (– = not in the top k):"]
    modes = list(result["modes"])
    out.append("  " + "".join(f"{m:>9}" for m in modes) + "  question")
    for i, case in enumerate(next(iter(result["modes"].values()))["cases"]):
        ranks = [result["modes"][m]["cases"][i]["first_right_rank"] for m in modes]
        cells = "".join(f"{(r if r else '–'):>9}" for r in ranks)
        out.append(f"  {cells}  {case['query'][:60]}")
    return "\n".join(out)
