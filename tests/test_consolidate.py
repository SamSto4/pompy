"""Step 19: project facts, consolidation, and forgetting (a fixture timeline, no API calls)."""

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from pompy import consolidate, episodes, excerpts, facts, mine, patterns
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"
LAST_ACTIVE = datetime(2026, 4, 2, 15, 30, tzinfo=UTC)  # the fixtures' newest exchange


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))
    monkeypatch.setattr(facts, "MIN_CHATS", 1)  # the fixtures hold one chat per project
    monkeypatch.setattr(facts, "MIN_EXCHANGES", 1)
    monkeypatch.setattr(mine, "MIN_GROUP", 1)
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, ZoneInfo("UTC"))
    episodes.build(c, ZoneInfo("UTC"))
    yield c
    c.close()


class FakeClient:
    def __init__(self, answers):
        self.answers = answers  # project name -> facts dict
        self.sent = []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.sent.append(kw)
        name = kw["messages"][0]["content"].split("\n")[0].split(": ", 1)[1].split(" (")[0]
        text = json.dumps(self.answers[name])
        return NS(content=[NS(type="text", text=text)], stop_reason="end_turn",
                  usage=NS(input_tokens=100, output_tokens=40))  # fmt: skip


ANSWERS = {
    "demo-app": {"goal": "A React demo app with a login page.", "evidence_chats": [1],
                 "preferences": ["Reuse existing components", "Match the theme styles"]},
    "notes-api": {"goal": "An API for notes with pagination.", "evidence_chats": [1],
                  "preferences": []},
}  # fmt: skip


def _by_key(conn, now):
    return {p["key"]: p for p in patterns.load(conn, now=now)}


def test_project_facts_are_stored_with_the_chats_they_came_from(conn):
    client = FakeClient(ANSWERS)
    result = facts.refresh(conn, client, now=LAST_ACTIVE)
    assert {p["project"] for p in result["projects"]} == {"demo-app", "notes-api"}
    held = _by_key(conn, LAST_ACTIVE)
    goal = next(p for k, p in held.items() if k.startswith("goal@") and "React" in p["statement"])
    assert goal["kind"] == "goal" and goal["status"] == "supported"
    assert goal["scope"] == "project: demo-app"
    assert [e["prompt_uuid"] for e in goal["supports"]] == ["a-u1"]  # the chat's first exchange
    prefs = [p for k, p in held.items() if k.startswith("pref@")]
    assert sorted(p["statement"] for p in prefs) == [
        "Match the theme styles",
        "Reuse existing components",
    ]
    assert client.sent[0]["output_config"]["format"]["type"] == "json_schema"
    assert client.sent[0]["model"] == "claude-haiku-5-5"


def test_briefs_never_send_home_paths_keys_or_emails():
    project = {"name": "x", "chats": 1, "cards": [{
        "local_start": "2026-01-01 10:00", "title": None, "exchanges": 1, "outcome": "talk only",
        "intent": "open /Users/someone/app and use sk-ant-abcdefghijklmnop123, mail a@b.com",
    }]}  # fmt: skip
    text = facts.brief(project)
    assert "/Users/someone" not in text and "sk-ant-" not in text and "a@b.com" not in text


def test_a_preference_that_stops_recurring_is_retired(conn):
    facts.refresh(conn, FakeClient(ANSWERS), now=LAST_ACTIVE)
    fewer = {
        **ANSWERS,
        "demo-app": {**ANSWERS["demo-app"], "preferences": ["Reuse existing components"]},
    }
    facts.refresh(conn, FakeClient(fewer), now=LAST_ACTIVE + timedelta(days=1))
    prefs = {p["statement"]: p["status"] for k, p in _by_key(conn, LAST_ACTIVE).items()
             if k.startswith("pref@")}  # fmt: skip
    assert prefs["Reuse existing components"] == "supported"
    assert list(prefs.values()).count("retired") == 1


def test_a_project_you_stop_working_on_fades_and_is_forgotten(conn):
    facts.refresh(conn, FakeClient(ANSWERS), now=LAST_ACTIVE)
    soon = _by_key(conn, LAST_ACTIVE + timedelta(days=30))
    later = _by_key(conn, LAST_ACTIVE + timedelta(days=180))
    goal = next(k for k in soon if k.startswith("goal@"))
    assert later[goal]["confidence_now"] < soon[goal]["confidence_now"]  # fading
    retired = patterns.retire_stale(conn, LAST_ACTIVE + timedelta(days=400))
    assert goal in retired


def test_old_agent_hypotheses_are_retired_unless_rechecked(conn):
    stats = {"effect": 0.0, "n_a": 3, "n_b": 1, "p": 1.0, "q": 1.0, "rate_a": 0, "rate_b": 0}
    patterns.record(conn, key="agent:x", statement="x", scope="all", kind="habit",
                    source="agent", stats=stats, supports=["a-u1"], contradicts=["b-u1"],
                    now=LAST_ACTIVE)  # fmt: skip
    assert patterns.retire_unconfirmed_hypotheses(conn, 180, LAST_ACTIVE + timedelta(days=90)) == []
    assert patterns.retire_unconfirmed_hypotheses(conn, 180, LAST_ACTIVE + timedelta(days=200)) == [
        "agent:x"
    ]


def test_consolidation_reports_new_then_nothing_then_forgetting(conn):
    first = consolidate.consolidate(conn, ZoneInfo("UTC"), FakeClient(ANSWERS), now=LAST_ACTIVE)
    assert first["changes"]["new"] and first["facts"]["projects"]
    again = consolidate.consolidate(conn, ZoneInfo("UTC"), now=LAST_ACTIVE + timedelta(hours=1))
    assert not any(again["changes"].values())
    assert "Nothing changed" in consolidate.render(again)
    much_later = consolidate.consolidate(
        conn, ZoneInfo("UTC"), now=LAST_ACTIVE + timedelta(days=400)
    )
    retired = {r["key"] for r in much_later["changes"]["retired"]}
    assert any(k.startswith("goal@") for k in retired)
    assert "Retired" in consolidate.render(much_later)
