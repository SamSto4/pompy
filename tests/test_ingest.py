"""Step 3: ingest must reproduce the answer key in tests/fixtures/expected.json."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from pompy.ingest import ingest, rebuild

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())


@pytest.fixture
def env(tmp_path):
    source = tmp_path / "projects"
    shutil.copytree(FIXTURES / "projects", source)
    data = tmp_path / "data"
    ingest(source, data)
    conn = sqlite3.connect(data / "pompy.db")
    conn.row_factory = sqlite3.Row
    yield source, data, conn
    conn.close()


def _tool_calls(conn, message_id):
    return conn.execute(
        "SELECT COUNT(*) FROM tool_uses WHERE message_id = ?", (message_id,)
    ).fetchone()[0]


def _rows(conn, sql):
    return [dict(r) for r in conn.execute(sql)]


def test_keeps_exactly_the_human_prompts(env):
    _, _, conn = env
    prompts = {r["uuid"]: r for r in _rows(conn, "SELECT * FROM prompts")}
    assert set(prompts) == set(EXPECTED["prompts"])
    for uuid, want in EXPECTED["prompts"].items():
        got = prompts[uuid]
        assert got["kind"] == want["kind"], uuid
        if "text" in want:
            assert got["text"] == want["text"], uuid
        if "source" in want:
            assert got["source"] == want["source"], uuid
        assert bool(got["has_paste"]) == want.get("has_paste", False), uuid
        assert bool(got["has_image"]) == want.get("has_image", False), uuid


def test_injected_blocks_are_stripped(env):
    _, _, conn = env
    texts = [r["text"] for r in _rows(conn, "SELECT text FROM prompts")]
    for marker in ("<ide_opened_file", "<pasted_content", "[Image:", "<system-reminder"):
        assert not any(marker in t for t in texts), marker
    pasted = conn.execute("SELECT text FROM prompts WHERE uuid='b-u3'").fetchone()[0]
    assert "KeyError" in pasted and pasted.endswith("why does this happen")


def test_sessions(env):
    _, _, conn = env
    sessions = {r["session_id"]: r for r in _rows(conn, "SELECT * FROM sessions")}
    assert set(sessions) == set(EXPECTED["sessions"])
    for sid, want in EXPECTED["sessions"].items():
        for key, value in want.items():
            assert sessions[sid][key] == value, (sid, key)
        assert len(sessions[sid]["project_key"]) == 12


def test_events(env):
    _, _, conn = env
    for sid, want in EXPECTED["events"].items():
        got = dict(
            conn.execute(
                "SELECT kind, COUNT(*) FROM events WHERE session_id=? GROUP BY kind", (sid,)
            ).fetchall()
        )
        assert got == want, sid


def test_turns_merge_split_messages(env):
    _, _, conn = env
    turns = {r["message_id"]: r for r in _rows(conn, "SELECT * FROM turns")}
    assert set(turns) == set(EXPECTED["turns"])
    for mid, want in EXPECTED["turns"].items():
        assert turns[mid]["model"] == want["model"], mid
        assert turns[mid]["effort"] == want["effort"], mid
        assert _tool_calls(conn, mid) == want["tool_calls"], mid
        if "output_tokens" in want:
            assert turns[mid]["output_tokens"] == want["output_tokens"], mid


def test_second_run_adds_nothing(env):
    source, data, _ = env
    report = ingest(source, data)
    assert report.files_read == 0
    assert sum(report.new.values()) == 0


def test_reads_only_new_complete_lines(env):
    source, data, conn = env
    transcript = source / "-home-dev-notes-api" / "sess-beta.jsonl"
    new = {
        "type": "user",
        "uuid": "b-new",
        "sessionId": "sess-beta",
        "timestamp": "2026-04-03T09:00:00.000Z",
        "promptSource": "typed",
        "message": {"role": "user", "content": "add tests for pagination"},
    }
    line = json.dumps(new) + "\n"
    with transcript.open("a") as f:
        f.write(line[:20])  # Claude Code is mid-write
    assert sum(ingest(source, data).new.values()) == 0
    with transcript.open("a") as f:
        f.write(line[20:])
    assert ingest(source, data).new["prompt"] == 1
    assert conn.execute("SELECT text FROM prompts WHERE uuid='b-new'").fetchone()[0] == (
        "add tests for pagination"
    )


def test_raw_copy_survives_cleanup_and_rebuilds(env):
    source, data, conn = env
    before = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
    raw = data / "raw" / "-home-dev-demo-app" / "sess-alpha.jsonl"
    assert raw.read_bytes() == (source / "-home-dev-demo-app" / "sess-alpha.jsonl").read_bytes()

    shutil.rmtree(source)  # simulate Claude Code deleting old transcripts
    conn.close()
    rebuild(data, source)
    conn2 = sqlite3.connect(data / "pompy.db")
    assert conn2.execute("SELECT COUNT(*) FROM prompts").fetchone()[0] == before
    assert (
        conn2.execute(
            "SELECT subagent_files FROM sessions WHERE session_id='sess-alpha'"
        ).fetchone()[0]
        == 1
    )
    conn2.close()


def test_reingest_after_rebuild_is_safe(env):
    """After a rebuild, a normal ingest must not re-read files or double-count anything."""
    source, data, conn = env
    conn.close()
    rebuild(data, source)
    report = ingest(source, data)
    assert report.files_read == 0
    conn2 = sqlite3.connect(data / "pompy.db")
    assert _tool_calls(conn2, "msg_a1") == 1
    conn2.close()


def test_rereading_a_file_never_double_counts(env):
    """Even a forced full re-read leaves every count unchanged (all writes are idempotent)."""
    source, data, conn = env
    tables = ("prompts", "turns", "tool_uses", "events", "sessions")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    conn.execute("DELETE FROM files")
    conn.commit()
    ingest(source, data)
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert after == before
    assert _tool_calls(conn, "msg_a1") == 1


def test_resumed_session_copies_are_deduplicated(env):
    """A resumed session's file repeats earlier records; only the new ones count."""
    source, data, conn = env
    original = (source / "-home-dev-notes-api" / "sess-beta.jsonl").read_text().splitlines()[0]
    new = {
        "type": "user",
        "uuid": "g-u1",
        "sessionId": "sess-gamma",
        "timestamp": "2026-04-05T10:00:00.000Z",
        "promptSource": "typed",
        "message": {"role": "user", "content": "continue with the pagination tests"},
    }
    resumed = source / "-home-dev-notes-api" / "sess-gamma.jsonl"
    resumed.write_text(original + "\n" + json.dumps(new) + "\n")
    before = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
    report = ingest(source, data)
    assert report.new["prompt"] == 1
    assert report.new["duplicate"] == 1
    assert conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0] == before + 1
