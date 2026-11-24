"""Step 18: the hypothesis agent, driven by a scripted fake client (no API calls in tests)."""

import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from pompy import excerpts, patterns, think
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, ZoneInfo("UTC"))
    yield c
    c.close()


def _use(i, name, **inp):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp)


def _resp(*blocks, stop="tool_use"):
    usage = NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=800,
               cache_creation_input_tokens=0)  # fmt: skip
    return NS(content=list(blocks), stop_reason=stop, usage=usage)


class FakeClient:
    """Plays back scripted responses and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        return self.script.pop(0)


GOOD = dict(
    statement="Follow-ups that assume Claude remembers an earlier detail go wrong",
    scope="all",
    rationale="Each supporting prompt refers back without restating.",
    supports=["a-u1", "a-u9", "b-u3"],
    contradicts=["b-u1"],
)


def _script():
    return [
        _resp(
            *[_use(i, "get_excerpt", id=u) for i, u in enumerate(["a-u1", "a-u9", "b-u3", "b-u1"])]
        ),
        _resp(_use(9, "propose_pattern", **{**GOOD, "supports": ["a-u1", "a-u9", "zz-invented"]})),
        _resp(_use(10, "propose_pattern", **GOOD)),
        _resp(NS(type="text", text="Stored one hypothesis."), stop="end_turn"),
    ]


def test_the_agent_stores_a_hypothesis_with_evidence_on_both_sides(conn):
    client = FakeClient(_script())
    run = think.think(conn, client, embedder=None)
    assert run.stopped == "done" and run.summary == "Stored one hypothesis."
    [p] = [p for p in patterns.load(conn) if p["source"] == "agent"]
    assert p["status"] == "hypothesis"  # the LLM proposes; statistics decide
    assert {e["prompt_uuid"] for e in p["supports"]} == {"a-u1", "a-u9", "b-u3"}
    assert [e["prompt_uuid"] for e in p["contradicts"]] == ["b-u1"]


def test_invented_evidence_is_refused(conn):
    client = FakeClient(_script())
    think.think(conn, client, embedder=None)
    results = client.requests[2]["messages"][-1]["content"]
    assert results[0]["is_error"] and "zz-invented" in results[0]["content"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"supports": ["a-u1", "a-u9"]}, "at least 3 supporting"),
        ({"contradicts": []}, "1 contradicting"),
        ({"contradicts": ["a-u1"]}, "both support and contradict"),
    ],
)
def test_proposals_need_evidence_on_both_sides(conn, change, message):
    run = think.Run(seen={"a-u1", "a-u9", "b-u3", "b-u1"})
    text, is_error = think.run_tool(conn, None, run, "propose_pattern", {**GOOD, **change})
    assert is_error and message in text


def test_instructions_and_tools_are_cached_and_refusals_fall_back(conn):
    client = FakeClient(_script())
    think.think(conn, client, embedder=None)
    first = client.requests[0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["fallbacks"] == "default" and first["model"] == "claude-opus-5-5"
    assert first["messages"][0]["content"].startswith("Briefing:")
    assert all(t["strict"] for t in first["tools"])


def test_usage_adds_up_and_costs_are_estimated(conn):
    run = think.think(conn, FakeClient(_script()), embedder=None)
    assert run.usage == {"input": 4000, "output": 800, "cache_read": 3200, "cache_write": 0}
    assert think.cost(run, "claude-opus-5-5") == pytest.approx(
        (4000 * 4 + 800 * 20 + 3200 * 0.2) / 1e6, abs=1e-4
    )
    text = think.render(run, "claude-opus-5-5")
    assert "4 excerpts sent to the API" in text and "Stored 1 hypothesis" in text


def test_a_refusal_or_the_turn_limit_stops_the_loop(conn, monkeypatch):
    refused = think.think(conn, FakeClient([_resp(stop="refusal")]), embedder=None)
    assert refused.stopped == "refused"
    monkeypatch.setattr(think, "MAX_TURNS", 2)
    looping = [_resp(_use(i, "list_patterns")) for i in range(3)]
    assert think.think(conn, FakeClient(looping), embedder=None).stopped == "turn limit"


def test_the_env_file_is_read_without_overriding_the_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(think.config, "REPO_ROOT", tmp_path)
    (tmp_path / ".env").write_text('# comment\nPOMPY_TEST_KEY="from-file"\nPOMPY_OTHER=x\n')
    monkeypatch.setenv("POMPY_OTHER", "already-set")
    monkeypatch.delenv("POMPY_TEST_KEY", raising=False)
    think.load_env()
    import os

    assert os.environ["POMPY_TEST_KEY"] == "from-file"
    assert os.environ["POMPY_OTHER"] == "already-set"
