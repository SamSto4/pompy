"""Step 8: `pompy publish` builds the dashboard page + data bundle, and only safe text leaves."""

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import publish, store
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))  # no stray working_set.json
    source = tmp_path / "projects"
    shutil.copytree(FIXTURES / "projects", source)
    ingest(source, tmp_path / "data")
    conn = sqlite3.connect(tmp_path / "data" / "pompy.db")
    out = tmp_path / "out" / "dashboard"
    publish.build(conn, out, ZoneInfo("UTC"))
    conn.close()
    return out, json.loads((out / "data.json").read_text())


def test_build_writes_the_page_and_its_data(built):
    out, data = built
    names = {p.name for p in out.iterdir()}
    assert {
        "index.html",
        "scorer.js",
        "app-core.js",
        "app-tabs.js",
        "app-lab.js",
        "app-profile.js",
        "data.json",
    } <= names
    assert data["meta"]["prompts"] == 10 and data["meta"]["rubric_version"] == 1
    assert {"stats", "keywords", "score", "habits", "rules", "prompts"} <= set(data)


def test_every_per_prompt_fact_is_decided_in_python(built):
    _, data = built
    rows = {r["u"]: r for r in data["prompts"]}
    a1 = rows["a-u1"]
    assert a1["d"] == "2026-03-10" and a1["h"] == 23 and a1["o"] is True  # opened its chat
    assert a1["r"] == "task" and a1["s"] == 5
    assert set(a1["fr"]) == {"correction", "interrupt", "rejected"}
    assert rows["b-u1"]["fr"] == ["clarify"] and rows["b-u1"]["q"] == "Which bug do you mean?"
    assert rows["a-u11"]["k"] == "command" and rows["a-u11"]["r"] is None


def test_only_your_own_words_leave_the_database(built):
    _, data = built
    text = json.dumps(data)
    assert "Traceback" not in text  # b-u3's pasted error stays out: only own words are bundled
    assert "sess-alpha" not in text and "sess-beta" not in text  # chats are numbered instead
    assert "/home/dev" not in text  # no working folders


def test_habits_cite_real_prompts(built):
    _, data = built
    uuids = {r["u"] for r in data["prompts"]}
    assert len(data["habits"]) == 3
    for hb in data["habits"]:
        assert hb["feature"] != "goal" and set(hb["examples"]) <= uuids


def test_template_files_hold_no_data():
    for f in publish.TEMPLATE_DIR.iterdir():
        text = f.read_text()
        assert "a-u1" not in text and "demo-app" not in text, f.name


@pytest.mark.parametrize(
    "raw, cleaned",
    [
        (
            "export ANTHROPIC_API_KEY = sk-ant-abcdefghijklmnop123",
            "export ANTHROPIC_API_KEY = [key]",
        ),
        ("password: hunter2hunter2", "password: [redacted]"),
        ("Authorization: Bearer abcdefghijklmnop1234", "Authorization: Bearer [redacted]"),
        ("mail me at someone@example.com", "mail me at [email]"),
        ("open /Users/someone/Desktop/app", "open ~/Desktop/app"),
        ("keep token usage low", "keep token usage low"),  # a word, not a secret
        ("rotate the token efficiently", "rotate the token efficiently"),
    ],
)
def test_redaction(raw, cleaned):
    assert publish.redact(raw) == cleaned


def test_snapshot_date_is_local(tmp_path):
    conn = store.connect(tmp_path / "x.db")
    now = datetime(2026, 11, 30, 20, 0, tzinfo=UTC)  # already 1 December in India
    data = publish.build_bundle(conn, ZoneInfo("Asia/Kolkata"), now=now)
    assert data["meta"]["today"] == "2026-12-01"


def test_prompts_carry_meaning_neighbours_when_embeddings_exist(tmp_path, monkeypatch):
    from fakes import FakeEmbedder

    from pompy import embed, excerpts

    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    conn = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(conn, ZoneInfo("UTC"))
    embed.update(conn, FakeEmbedder())
    data = publish.build_bundle(conn, ZoneInfo("UTC"))
    conn.close()
    rows = {r["u"]: r for r in data["prompts"]}
    with_nb = [r for r in rows.values() if r.get("nb")]
    assert with_nb and all(r["r"] == "task" for r in with_nb)
    for r in with_nb:
        assert r["u"] not in r["nb"] and all(rows[u]["r"] == "task" for u in r["nb"])
