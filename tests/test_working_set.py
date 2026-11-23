"""Step 7: the WorkingSet (short-term memory): windows, the lens, retrieval, comparison."""

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import compare, keywords, score, stats, store
from pompy.ingest import ingest
from pompy.parse import Prompt
from pompy.search import search
from pompy.working_set import Window, WorkingSet, before, parse_period

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    source = tmp_path / "projects"
    shutil.copytree(FIXTURES / "projects", source)
    ingest(source, tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _ws(period, tz=UTC, **kw):
    return WorkingSet(window=parse_period(period, tz, now=NOW, **kw), tz=tz)


# ── Periods follow your local calendar ───────────────────────────────────────


def test_default_is_all_time():
    ws = WorkingSet()
    assert ws.window.all_time and ws.window.label == "all-time"


@pytest.mark.parametrize(
    "period, since, until",
    [
        ("this-month", "2026-11-01T00:00:00.000Z", "2026-12-01T00:00:00.000Z"),
        ("last-month", "2026-10-01T00:00:00.000Z", "2026-11-01T00:00:00.000Z"),
        ("2026-12", "2026-12-01T00:00:00.000Z", "2027-01-01T00:00:00.000Z"),
        ("this-week", "2026-11-16T00:00:00.000Z", "2026-11-23T00:00:00.000Z"),
        ("2026", "2026-01-01T00:00:00.000Z", "2027-01-01T00:00:00.000Z"),
        ("2026-09..2026-10", "2026-09-01T00:00:00.000Z", "2026-11-01T00:00:00.000Z"),
        ("..2026-10", None, "2026-11-01T00:00:00.000Z"),
        ("2026-10-05..", "2026-10-05T00:00:00.000Z", None),
        ("7d", "2026-11-15T12:00:00.000Z", None),
    ],
)
def test_periods_in_utc(period, since, until):
    w = parse_period(period, UTC, now=NOW)
    assert (w.since, w.until) == (since, until)


def test_months_start_at_local_midnight():
    # India is UTC+5:30, so its November starts at 18:30 UTC on 31 October.
    w = parse_period("2026-11", ZoneInfo("Asia/Kolkata"), now=NOW)
    assert w.since == "2026-10-31T18:30:00.000Z"


@pytest.mark.parametrize("bad", ["bogus", "2026-13", "last-yearish"])
def test_bad_periods_explain_themselves(bad):
    with pytest.raises(ValueError):
        parse_period(bad, UTC, now=NOW)


def test_before_is_everything_earlier():
    w = before(parse_period("this-month", UTC, now=NOW))
    assert (w.since, w.until, w.label) == (None, "2026-11-01T00:00:00.000Z", "before this-month")
    with pytest.raises(ValueError):
        before(Window())


# ── The lens: insight functions see only the window ─────────────────────────


def test_lens_narrows_every_table_then_disappears(conn):
    ws = _ws("2026-04")  # only sess-beta happened in April
    with ws.lens(conn):
        assert {r[0] for r in conn.execute("SELECT DISTINCT session_id FROM prompts")} == {
            "sess-beta"
        }
        assert [r[0] for r in conn.execute("SELECT session_id FROM sessions")] == ["sess-beta"]
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM main.prompts").fetchone()[0] == 10
    # Afterwards the real tables are back, and writable.
    assert conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0] == 10
    store.save(conn, Prompt("z-1", "sess-beta", "2026-04-03T10:00:00.000Z", "typed", "typed",
                            "hello", "hello", False, False, None))  # fmt: skip
    assert conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0] == 11


def test_a_late_night_prompt_lands_in_your_local_day(conn):
    # a-u1 was sent at 23:30 UTC on 10 March: that's 05:00 on 11 March in India.
    india = ZoneInfo("Asia/Kolkata")
    assert stats.compute(conn, _ws("2026-03-10", india))["totals"]["prompts"] == 0
    assert stats.compute(conn, _ws("2026-03-11", india))["totals"]["prompts"] == 6
    assert stats.compute(conn, _ws("2026-03-10", UTC))["totals"]["prompts"] == 6


def test_a_window_and_its_complement_add_up_to_all_time(conn):
    march, rest = _ws("..2026-03"), _ws("2026-04..")
    for name, total in [("prompts", 10), ("assistant_messages", 5)]:
        parts = [stats.compute(conn, ws)["totals"][name] for ws in (march, rest)]
        assert sum(parts) == total == stats.compute(conn, WorkingSet(tz=UTC))["totals"][name]
    kw = [keywords.compute(conn, ws)["typed_prompts"] for ws in (march, rest)]
    assert sum(kw) == keywords.compute(conn, WorkingSet(tz=UTC))["typed_prompts"] == 7


def test_project_window(conn):
    ws = _ws("all", project="notes-api")
    assert stats.compute(conn, ws)["totals"]["prompts"] == 4
    assert stats.compute(conn, _ws("all", project="no-such-project"))["totals"]["prompts"] == 0


def test_search_respects_the_window(conn):
    assert search(conn, "login")  # all-time: the March login prompts match
    with _ws("2026-04").lens(conn):
        assert search(conn, "login") == []


# ── Facts about one prompt never change with the window ─────────────────────


def test_windowed_score_never_overwrites_all_time_scores(conn):
    score.compute(conn, WorkingSet(tz=UTC))
    score.compute(conn, _ws("2026-04"))
    assert conn.execute("SELECT COUNT(*) FROM prompt_scores").fetchone()[0] == 7


def test_friction_reads_full_memory_inside_a_lens(conn):
    everywhere = score.friction(conn)
    with _cut(conn):
        assert score.friction(conn) == everywhere


def _cut(conn):
    """A window that starts mid-session: after a-u9, before a-u17."""
    return WorkingSet(window=Window("cut", since="2026-03-10T23:35:00.000Z")).lens(conn)


def test_a_session_cut_by_the_window_keeps_its_real_opener(conn):
    # In the cut window, a-u17 is sess-alpha's first prompt, but not its real opener (a-u1).
    cut = score.compute(conn, WorkingSet(window=Window("cut", since="2026-03-10T23:35:00.000Z")))
    assert cut["score"]["session_openers"] == 1  # only sess-beta's opener (b-u1) is in view
    assert score.compute(conn, WorkingSet(tz=UTC))["score"]["session_openers"] == 2


# ── Retrieval: what goes into the context window, and what doesn't ──────────


def test_retrieve_fills_within_budget_and_says_what_was_left_out(conn):
    # A budget that fits one exchange (prompt + reply + outcome) but not all three candidates.
    ws = WorkingSet(tz=UTC, question="login button password", budget_tokens=40)
    ws.retrieve(conn)
    assert ws.snippets and ws.tokens_used <= 40
    assert all(s.session_id == "sess-alpha" for s in ws.snippets)
    assert ws.left_out and all("reason" in x for x in ws.left_out)
    m = ws.manifest()
    json.dumps(m)  # the Memory Lab reads this as JSON
    assert m["counts"]["prompts_in_memory"] == 10
    assert len(m["snippets"]) + len(m["left_out"]) == m["counts"]["candidates"]


def test_retrieve_caps_snippets_per_chat(conn):
    ws = WorkingSet(tz=UTC, question="login button password link", budget_tokens=10_000)
    ws.retrieve(conn)
    assert len(ws.snippets) <= 2
    assert any("from this chat" in x["reason"] for x in ws.left_out)


def test_retrieve_inside_a_window(conn):
    ws = WorkingSet(window=parse_period("2026-04", UTC, now=NOW), tz=UTC, question="login")
    ws.retrieve(conn)
    assert ws.snippets == [] and ws.window_counts["prompts_in_window"] == 4


# ── Compare: two working sets, same functions ────────────────────────────────


def test_compare_two_periods(conn):
    c = compare.compute(conn, _ws("2026-03"), _ws("2026-04"))
    rows = {r["key"]: r for r in c["rows"]}
    assert rows["typed"]["a"] + rows["typed"]["b"] == 7
    assert c["a"]["thin"] and c["b"]["thin"]  # a handful of prompts is thin evidence
    text = compare.render(c)
    assert "Specificity" in text and "thin evidence" in text
