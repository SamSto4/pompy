"""Step 20: showing what Pompy has learned (dashboard bundle, and the notch's after-send line)."""

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import excerpts, patterns, publish, similar
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 4, 3, 12, 0, tzinfo=UTC)
STRONG = {"effect": 0.3, "n_a": 40, "n_b": 40, "p": 0.001, "q": 0.01, "rate_a": 0.5,
          "rate_b": 0.2}  # fmt: skip
WEAK = {**STRONG, "n_a": 3, "q": 0.9}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, ZoneInfo("UTC"))
    yield c
    c.close()


def _rec(conn, key, stats, supports=("a-u1",), contradicts=("b-u1",), when=NOW, **kw):
    patterns.record(conn, key=key, statement=kw.get("statement", key), scope="all",
                    kind=kw.get("kind", "risk"), source=kw.get("source", "miner"), stats=stats,
                    supports=list(supports), contradicts=list(contradicts), now=when)  # fmt: skip


def test_the_bundle_carries_each_belief_with_evidence_both_ways_and_history(conn):
    _rec(conn, "lacks:done@all", WEAK, when=NOW - timedelta(days=10))
    _rec(conn, "lacks:done@all", STRONG)  # re-checked: now it holds
    patterns.record_fact(conn, key="goal@demo", statement="A demo app", scope="project: demo-app",
                         kind="goal", evidence=["a-u1"], confidence=0.8,
                         last_seen=(NOW - timedelta(days=30)).isoformat(), now=NOW)  # fmt: skip
    out = publish.learned(conn, ZoneInfo("UTC"), NOW)
    by = {p["key"]: p for p in out["patterns"]}
    done = by["lacks:done@all"]
    assert done["status"] == "supported" and done["tested"] and done["rates"] == [0.5, 0.2]
    assert done["n_for"] == 1 and done["n_against"] == 1
    assert done["for"][0]["t"] and done["against"][0]["d"].startswith("2026-")
    assert [h[2] for h in done["history"]] == ["hypothesis", "supported"]
    goal = by["goal@demo"]
    assert goal["source"] == "facts" and not goal["tested"]
    assert goal["c"] < 0.8  # 30 days of fading already
    assert out["half_life_days"] == patterns.HALF_LIFE_DAYS


def test_retired_beliefs_are_counted_not_shown(conn):
    patterns.record_fact(conn, key="goal@old", statement="Old", scope="project: old",
                         kind="goal", evidence=["a-u1"], confidence=0.5,
                         last_seen=(NOW - timedelta(days=400)).isoformat(), now=NOW)  # fmt: skip
    patterns.retire_stale(conn, NOW)
    out = publish.learned(conn, None, NOW)
    assert out["retired"] == 1 and not out["patterns"]


def test_evidence_text_is_redacted(conn):
    conn.execute("UPDATE excerpts SET prompt = 'mail me at a@b.com in /Users/someone/x' "
                 "WHERE prompt_uuid = 'a-u1'")  # fmt: skip
    _rec(conn, "lacks:done@all", STRONG)
    [p] = publish.learned(conn, None, NOW)["patterns"]
    assert "a@b.com" not in p["for"][0]["t"] and "/Users/someone" not in p["for"][0]["t"]


def test_a_supported_rule_applies_only_when_the_prompt_breaks_it(conn):
    _rec(conn, "lacks:done@all", STRONG, statement="Prompts without done definition go wrong")
    vague = similar.applies(conn, "fix the nav bar", set(), now=NOW)
    assert vague and vague["key"] == "lacks:done@all" and vague["confidence"] > 0.5
    clear = "Fix the nav bar in src/Nav.tsx so it collapses on mobile. Done when tests pass."
    assert similar.applies(conn, clear, set(), now=NOW) is None


def test_hypotheses_and_facts_never_reach_the_notch(conn):
    _rec(conn, "lacks:done@all", WEAK)
    patterns.record_fact(conn, key="goal@demo", statement="A demo app", scope="project: demo-app",
                         kind="goal", evidence=["a-u1"], confidence=0.8,
                         last_seen=NOW.isoformat(), now=NOW)  # fmt: skip
    assert similar.applies(conn, "fix the nav bar", {"a-u1"}, now=NOW) is None


def test_an_agent_pattern_applies_when_a_neighbour_is_its_evidence(conn):
    patterns.record(conn, key="agent:x", statement="Plans in the same message get skipped",
                    scope="all", kind="habit", source="agent", stats=STRONG,
                    supports=["a-u1", "a-u9"], contradicts=["b-u1"], now=NOW)  # fmt: skip
    assert similar.applies(conn, "anything", {"zz"}, now=NOW) is None
    hit = similar.applies(conn, "anything", {"a-u9"}, now=NOW)
    assert hit and hit["key"] == "agent:x"


def test_similar_reports_the_pattern_and_renders_it(conn):
    _rec(conn, "very-short@all", STRONG, statement="Very short task prompts go wrong more often")
    result = similar.similar(conn, "fix nav", embedder=None, tz=ZoneInfo("UTC"))
    assert result["pattern"]["key"] == "very-short@all"
    assert "Pompy has learned: Very short" in similar.render(result)
    assert "Pompy has learned: Very short" in similar.render({**result, "count": 0, "hits": []})


def test_replies_and_short_answers_never_get_a_learned_line(conn):
    _rec(conn, "very-short@all", STRONG)
    assert similar.applies(conn, "yes", set(), now=NOW) is None
    assert similar.applies(conn, "fix the nav bar", set(), now=NOW) is not None
