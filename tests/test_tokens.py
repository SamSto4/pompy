"""Token usage: exact sums of the API's own numbers, credited to the prompt that caused them."""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import tokens
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


def test_totals_add_up_every_way(conn):
    t = tokens.compute(conn, ZoneInfo("UTC"))
    total = t["total"]["total"]
    assert total == sum(t["total"][k] for k in tokens.KINDS) > 0
    assert sum(d["total"] for d in t["by_day"].values()) == total
    assert sum(p["total"] for p in t["by_project"].values()) == total
    raw = conn.execute(
        "SELECT SUM(input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens) "
        "FROM turns WHERE ts IS NOT NULL"
    ).fetchone()[0]
    assert total == raw  # nothing estimated: the stored API numbers, summed


def test_each_reply_is_credited_to_one_prompt(conn):
    t = tokens.compute(conn, ZoneInfo("UTC"))
    credited = sum(t["per_prompt"].values())
    assert credited <= t["total"]["total"]
    if t["unattributed_replies"] == 0:
        assert credited == t["total"]["total"]


def test_chats_and_positions_are_shaped_for_the_dashboard(conn):
    t = tokens.compute(conn, ZoneInfo("UTC"))
    assert t["top_chats"] and all(c["si"] for c in t["top_chats"])
    assert [b["label"] for b in t["by_position"]] == ["1–5", "6–15", "16–40", "41+"]
    assert 0 <= t["reread_pct"] <= 100
