"""Phase 4 (Steps 21-24): the advice log, shown vs held back, procedural memory, project tips."""

import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pompy import advice, excerpts, facts, patterns, score, similar, store
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
NAMES = [f.name for f in score.FEATURES]


# ── A small synthetic memory: chats of task prompts with known parts and outcomes ──


@pytest.fixture
def db(tmp_path):
    conn = store.connect(tmp_path / "pompy.db")
    conn.executescript(store.PROMPT_SCORES_SCHEMA)
    yield conn
    conn.close()


def _session(conn, sid, cwd="/home/dev/demo-app"):
    conn.execute(
        "INSERT OR IGNORE INTO sessions (session_id, project_key, project_name, cwd) "
        "VALUES (?,?,?,?)",
        (sid, store.project_key(cwd), cwd.rsplit("/", 1)[-1], cwd),
    )


def _prompt(conn, uuid, sid, at, text, parts=(), wrong=False, role="task"):
    _session(conn, sid)
    conn.execute(
        "INSERT INTO prompts (uuid, session_id, ts, kind, text, own_text, word_len) "
        "VALUES (?,?,?,?,?,?,?)",
        (uuid, sid, at.isoformat().replace("+00:00", "Z"), "typed", text, text,
         len(text.split())),
    )  # fmt: skip
    conn.execute(
        "INSERT INTO prompt_scores (uuid, session_id, ts, role, score, friction, "
        + ", ".join(f"f_{n}" for n in NAMES)
        + ") VALUES (?,?,?,?,?,?,"
        + ",".join("?" * len(NAMES))
        + ")",
        (
            uuid,
            sid,
            at.isoformat(),
            role,
            len(parts),
            int(wrong),
            *(int(n in parts) for n in NAMES),
        ),
    )


def _chat(conn, i, shown, follow_parts, follow_wrong):
    """One chat: a tipped prompt, then a follow-up two minutes later. Returns its log line."""
    sid, at = f"s{i}", T0 + timedelta(hours=i)
    text = f"fix the checkout page number {i}"
    _prompt(conn, f"p{i}", sid, at, text, parts=("goal",))
    _prompt(conn, f"n{i}", sid, at + timedelta(minutes=2), f"now the cart {i} please",
            parts=follow_parts, wrong=follow_wrong)  # fmt: skip
    return {"at": at.timestamp() + 5, "digest": advice.digest(text),
            "tips": ["lesson:problem"], "shown": ["lesson:problem"] if shown else [],
            "held_back": not shown, "quiet": []}  # fmt: skip


def test_a_fingerprint_ignores_spacing_and_never_holds_the_words():
    d = advice.digest("fix  the\nnav bar")
    assert d == advice.digest("fix the nav bar") and len(d) == 16 and "nav" not in d


def test_the_holdout_is_about_one_in_five_and_stable_per_prompt():
    prints = [advice.digest(f"prompt {i}") for i in range(2000)]
    share = sum(advice.held_back(p, 0.2) for p in prints) / len(prints)
    assert 0.16 < share < 0.24
    assert all(advice.held_back(p, 0.2) == advice.held_back(p, 0.2) for p in prints[:50])
    assert not any(advice.held_back(p, 0.0) for p in prints)


def test_a_held_back_prompt_shows_no_tip(monkeypatch):
    monkeypatch.setenv("POMPY_HOLDOUT", "1")
    d = advice.decide(["lesson:problem"], advice.digest("x"), None)
    assert d == {"shown": [], "quiet": [], "held_back": True}


def test_procedural_memory_orders_tips_and_quietens_ignored_ones(monkeypatch):
    monkeypatch.setenv("POMPY_HOLDOUT", "0")
    prof = {"tips": {"lesson:problem": {"status": "quiet", "weight": -0.3},
                     "project:done": {"status": "works", "weight": 0.4}}}  # fmt: skip
    fingerprint = next(f for f in (advice.digest(str(i)) for i in range(50))
                       if not advice._retry(f))  # fmt: skip
    d = advice.decide(["lesson:problem", "learned:x", "project:done"], fingerprint, prof)
    assert d["shown"] == ["project:done", "learned:x"] and d["quiet"] == ["lesson:problem"]
    retry = next(f for f in (advice.digest(str(i)) for i in range(50)) if advice._retry(f))
    assert "lesson:problem" in advice.decide(["lesson:problem"], retry, prof)["shown"]


@pytest.mark.parametrize(
    ("tip", "after", "expected"),
    [
        ("lesson:problem", {"done"}, True),
        ("lesson:problem", {"goal"}, False),
        ("lesson:clarify", {"reference"}, True),
        ("learned:lacks:context@all", {"context"}, True),
        ("project:constraints", {"constraints"}, True),
        ("learned:late-night@all", {"goal"}, None),  # can't be seen in a prompt
        ("project:reminder", {"goal"}, None),
    ],
)
def test_acting_on_a_tip_means_the_next_prompt_has_what_it_asked_for(tip, after, expected):
    before = {"score": 1, "words": 4, "features": {"goal": True}}
    nxt = {"score": len(after), "words": 8, "features": {n: n in after for n in NAMES}}
    assert advice.acted(tip, before, nxt) is expected


def test_impact_compares_shown_with_held_back_and_learns_the_tip_works(db):
    rows = [_chat(db, i, shown=True, follow_parts=("goal", "done") if i % 5 else ("goal",),
                  follow_wrong=i % 4 == 0) for i in range(10)]  # fmt: skip
    rows += [_chat(db, i, shown=False, follow_parts=("goal", "done") if i % 5 == 0 else ("goal",),
                   follow_wrong=True) for i in range(10, 20)]  # fmt: skip
    rows.append({"at": T0.timestamp(), "digest": advice.digest("typed in a terminal"),
                 "tips": ["lesson:problem"], "shown": ["lesson:problem"]})  # fmt: skip
    db.commit()
    r = advice.impact(db, rows, now=(T0 + timedelta(days=2)).timestamp())
    assert r["logged"] == 21 and r["linked"] == 20 and r["held_back"] == 10
    t = r["tips"]["lesson:problem"]
    assert (t["shown"], t["shown_acted"], t["held"], t["held_acted"]) == (10, 8, 10, 2)
    assert t["acted_rate"] == 0.8 and t["baseline_rate"] == 0.2 and t["status"] == "works"
    assert t["weight"] > 0.4
    o = r["overall"]
    assert o["shown"]["went_wrong"] == 0.3 and o["held"]["went_wrong"] == 1.0
    assert o["effect"] == -0.7 and o["p"] < 0.05
    text = advice.render(r)
    assert "acted on 80%" in text and "without it 20%" in text and "a real difference" in text


def test_a_tip_you_act_on_no_more_than_without_it_goes_quiet(db):
    rows = [_chat(db, i, shown=i < 10, follow_parts=("goal", "done") if i % 2 else ("goal",),
                  follow_wrong=False) for i in range(20)]  # fmt: skip
    db.commit()
    r = advice.impact(db, rows, now=(T0 + timedelta(days=2)).timestamp())
    assert r["tips"]["lesson:problem"]["status"] == "quiet"
    prof = advice.profile(r)
    assert prof["tips"]["lesson:problem"]["status"] == "quiet"


def test_old_tips_fall_out_of_procedural_memory(db):
    rows = [_chat(db, i, shown=True, follow_parts=("goal",), follow_wrong=False)
            for i in range(10)]  # fmt: skip
    db.commit()
    now = (T0 + timedelta(days=advice.WINDOW_DAYS + 5)).timestamp()
    t = advice.impact(db, rows, now=now)["tips"]["lesson:problem"]
    assert t["shown"] == 10 and t["status"] == "learning"  # all-time counts kept, judged afresh


def test_a_follow_up_must_be_in_the_same_chat_and_soon(db):
    _prompt(db, "a", "s1", T0, "fix the login page")
    _prompt(db, "b", "s1", T0 + timedelta(hours=2), "and the footer", parts=("done",))
    db.commit()
    row = {"at": T0.timestamp(), "digest": advice.digest("fix the login page"),
           "tips": ["lesson:problem"], "shown": ["lesson:problem"]}  # fmt: skip
    assert advice.link(db, [row])[0]["next"] is None


def test_the_trend_compares_the_month_before_advice_with_since(db):
    _prompt(db, "old", "s0", T0 - timedelta(days=3), "old one", parts=(), wrong=True)
    rows = [_chat(db, 1, shown=True, follow_parts=("goal", "done"), follow_wrong=False)]
    db.commit()
    tr = advice.trend(db, rows)
    assert tr["before"] == {"n": 1, "mean_score": 0.0, "went_wrong": 1.0}
    assert tr["since"]["n"] == 2 and tr["since"]["went_wrong"] == 0.0


# ── Step 24: the project-aware notch ──────────────────────────────────────────


def _goal(conn, cwd, confidence=0.9, last_seen=T0):
    patterns.record_fact(conn, key=f"goal@{store.project_key(cwd)}",
                         statement="A demo shop with a checkout.", scope="project: demo-app",
                         kind="goal", evidence=[], confidence=confidence,
                         last_seen=last_seen.isoformat(), now=T0)  # fmt: skip


def test_the_notch_knows_this_projects_goal_and_its_usual_gap(db):
    for i in range(12):  # constraints are rare in this project, context is common
        _prompt(db, f"t{i}", "s1", T0 + timedelta(minutes=i), f"task {i}",
                parts=("goal", "context") + (("constraints",) if i < 2 else ()))  # fmt: skip
    _goal(db, "/home/dev/demo-app")
    hint = facts.for_cwd(db, "/home/dev/demo-app/src/pages", ["constraints", "context"],
                         now=T0)  # fmt: skip
    assert hint["goal"] == "A demo shop with a checkout." and hint["project"] == "demo-app"
    assert hint["gap"] == "constraints" and hint["tip"] == "project:constraints"
    assert "only 17% of the time" in hint["text"]
    assert facts.for_cwd(db, "/home/dev/demo-app", ["format"], now=T0)["gap"] in (None, "format")


def test_no_project_tip_for_an_unknown_folder_or_a_faded_goal(db):
    _goal(db, "/home/dev/demo-app", confidence=0.3, last_seen=T0 - timedelta(days=200))
    assert facts.for_cwd(db, "/home/dev/other", ["done"], now=T0) is None
    assert facts.for_cwd(db, "/home/dev/demo-app", ["done"], now=T0) is None
    assert facts.for_cwd(db, None, ["done"], now=T0) is None


def test_a_project_with_little_history_gets_a_reminder_without_a_gap(db):
    _session(db, "s1")
    _goal(db, "/home/dev/demo-app")
    hint = facts.for_cwd(db, "/home/dev/demo-app", ["done"], now=T0)
    assert hint["gap"] is None and hint["tip"] == "project:reminder"
    assert hint["text"] == "This project: A demo shop with a checkout."


# ── The notch end to end: `pompy similar` on the fixture memory ───────────────


@pytest.fixture
def memory(tmp_path, monkeypatch):
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, ZoneInfo("UTC"))
    score.compute(c)
    c.close()
    monkeypatch.setenv("POMPY_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return tmp_path


def _run(monkeypatch, capsys, *argv):
    from pompy import cli

    monkeypatch.setattr(sys, "argv", ["pompy", *argv])
    cli.main()
    return capsys.readouterr().out


def test_pompy_similar_logs_tip_ids_but_never_the_prompt(memory, monkeypatch, capsys):
    prompt = "build the login page with a forgot password link"
    c = sqlite3.connect(memory / "data" / "pompy.db")
    _goal(c, "/home/dev/demo-app", last_seen=datetime.now(UTC))
    c.close()
    monkeypatch.setenv("POMPY_HOLDOUT", "0")
    out = json.loads(_run(monkeypatch, capsys, "similar", "--keyword", "--json", "--session",
                          "s9", "--cwd", "/home/dev/demo-app", prompt))  # fmt: skip
    assert out["project"]["goal"] == "A demo shop with a checkout."
    assert "project:reminder" in out["shown"] and out["held_back"] is False
    log = (memory / "data" / advice.LOG_NAME).read_text()
    row = json.loads(log)
    assert row["digest"] == advice.digest(prompt) and row["session"] == "s9"
    assert row["project"] == store.project_key("/home/dev/demo-app")
    assert "login" not in log and "password" not in log  # ids and numbers only


def test_a_subfolder_is_logged_under_its_project(memory, monkeypatch, capsys):
    _run(monkeypatch, capsys, "similar", "--keyword", "--cwd", "/home/dev/demo-app/src/pages",
         "add a signup page")  # fmt: skip
    row = json.loads((memory / "data" / advice.LOG_NAME).read_text())
    assert row["project"] == store.project_key("/home/dev/demo-app")


def test_held_back_prompts_hide_the_tips_but_are_still_logged(memory, monkeypatch, capsys):
    c = sqlite3.connect(memory / "data" / "pompy.db")
    _goal(c, "/home/dev/demo-app", last_seen=datetime.now(UTC))
    c.close()
    monkeypatch.setenv("POMPY_HOLDOUT", "1")
    out = json.loads(_run(monkeypatch, capsys, "similar", "--keyword", "--json",
                          "--cwd", "/home/dev/demo-app", "add a signup page"))  # fmt: skip
    assert out["project"] is None and out["lesson"] is None and out["held_back"] is True
    assert out["advice"] is None  # the misses tip is held back too
    row = json.loads((memory / "data" / advice.LOG_NAME).read_text())
    assert row["held_back"] and "project:reminder" in row["tips"] and row["shown"] == []


def test_no_log_and_non_task_prompts_leave_the_log_alone(memory, monkeypatch, capsys):
    _run(monkeypatch, capsys, "similar", "--keyword", "--no-log", "add a signup page")
    _run(monkeypatch, capsys, "similar", "--keyword", "yes")
    assert not (memory / "data" / advice.LOG_NAME).exists()


def test_pompy_impact_reports_and_writes_procedural_memory(memory, monkeypatch, capsys):
    assert "No advice logged yet" in _run(monkeypatch, capsys, "impact")
    assert json.loads((memory / "out" / advice.PROFILE_NAME).read_text())["tips"] == {}


def test_render_puts_the_project_line_with_the_other_advice():
    result = {"count": 0, "went_wrong": 0, "lesson": None, "pattern": None,
              "project": {"text": "This project: A demo shop."}}  # fmt: skip
    assert similar.render(result).splitlines() == [
        "Nothing like this in your history yet.",
        "This project: A demo shop.",
    ]
