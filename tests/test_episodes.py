"""Step 13: episode cards, one per chat, for locating; excerpts remain the evidence."""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeEmbedder

from pompy import embed, episodes, excerpts, score
from pompy.ingest import ingest
from pompy.working_set import WorkingSet

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


@pytest.fixture
def conn(tmp_path):
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    report = ingest(tmp_path / "projects", tmp_path / "data")
    assert report.episodes == 2  # ingest builds them
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, UTC)
    episodes.build(c, UTC)
    yield c
    c.close()


def _card(conn, sid):
    return next(c for c in episodes.cards(conn) if c["session_id"] == sid)


def test_one_card_per_chat_newest_first(conn):
    assert [c["session_id"] for c in episodes.cards(conn)] == ["sess-beta", "sess-alpha"]


def test_a_card_says_what_the_chat_was_for_and_how_it_went(conn):
    a = _card(conn, "sess-alpha")
    assert a["title"] == "Build login page"
    assert a["intent"].startswith("Build a login page for the demo app")
    assert a["last_prompt"] == "also add a forgot password link"
    assert (a["exchanges"], a["tasks"]) == (3, 2)
    assert a["friction_kinds"] == {"correction": 1, "interrupt": 1, "rejected": 1}
    assert a["local_start"] == "2026-03-10 23:30" and a["minutes"] == 12


def test_the_mean_score_matches_pompy_score(conn):
    score.compute(conn)
    rows = conn.execute(
        "SELECT score FROM prompt_scores WHERE session_id = 'sess-alpha' AND role = 'task'"
    ).fetchall()
    assert _card(conn, "sess-alpha")["mean_score"] == sum(r[0] for r in rows) / len(rows)


def test_outcomes(conn):
    assert _card(conn, "sess-beta")["outcome"] == "stopped after friction"  # last: Claude asked
    assert _card(conn, "sess-alpha")["outcome"] == "talk only"  # no file was changed


@pytest.mark.parametrize(
    "replies, shipped",
    [
        (["Committed and pushed to main."], True),
        (["Deployed to production."], True),
        (["Nothing is committed yet."], False),
        (["I have not pushed anything."], False),
        (["Changes haven't been committed."], False),
        (["Done. Want me to commit?"], False),
    ],
)
def test_shipped_reads_replies_but_not_negations(replies, shipped):
    assert episodes._shipped(replies) is shipped


def test_cards_respect_a_window_and_project(conn):
    april = WorkingSet(tz=UTC, window=WorkingSet.for_period("2026-04", UTC).window)
    with april.lens(conn):
        assert [c["session_id"] for c in episodes.cards(conn)] == ["sess-beta"]
    assert [c["session_id"] for c in episodes.cards(conn, project="demo-app")] == ["sess-alpha"]


def test_find_locates_chats_and_brings_the_evidence(conn):
    fake = FakeEmbedder()
    embed.update(conn, fake)
    found = episodes.find(conn, "sign in with credentials", fake, tz=UTC)
    top = found[0]
    assert top["card"]["session_id"] == "sess-alpha"
    assert top["evidence"] and all(e["session_id"] == "sess-alpha" for e in top["evidence"])
    assert {"prompt", "outcome", "uuid"} <= set(top["evidence"][0])  # excerpts, not the card


def test_render_card_reads_as_one_short_block(conn):
    text = episodes.render_card(_card(conn, "sess-beta"))
    assert text.splitlines()[1].strip() == "Fix notes API bug"
    assert "ended: stopped after friction" in text and len(text.splitlines()) == 3
