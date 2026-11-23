"""Pre-flight: the UserPromptSubmit hook that reviews a prompt before Claude sees it."""

import io
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pompy import preflight, score
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"
VAGUE = "fix the nav bar on mobile"
CLEAR = (
    "In src/components/nav.tsx, fix the nav bar on mobile so it stays on one line, because it "
    "wraps under 400px. Keep the desktop layout as it is. Done when it fits on an iPhone SE."
)


def _event(prompt, session="s1"):
    return json.dumps(
        {"prompt": prompt, "session_id": session, "hook_event_name": "UserPromptSubmit"}
    )


@pytest.fixture
def dirs(tmp_path):
    data, out = tmp_path / "data", tmp_path / "out"
    profile = {
        "order": ["goal", "done", "format", "context", "reference", "example", "constraints"],
        "share": {},
        "parts": {
            f.name: {"label": f.label.replace("-", " "), "tip": f.tip} for f in score.FEATURES
        },
    }
    preflight.write_profile(profile, out)
    return data, out


def test_a_vague_task_prompt_is_held_with_your_weakest_habits(dirs):
    data, out = dirs
    decision = json.loads(preflight.run_hook(_event(VAGUE), data, out, now=1000.0))
    assert decision["decision"] == "block"
    lines = decision["reason"].splitlines()
    assert lines[0].startswith("Pompy held this prompt (")
    assert lines[1] == "  + Add done definition: " + score.FEATURES[3].tip  # weakest first
    assert lines[2].startswith("  + Add output format:")


@pytest.mark.parametrize("prompt", [CLEAR, "yes", "commit and push", "/compact", "!! fix it"])
def test_clear_prompts_replies_commands_and_skips_go_through(dirs, prompt):
    data, out = dirs
    assert preflight.run_hook(_event(prompt), data, out, now=1000.0) == ""


def test_sending_the_same_text_again_goes_through_within_ten_minutes(dirs):
    data, out = dirs
    assert preflight.run_hook(_event(VAGUE), data, out, now=1000.0)
    assert preflight.run_hook(_event(VAGUE), data, out, now=1000.0 + 60) == ""
    later = 1000.0 + preflight.RESEND_WINDOW_S + 1
    assert preflight.run_hook(_event(VAGUE + " now"), data, out, now=later)  # new text: held


def test_off_switch(dirs):
    data, out = dirs
    data.mkdir(parents=True, exist_ok=True)
    (data / "preflight_off").touch()
    assert preflight.run_hook(_event(VAGUE), data, out, now=1000.0) == ""


def test_without_a_profile_it_still_reviews(tmp_path):
    out = preflight.run_hook(_event(VAGUE), tmp_path / "data", tmp_path / "none", now=1.0)
    assert json.loads(out)["decision"] == "block"


def test_the_episodic_trace_never_holds_the_prompt_text(dirs):
    data, out = dirs
    preflight.run_hook(_event(VAGUE), data, out, now=1000.0)
    preflight.run_hook(_event(CLEAR), data, out, now=1001.0)
    rows = [json.loads(line) for line in (data / "preflight.jsonl").read_text().splitlines()]
    assert [r["held"] for r in rows] == [True, False]
    text = (data / "preflight.jsonl").read_text()
    assert "nav bar" not in text and "iPhone" not in text


@pytest.mark.parametrize("stdin", ["not json", "", "{}", '{"prompt": 5}'])
def test_it_fails_open(monkeypatch, capsys, tmp_path, stdin):
    monkeypatch.setenv("POMPY_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("POMPY_OUT", str(tmp_path / "out"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    preflight.main_hook()  # must not raise
    assert capsys.readouterr().out == ""


def test_the_profile_puts_goal_first_then_your_rarest_parts(tmp_path):
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    conn = sqlite3.connect(tmp_path / "data" / "pompy.db")
    result = score.compute(conn)
    conn.close()
    profile = preflight.build_profile(result)
    assert profile["order"][0] == "goal"
    shares = [profile["share"][n] for n in profile["order"][1:]]
    assert shares == sorted(shares)
    assert profile["parts"]["done"]["label"] == "Done definition"


def test_the_review_fits_the_hot_path_budget(dirs):
    data, out = dirs
    preflight.run_hook(_event("warm up"), data, out, now=1.0)
    start = time.perf_counter()
    for i in range(20):
        preflight.run_hook(_event(f"{VAGUE} {i}"), data, out, now=2.0 + i)
    per_call_ms = (time.perf_counter() - start) * 1000 / 20
    assert per_call_ms < 100  # in-process; `pompy preflight --bench` measures a fresh process


def test_as_a_fresh_process_it_answers_quickly(dirs, tmp_path):
    data, out = dirs
    env = {"POMPY_DATA": str(data), "POMPY_OUT": str(out), "PATH": "/usr/bin:/bin"}
    start = time.perf_counter()
    run = subprocess.run(
        [sys.executable, "-m", "pompy.cli", "preflight"],
        input=_event(VAGUE),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert json.loads(run.stdout)["decision"] == "block"
    assert elapsed_ms < 1000  # generous for CI; measured ~40 ms median on the dev Mac
