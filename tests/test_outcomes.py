"""Was the reply useful? Verdicts, causes and advice (the dashboard redesign)."""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import excerpts, outcomes, publish, score, similar
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    score.compute(c)
    excerpts.build(c, ZoneInfo("UTC"))
    yield c
    c.close()


def test_each_task_prompt_gets_a_verdict_from_your_reaction(conn):
    v = outcomes.judge(conn)
    assert v["a-u1"] == "missed"  # you rejected, interrupted, then corrected it
    assert v["b-u1"] == "unclear"  # "fix the bug" → "Which bug do you mean?"
    assert v["a-u17"] == "no reaction"  # the chat ended there
    assert v["b-u4"] == "moved on"
    assert "a-u9" not in v  # a correction is not itself a task prompt


@pytest.mark.parametrize("reply", ["perfect, thanks", "works now!", "yes", "ok commit and push it"])
def test_praise_or_shipping_counts_as_useful(reply):
    assert outcomes.POSITIVE_RE.search(reply)


@pytest.mark.parametrize("reply", ["now add a footer", "the button is still blue", "no"])
def test_other_replies_are_not_praise(reply):
    assert not outcomes.POSITIVE_RE.search(reply)


def test_a_go_ahead_question_is_not_confusion():
    assert outcomes.PERMISSION_RE.search("Should I start Step 4, or hold off for now?")
    assert not outcomes.PERMISSION_RE.search("Which bug do you mean?")


def test_an_unclear_reply_is_traced_to_the_part_the_question_points_at():
    steps = outcomes.cause("unclear", ["clarify"], ["reference", "done"], "Which file is it in?")
    assert steps[0] == 'Claude had to ask: "Which file is it in?"'
    assert steps[1] == "The prompt never said which file, page or error to look at."
    assert outcomes.lacked_part(steps) == "reference"


def test_a_reported_problem_is_traced_to_a_missing_check():
    steps = outcomes.cause("missed", ["problem"], ["done", "constraints"], None, "still a 404")
    assert outcomes.lacked_part(steps) == "done"


def test_saying_what_you_want_instead_is_a_correction_not_a_bug():
    steps = outcomes.cause("missed", ["problem"], ["done", "constraints"], None,
                           "I want this to look like the pricing page")  # fmt: skip
    assert steps[0].startswith("You had to correct it")
    assert outcomes.lacked_part(steps) == "constraints"


def test_useful_replies_have_no_cause():
    assert outcomes.cause("useful", [], ["done"]) is None
    assert outcomes.cause("moved on", [], ["done"]) is None


def test_the_bundle_carries_verdicts_causes_and_the_evidence(conn):
    rows = {r["u"]: r for r in publish._prompt_rows(conn, ZoneInfo("UTC"))}
    bug = rows["b-u1"]
    assert bug["v"] == "unclear" and bug["c"] and bug["lk"] == "reference"
    assert bug["rp"]  # Claude's reply, to read the evidence
    login = rows["a-u1"]
    assert login["v"] == "missed" and login["nx"].startswith("no, use the existing Button")
    assert "c" not in rows["b-u4"]  # moved on: nothing to explain


def test_advice_names_what_similar_misses_lacked_when_this_prompt_lacks_it_too(conn):
    advice, why = similar.from_misses(conn, "fix the crash", ["b-u1"])
    assert why["b-u1"]["part"] == "reference"
    assert advice and advice["part"] == "reference" and "name the file" in advice["text"]
    exact = "fix the crash in src/api/notes.ts, the error is 'TypeError: x is undefined'"
    assert similar.from_misses(conn, exact, ["b-u1"])[0] is None  # this prompt already names it
    assert similar.from_misses(conn, "yes", ["b-u1"])[0] is None  # not a task prompt


def test_a_misses_tip_is_measured_by_the_part_it_asks_for():
    from pompy import advice

    assert advice.asks("misses:reference") == "reference"
    assert advice.asks("misses:nonsense") is None


def test_similar_offers_the_misses_tip_to_the_holdout(conn):
    result = similar.similar(conn, "fix the crash", embedder=None, tz=ZoneInfo("UTC"))
    ids = [t["id"] for t in result["tips"]]
    if result["advice"]:
        assert f"misses:{result['advice']['part']}" in ids
    hidden = similar.advise(result, "f" * 16, {"tips": {}, "quiet": []})
    assert hidden["advice"] is None or f"misses:{result['advice']['part']}" in hidden["shown"]
