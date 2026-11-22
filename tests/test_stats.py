"""Step 4: usage analytics on the synthetic chats. Expected numbers worked out by hand."""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import stats
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    source = tmp_path / "projects"
    shutil.copytree(FIXTURES / "projects", source)
    ingest(source, tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    yield c
    c.close()


def test_totals(conn):
    t = stats.compute(conn, ZoneInfo("UTC"))["totals"]
    assert t["prompts"] == 10
    assert t["prompts_by_kind"] == {"typed": 7, "command": 1, "bash": 1, "suggestion": 1}
    assert t["sessions"] == 2 and t["projects"] == 2
    assert t["assistant_messages"] == 5 and t["tool_calls"] == 1


def test_models_and_effort(conn):
    s = stats.compute(conn, ZoneInfo("UTC"))
    m = s["models"]
    assert m["by_messages"] == {"claude-opus-5-5": 2, "claude-sonnet-5-5": 2, "claude-haiku-5-5": 1}
    assert m["main_model_per_session"]["claude-opus-5-5"] == 1  # sess-alpha
    assert m["switching_sessions"] == 2
    assert m["output_tokens"]["claude-opus-5-5"] == 85 + 20  # split message counted once
    assert s["effort"]["by_messages"] == {"high": 2, "medium": 2, "unset": 1}


def test_time_is_converted_to_local_zone(conn):
    utc = stats.compute(conn, ZoneInfo("UTC"))["time"]
    assert utc["by_hour"][23] == 6 and utc["by_hour"][14] == 3 and utc["by_hour"][15] == 1

    ny = stats.compute(conn, ZoneInfo("America/New_York"))["time"]
    # 23:30 UTC on 10 March 2026 is 19:30 in New York (daylight saving began 8 March).
    assert ny["by_hour"][19] == 6 and ny["by_hour"][10] == 3 and ny["by_hour"][11] == 1
    assert ny["peak_hour"] == 19
    assert ny["by_weekday"]["Tue"] == 6 and ny["by_weekday"]["Thu"] == 4
    assert ny["by_part_of_day"]["evening (18–24)"] == 6
    assert sum(map(sum, ny["weekday_hour_grid"])) == 10
    assert ny["active_days"] == 2


def test_sessions_count_only_active_time(conn):
    se = stats.compute(conn, ZoneInfo("UTC"))["sessions"]
    assert se["prompts_per_session"]["max"] == 6
    # alpha: 35 active minutes. beta: ~5 minutes, because the 85-minute pause doesn't count.
    assert se["active_minutes"]["max"] == 35.0
    assert se["active_minutes"]["median"] == pytest.approx(20.0, abs=0.1)
    assert se["with_subagents"] == 1


def test_lengths_commands_tools(conn):
    s = stats.compute(conn, ZoneInfo("UTC"))
    assert sum(s["prompt_length"]["buckets"].values()) == 7
    assert s["prompt_length"]["with_paste"] == 1 and s["prompt_length"]["with_image"] == 1
    assert s["commands"] == {"/compact": 1}
    assert s["tools"] == {"Read": 1}
    assert s["surfaces"] == {"cli": 6, "claude-desktop": 4}


def test_report_renders(conn):
    text = stats.render(stats.compute(conn, ZoneInfo("UTC")))
    assert "POMPY" in text and "claude-opus-5-5" in text
