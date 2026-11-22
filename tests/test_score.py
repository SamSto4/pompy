"""Step 6: specificity rubric, roles, friction signals and the score analysis."""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import score
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


# ── Rubric features: one positive and one negative example each ─────────────


@pytest.mark.parametrize(
    "feature, present, absent",
    [
        ("goal", "add a reset link to the login form", "the header"),
        ("context", "it's for the checkout page because users drop off", "add a footer"),
        ("constraints", "don't change the colors and keep the layout", "add a footer"),
        ("done", "done when the tests pass", "add a footer"),
        ("format", "summarize it as a bullet list", "add a footer"),
        ("example", "make it look like the pricing page", "add a footer"),
        ("reference", "fix the crash in src/app/page.tsx", "fix the crash"),
    ],
)
def test_each_feature(feature, present, absent):
    assert score.detect(present)[feature] is True
    assert score.detect(absent)[feature] is False


def test_paste_and_image_count_as_context_reference_example():
    f = score.detect("why does this happen", has_paste=True, has_image=True)
    assert f["context"] and f["reference"] and f["example"]


def test_a_well_specified_prompt():
    text = (
        "Build a login page for the demo app using React. It should have email and password "
        "fields and match the styles in src/styles/theme.css. Done means the tests pass."
    )
    result = score.score_text(text)
    assert result["role"] == "task"
    assert result["score"] == 5
    assert result["missing"] == ["format", "example"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("yes", "reply"),
        ("ok go", "reply"),
        ("try again", "correction"),
        ("no, use the other button", "correction"),
        ("commit and push", "routine"),
        ("archive this session", "routine"),
        ("start step 2", "routine"),
        ("/compact", "other"),
        ("fix the login bug", "task"),
    ],
)
def test_roles(text, expected):
    assert score.role(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "why am I getting a 404 on my dashboard",
        "xcode isnt responding when I open it",
        "I think the app crashed",
        "can you tell me whats wrong",
        "I thought we removed that",
    ],
)
def test_problem_reports_from_real_follow_ups(text):
    assert score._PROBLEM_RE.search(text)


@pytest.mark.parametrize(
    "text", ["do not commit the new voice yet", "add a not-found page", "why does this happen"]
)
def test_not_every_negative_is_a_problem(text):
    assert not score._PROBLEM_RE.search(text)


# ── Friction + analysis on the synthetic chats ───────────────────────────────


def test_friction_after_the_login_prompt(conn):
    fr = score.friction(conn)
    # a-u1 → tool rejected + interrupt, then "no, use the existing Button…" (a correction)
    assert fr["a-u1"] == {
        "correction": True,
        "problem": False,
        "interrupt": True,
        "rejected": True,
        "rephrase": False,
        "any": True,
    }
    assert not fr["b-u1"]["any"]  # "fix the bug" → next prompt just pastes the error
    assert not fr["a-u17"]["any"]  # last prompt of the session


def test_compute_scores_and_persists(conn):
    s = score.compute(conn, ZoneInfo("UTC"))
    assert s["typed_prompts"] == 7
    assert s["roles"] == {"task": 6, "correction": 1}
    assert s["score"]["distribution"][:6] == [1, 2, 1, 1, 0, 1]  # scores 5,1,1,3,2,0
    assert s["score"]["mean"] == 2.0
    assert s["friction"]["overall"]["friction"] == 1 and s["friction"]["overall"]["n"] == 6
    assert s["examples"][0]["uuid"] == "a-u1"
    rows = conn.execute("SELECT COUNT(*), SUM(friction) FROM prompt_scores").fetchone()
    assert tuple(rows) == (7, 1)


def test_rescoring_replaces_rows(conn):
    score.compute(conn, ZoneInfo("UTC"))
    score.compute(conn, ZoneInfo("UTC"))
    assert conn.execute("SELECT COUNT(*) FROM prompt_scores").fetchone()[0] == 7


def test_check_output():
    text = score.render_check("fix the bug", score.score_text("fix the bug"))
    assert "1/7" in text and "Consider adding" in text
    assert "isn't scored" in score.render_check("yes", score.score_text("yes"))
