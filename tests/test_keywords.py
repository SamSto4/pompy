"""Step 5: keywords, themes, opening style and keyword search on the synthetic chats."""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import keywords, store
from pompy.ingest import ingest
from pompy.parse import Prompt, SessionInfo
from pompy.search import search, to_fts_query

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    source = tmp_path / "projects"
    shutil.copytree(FIXTURES / "projects", source)
    ingest(source, tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    yield c
    c.close()


@pytest.fixture
def kw(conn):
    return keywords.compute(conn, ZoneInfo("UTC"))


def _terms(result):
    return {row["term"]: row for row in result["top_terms"]}


def test_only_typed_prompts_in_own_words(kw):
    assert kw["typed_prompts"] == 7
    terms = _terms(kw)
    assert "keyerror" not in terms  # pasted traceback is not your vocabulary
    assert "compact" not in terms  # slash commands are not either


def test_ranked_by_how_many_prompts_mention_a_term(kw):
    top = kw["top_terms"][0]
    assert top["term"] == "password" and top["prompts"] == 2 and top["sessions"] == 1


def test_plurals_fold_and_paths_are_dropped(kw):
    terms = _terms(kw)
    assert terms["field"]["count"] == 2  # "fields" + "field"
    assert "fields" not in terms
    for path_bit in ("src", "theme", "css"):
        assert path_bit not in terms, path_bit
    assert terms["styles"]["count"] == 1  # the prose word counts; src/styles/theme.css doesn't
    assert (
        "login" in terms
    )  # the word in "login page" survives; the path /api/login doesn't add one
    assert terms["login"]["count"] == 1


def test_action_verbs_are_tracked_separately(kw):
    assert "build" not in _terms(kw) and "fix" not in _terms(kw)
    assert kw["action_verbs"]["opening"] == {"build": 1, "add": 1, "fix": 1, "make": 1}


def test_opening_style(kw):
    assert kw["opening_style"] == {"command": 4, "statement": 2, "question": 1}


def test_project_themes(kw):
    demo = next(p for p in kw["by_project"] if p["name"] == "demo-app")
    assert demo["prompts"] == 3
    assert {"password", "field"} <= set(demo["terms"])


def test_tokenizer_drops_code_urls_identifiers():
    text = 'Fix `renderPage()` see https://example.com/x and "startLineNumber": 35 in my_module'
    assert keywords.content_terms(text) == []
    assert keywords.content_terms("The checkout pages and the checkout page") == [
        "checkout",
        "pages",
        "checkout",
        "page",
    ]


def test_opening_style_examples():
    assert keywords.opening_style("ok now please fix the header") == "command"
    assert keywords.opening_style("can you look at the logs") == "request"
    assert keywords.opening_style("why is the build slow") == "question"
    assert keywords.opening_style("yes") == "reply"
    assert keywords.opening_style("the header overlaps the menu") == "statement"


def test_search_matches_stems_and_returns_source_refs(conn):
    [hit] = search(conn, "fixing bugs", tz=ZoneInfo("UTC"))
    assert hit["uuid"] == "b-u1" and hit["session_id"] == "sess-beta"
    assert "[fix]" in hit["snippet"] and "[bug]" in hit["snippet"]
    assert hit["local_time"] == "2026-04-02 14:00"


def test_search_finds_pasted_text_and_filters_by_project(conn):
    assert [h["uuid"] for h in search(conn, "KeyError")] == ["b-u3"]  # pasted text is searchable
    assert len(search(conn, "password")) == 2
    assert search(conn, "password", project="notes") == []


def test_search_is_safe_with_odd_input(conn):
    assert to_fts_query('login" OR 1=1; --') == '"login" "OR" "1 1"'
    assert search(conn, 'login"; DROP TABLE prompts; --') == []
    assert search(conn, "???") == []


# ── Regression tests for the 5 findings confirmed by the Step 5 review ──────────


def _add_prompt(conn, session_id, cwd, uuid, text):
    store.save(conn, SessionInfo(session_id=session_id, ts="2026-05-01T10:00:00Z", cwd=cwd))
    store.save(
        conn,
        Prompt(
            uuid=uuid,
            session_id=session_id,
            ts="2026-05-01T10:00:00Z",
            kind="typed",
            source="typed",
            text=text,
            own_text=text,
        ),
    )
    conn.commit()


def test_project_filter_prefers_exact_name_and_has_no_wildcards(conn):
    _add_prompt(conn, "sess-x", "/home/dev/demo-app-2", "x-1", "password reset flow")
    assert {h["uuid"] for h in search(conn, "password", project="demo-app")} == {"a-u1", "a-u17"}
    assert {h["uuid"] for h in search(conn, "password", project="DEMO-APP")} == {"a-u1", "a-u17"}
    assert len(search(conn, "password", project="demo")) == 3  # no exact match: substring
    assert search(conn, "password", project="%") == []  # % is not a wildcard
    assert search(conn, "password", project="demo_app") == []  # _ is not a wildcard


def test_contractions_and_hyphens_stay_one_phrase(conn):
    _add_prompt(conn, "sess-y", "/home/dev/demo-app", "y-1", "let's go do it")
    _add_prompt(conn, "sess-y", "/home/dev/demo-app", "y-2", "it's broken again")
    assert to_fts_query("it's co-author") == '"it s" "co author"'
    assert [h["uuid"] for h in search(conn, "it's")] == ["y-2"]


def test_phrases_count_prompts_not_repetitions(conn):
    _add_prompt(conn, "sess-z", "/home/dev/demo-app", "z-1", "nav bar nav bar nav bar nav bar")
    kw = keywords.compute(conn, ZoneInfo("UTC"))
    assert "nav bar" not in {p["phrase"] for p in kw["top_phrases"]}  # 1 prompt only
    _add_prompt(conn, "sess-z", "/home/dev/demo-app", "z-2", "the nav bar overlaps the logo")
    kw = keywords.compute(conn, ZoneInfo("UTC"))
    assert {"phrase": "nav bar", "prompts": 2} in kw["top_phrases"]


def test_opening_style_fixes_from_real_data():
    assert keywords.opening_style("ok let's make a plan") == "request"
    assert keywords.opening_style("npm run dev") == "command"
    assert keywords.opening_style('@"src/my app/page.tsx" fix this') == "command"
    assert keywords.opening_style("/compact") == "other"
    assert keywords.opening_style("https://example.com/x") == "other"
    assert keywords.opening_style("keep the header fixed") == "command"
    assert keywords.opening_style("approve") == "reply"


def test_dotted_acronyms_count_as_words():
    assert "ai" in keywords.content_terms("I love A.I. tools and the A.I agent")
    assert keywords.content_terms("A.I. tools").count("ai") == 1
    assert "ui" in keywords.content_terms("polish the U.I")
    assert "eg" not in keywords.content_terms("e.g. this example")
    assert keywords.content_terms("see src/a.i/file.py") == []  # still a path, not a word
