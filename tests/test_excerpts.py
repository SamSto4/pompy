"""Step 10: exchange excerpts, Pompy's episodic memory units (prompt + reply + what happened)."""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import excerpts
from pompy.ingest import ingest
from pompy.working_set import WorkingSet

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


@pytest.fixture
def conn(tmp_path):
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    report = ingest(tmp_path / "projects", tmp_path / "data")
    assert report.excerpts == 7  # ingest builds them
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, UTC)  # again in UTC, so headers don't depend on this machine's zone
    yield c
    c.close()


def _row(conn, uuid):
    conn.row_factory = sqlite3.Row
    return dict(conn.execute("SELECT * FROM excerpts WHERE prompt_uuid = ?", (uuid,)).fetchone())


def test_one_excerpt_per_typed_prompt_with_words(conn):
    uuids = {r[0] for r in conn.execute("SELECT prompt_uuid FROM excerpts")}
    assert uuids == {"a-u1", "a-u9", "a-u17", "b-u1", "b-u3", "b-u4", "b-u8"}
    assert "a-u11" not in uuids  # a slash command is not an exchange


def test_an_excerpt_holds_the_whole_exchange(conn):
    e = _row(conn, "b-u1")
    assert e["prompt"] == "fix the bug"
    assert e["reply"] == "Which bug do you mean?"
    assert e["outcome"] == 'Claude had to ask: "Which bug do you mean?"'
    assert e["friction"] == "clarify"
    assert e["body"].splitlines() == [
        "notes-api · 2026-04-02 · opened the chat",
        "You: fix the bug",
        "Claude: Which bug do you mean?",
        'Then: Claude had to ask: "Which bug do you mean?"',
    ]


def test_outcomes_say_what_happened_next(conn):
    assert _row(conn, "a-u1")["outcome"] == (
        "you corrected it; you interrupted; you rejected a tool call"
    )
    assert _row(conn, "a-u9")["outcome"] == "moved on"
    assert _row(conn, "a-u17")["outcome"] == "end of chat"


def test_the_header_carries_the_prompt_before(conn):
    assert _row(conn, "b-u3")["header"] == 'notes-api · 2026-04-02 · after "fix the bug"'
    long_before = _row(conn, "a-u9")["header"]
    assert long_before.startswith('demo-app · 2026-03-10 · after "Build a login page')
    assert long_before.endswith('…"')  # the previous prompt is clipped


def test_only_your_own_words_go_in(conn):
    assert "Traceback" not in _row(conn, "b-u3")["body"]  # the pasted error stays out


def test_context_makes_a_bare_prompt_findable(conn):
    # "why does this happen" never says "bug", but its header says it followed "fix the bug".
    hits = {h["uuid"] for h in excerpts.search(conn, "bug", tz=UTC)}
    assert {"b-u1", "b-u3"} <= hits


def test_search_respects_a_working_set_window(conn):
    ws = WorkingSet(tz=UTC, window=WorkingSet.for_period("2026-03", UTC).window)
    with ws.lens(conn):
        hits = excerpts.search(conn, "bug", tz=UTC, match_any=True)
    assert hits == []  # the bug exchanges happened in April


def test_rebuilding_is_idempotent(conn):
    sql = "SELECT prompt_uuid, text_hash FROM excerpts ORDER BY 1"
    first = conn.execute(sql).fetchall()
    excerpts.build(conn, UTC)
    assert conn.execute(sql).fetchall() == first
    assert conn.execute("SELECT COUNT(*) FROM excerpts_fts").fetchone()[0] == len(first)


def test_the_working_set_now_holds_exchanges(conn):
    ws = WorkingSet(tz=UTC, question="bug", budget_tokens=1000).retrieve(conn)
    top = ws.snippets[0]
    assert top.uuid == "b-u1" and top.reply == "Which bug do you mean?"
    assert top.outcome.startswith("Claude had to ask")
