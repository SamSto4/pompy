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
        "clarify": False,
        "any": True,
    }
    assert fr["b-u1"]["clarify"]  # "fix the bug" → Claude: "Which bug do you mean?"
    assert not fr["a-u17"]["any"]  # Claude did it, then offered a next step: not friction


# ── Clarifying questions: Claude had to ask before it could do the job ─────


@pytest.mark.parametrize(
    "reply",
    [
        "Which bug do you mean?",
        "There are two clear buttons in the app.\n\n```tsx\n<Clear />\n```\n\nWhich one?",
        "I can do this a few ways. What are we working with?",
        "If so, could you paste the actual output (or just the URL line)?",
        "Sure, what would you like to know?",
        "1. **Scope**: landing page only?\n2. **Brand**: which name goes in the header?",
    ],
)
def test_clarifying_replies(reply):
    assert score.is_clarifying(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "Added the link. Want me to add a test for it too?",
        "Should I merge Steps 2 and 3 and start building, or keep them separate?",
        "Done. Would you like me to commit?",
        "Everything passes. Ready to push?",
        "I fixed the nav bar. It now collapses on mobile.",
        "Is this the right file? It's in src/app.",  # ends on a statement
    ],
)
def test_offers_and_statements_are_not_clarifying(reply):
    assert not score.is_clarifying(reply)


def _session(conn, prompt, replies=(), tools=()):
    """Add one synthetic exchange: a typed prompt, then Claude's replies and tool calls."""
    conn.execute(
        "INSERT INTO prompts (uuid, session_id, ts, kind, source, text, own_text) "
        "VALUES ('x-u1', 'sess-x', '2026-05-01T10:00:00Z', 'typed', 'typed', ?, ?)",
        (prompt, prompt),
    )
    for i, text in enumerate(replies):
        conn.execute(
            "INSERT INTO replies VALUES (?, 'msg_x', 'sess-x', ?, ?)",
            (f"x-a{i}", f"2026-05-01T10:00:0{i + 1}Z", text),
        )
    for i, name in enumerate(tools):
        conn.execute(
            "INSERT INTO tool_uses VALUES (?, 'msg_x', 'sess-x', '2026-05-01T10:00:00Z', ?)",
            (f"x-t{i}", name),
        )


def test_ask_user_question_tool_is_clarify(conn):
    _session(conn, "redesign the homepage", tools=["Read", "AskUserQuestion"])
    assert score.friction(conn)["x-u1"]["clarify"]


def test_question_after_changing_files_is_not_clarify(conn):
    _session(conn, "fix the login", ["Fixed it. Which browser were you using?"], ["Edit"])
    assert not score.friction(conn)["x-u1"]["clarify"]


def test_only_the_last_reply_counts(conn):
    _session(conn, "fix the login", ["Which file? Never mind, found it.", "Done: login works."])
    assert not score.friction(conn)["x-u1"]["clarify"]


def test_questions_you_asked_for_are_not_friction(conn):
    _session(conn, "plan the app, ask me any questions first", tools=["AskUserQuestion"])
    assert not score.friction(conn)["x-u1"]["clarify"]


def test_compute_scores_and_persists(conn):
    s = score.compute(conn, ZoneInfo("UTC"))
    assert s["typed_prompts"] == 7
    assert s["roles"] == {"task": 6, "correction": 1}
    assert s["score"]["distribution"][:6] == [1, 2, 1, 1, 0, 1]  # scores 5,1,1,3,2,0
    assert s["score"]["mean"] == 2.0
    # a-u1 (correction, interrupt, rejected); b-u1 and b-u8 (Claude had to ask)
    assert s["friction"]["overall"]["friction"] == 3 and s["friction"]["overall"]["n"] == 6
    assert s["examples"][0]["uuid"] == "b-u8"  # lowest score first: "hello there, quick question…"
    rows = conn.execute("SELECT COUNT(*), SUM(friction) FROM prompt_scores").fetchone()
    assert tuple(rows) == (7, 3)


def test_rescoring_replaces_rows(conn):
    score.compute(conn, ZoneInfo("UTC"))
    score.compute(conn, ZoneInfo("UTC"))
    assert conn.execute("SELECT COUNT(*) FROM prompt_scores").fetchone()[0] == 7


def test_check_output():
    text = score.render_check("fix the bug", score.score_text("fix the bug"))
    assert "1/7" in text and "Consider adding" in text
    assert "isn't scored" in score.render_check("yes", score.score_text("yes"))
