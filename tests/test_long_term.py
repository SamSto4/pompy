"""Phase 3, Steps 16-17: long-term memory (patterns with evidence) and the pattern miner."""

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import excerpts, mine, patterns
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, ZoneInfo("UTC"))
    yield c
    c.close()


def _stats(effect, n=40, q=0.01):
    return {"effect": effect, "rate_a": 0.3, "rate_b": 0.3 - effect, "n_a": n, "n_b": n,
            "p": q, "q": q}  # fmt: skip


def _record(conn, effect, at, n=40, q=0.01, supports=("a-u1",), contradicts=("b-u1",)):
    return patterns.record(
        conn, key="lacks:done@all", statement="Prompts without a done definition go wrong more",
        scope="all", kind="risk", source="miner", stats=_stats(effect, n, q),
        supports=list(supports), contradicts=list(contradicts), now=at,
    )  # fmt: skip


# ── Step 16: the store ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "effect, n, q, status",
    [
        (0.2, 40, 0.01, "supported"),
        (-0.2, 40, 0.01, "contradicted"),
        (0.2, 40, 0.5, "hypothesis"),  # could be chance
        (0.2, 5, 0.01, "hypothesis"),  # too few prompts to tell
    ],
)
def test_status_comes_from_the_numbers(effect, n, q, status):
    assert patterns.judge(effect, n, n, q)[0] == status


def test_a_pattern_keeps_its_evidence_on_both_sides(conn):
    _record(conn, 0.2, T0)
    [p] = patterns.load(conn, now=T0)
    assert p["status"] == "supported"
    assert [e["prompt_uuid"] for e in p["supports"]] == ["a-u1"]
    assert [e["prompt_uuid"] for e in p["contradicts"]] == ["b-u1"]
    assert p["contradicts"][0]["outcome"].startswith("Claude had to ask")  # the excerpt itself


def test_rechecking_the_same_claim_updates_one_row_and_grows_its_history(conn):
    _record(conn, 0.2, T0)
    _record(conn, 0.05, T0 + timedelta(days=30), q=0.6)  # the effect shrank: no longer supported
    [p] = patterns.load(conn, now=T0 + timedelta(days=30))
    assert p["status"] == "hypothesis"
    assert [h["status"] for h in p["history"]] == ["supported", "hypothesis"]
    assert p["last_confirmed"].startswith("2026-01-01")  # still the last time it held


def test_confidence_fades_without_confirmation_and_the_pattern_retires(conn):
    first = _record(conn, 0.2, T0)
    assert patterns.decayed(first["confidence"], T0.isoformat(), T0 + timedelta(days=90)) == (
        pytest.approx(first["confidence"] / 2, abs=0.002)
    )
    assert patterns.retire_stale(conn, T0 + timedelta(days=60)) == []  # still trusted
    assert patterns.retire_stale(conn, T0 + timedelta(days=400)) == ["lacks:done@all"]
    [p] = patterns.load(conn, now=T0 + timedelta(days=400))
    assert p["status"] == "retired" and p["history"][-1]["status"] == "retired"


def test_reconfirming_resets_the_fade(conn):
    _record(conn, 0.2, T0)
    _record(conn, 0.2, T0 + timedelta(days=300))  # holds again
    assert patterns.retire_stale(conn, T0 + timedelta(days=330)) == []


# ── Step 17: the miner ───────────────────────────────────────────────────────


def test_two_proportion_test():
    effect, p = mine.two_proportion(30, 100, 10, 100)
    assert effect == pytest.approx(0.2) and p < 0.001
    assert mine.two_proportion(10, 100, 10, 100) == (0.0, 1.0)


def test_benjamini_hochberg_corrects_for_many_tests():
    ps = [0.01, 0.04, 0.03, 0.5]
    q = mine.benjamini_hochberg(ps)
    assert q == pytest.approx([0.04, 0.0533, 0.0533, 0.5], abs=1e-3)
    assert all(qi >= pi for qi, pi in zip(q, ps, strict=True))  # never more optimistic


def test_mining_stores_claims_with_evidence_and_rechecks_them(conn, monkeypatch, tmp_path):
    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))  # no themes file
    monkeypatch.setattr(mine, "MIN_GROUP", 1)  # the fixtures are tiny
    first = mine.mine(conn, ZoneInfo("UTC"), now=T0)
    assert first["tested"] > 0
    keys = {r["key"] for r in first["results"]}
    assert "very-short@all" in keys
    stored = {p["key"]: p for p in patterns.load(conn, now=T0)}
    assert set(stored) == keys
    for p in stored.values():  # every claim cites real excerpts
        cited = p["supports"] + p["contradicts"]
        assert all(e["prompt"] for e in cited)
    mine.mine(conn, ZoneInfo("UTC"), now=T0 + timedelta(days=7))
    again = {p["key"]: p for p in patterns.load(conn, now=T0 + timedelta(days=7))}
    assert all(len(p["history"]) == 2 for p in again.values())  # consolidation, not duplication
    assert "claims over" in mine.render(first)
