"""Step 12: hybrid retrieval (keyword + meaning, fused by RRF) and the retrieval eval."""

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeEmbedder

from pompy import embed, evaluate, excerpts, hybrid
from pompy.ingest import ingest
from pompy.search import to_fts_query
from pompy.working_set import WorkingSet

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("UTC")


@pytest.fixture
def conn(tmp_path):
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, TZ)
    embed.update(c, FakeEmbedder())
    yield c
    c.close()


def test_rrf_rewards_agreement_and_needs_no_weights():
    scores = hybrid.rrf(["a", "b", "c"], ["c", "a", "d"], k=60)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert max(scores, key=scores.get) == "a"  # high in both lists
    assert scores["c"] > scores["b"]  # found by both beats found by one
    assert scores["d"] == pytest.approx(1 / 63)


def test_or_queries_drop_stopwords():
    assert to_fts_query("paging through the results", match_any=True) == '"paging" OR "results"'
    assert to_fts_query("is it", match_any=True) == '"is" OR "it"'  # nothing but stopwords
    assert to_fts_query("paging through", match_any=False) == '"paging" "through"'


def test_hybrid_keeps_both_strengths(conn):
    fake = FakeEmbedder()
    exact = hybrid.search(conn, "forgot password link", fake, limit=3, tz=TZ)
    assert exact[0]["uuid"] == "a-u17" and exact[0]["keyword_rank"] == 1
    para = hybrid.search(conn, "sign in with credentials", fake, limit=3, tz=TZ)
    assert "a-u1" in {h["uuid"] for h in para}
    assert all(h["keyword_rank"] is None for h in para if h["uuid"] == "a-u1")  # meaning only


def test_every_hit_says_where_it_came_from(conn):
    for h in hybrid.search(conn, "fix the bug", FakeEmbedder(), limit=5, tz=TZ):
        assert h["keyword_rank"] or h["meaning_rank"]
        assert h["rrf"] > 0 and h["session_id"] and h["uuid"]


def test_without_an_embedder_hybrid_is_keyword_search(conn):
    plain = [h["uuid"] for h in excerpts.search(conn, "fix the bug", tz=TZ, match_any=True)]
    fused = [h["uuid"] for h in hybrid.search(conn, "fix the bug", None, limit=30, tz=TZ)]
    assert fused == plain


def test_blend_lifts_exchanges_where_something_went_wrong(conn):
    fake = FakeEmbedder()
    now = datetime(2026, 4, 3, tzinfo=UTC)
    plain = hybrid.search(conn, "notes list bug", fake, limit=10, tz=TZ, now=now)
    blended = hybrid.search(conn, "notes list bug", fake, limit=10, tz=TZ, blend=True, now=now)
    rank = lambda hits, u: [h["uuid"] for h in hits].index(u)  # noqa: E731
    assert rank(blended, "b-u1") <= rank(plain, "b-u1")  # b-u1 had friction (clarify)


def test_eval_on_the_fixtures(conn):
    cases = evaluate.load(FIXTURES / "eval.json")
    result = evaluate.run(conn, cases, FakeEmbedder(), k=5)
    m = result["modes"]
    assert m["hybrid"]["hit@5"] == 1.0  # every question answered in the top 5
    assert m["hybrid"]["hit@5"] >= max(m["keyword"]["hit@5"], m["meaning"]["hit@5"])
    assert m["keyword"]["hit@5"] < 1.0  # paraphrases defeat keywords: the reason for meaning
    assert "hybrid" in evaluate.render(result)


def test_eval_without_an_embedder_scores_keyword_only(conn):
    result = evaluate.run(conn, evaluate.load(FIXTURES / "eval.json"), None)
    assert list(result["modes"]) == ["keyword"]


def test_the_working_set_uses_hybrid_when_it_can(conn):
    ws = WorkingSet(tz=TZ, question="sign in with credentials", budget_tokens=2000)
    ws.retrieve(conn, embedder=FakeEmbedder())
    assert ws.retrieval == "hybrid" and "a-u1" in {s.uuid for s in ws.snippets}
    kw = WorkingSet(tz=TZ, question="sign in with credentials").retrieve(conn, embedder=None)
    assert kw.retrieval == "keyword" and "a-u1" not in {s.uuid for s in kw.snippets}
