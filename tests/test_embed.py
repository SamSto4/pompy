"""Step 11: the meaning index. A fake embedder stands in for the model, so CI downloads nothing.

The fake maps words to shared "concepts" (login ≈ sign in ≈ credentials), which is what a real
model learns from data. That is enough to prove the plumbing: incremental updates, cosine
ranking, lenses, and a meaning match that keyword search cannot make.
"""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from fakes import FakeEmbedder

from pompy import embed, excerpts
from pompy.ingest import ingest
from pompy.working_set import WorkingSet

FIXTURES = Path(__file__).parent / "fixtures"
UTC = ZoneInfo("UTC")


@pytest.fixture
def conn(tmp_path):
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, UTC)
    yield c
    c.close()


def test_every_excerpt_gets_one_unit_vector(conn):
    result = embed.update(conn, FakeEmbedder())
    assert result["embedded"] == 7 and result["unchanged"] == 0
    rows = conn.execute("SELECT dim, vector FROM embeddings").fetchall()
    assert len(rows) == 7
    for dim, blob in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        assert dim == 64 and abs(float(np.linalg.norm(v)) - 1) < 1e-5


def test_updates_are_incremental(conn):
    fake = FakeEmbedder()
    embed.update(conn, fake)
    assert embed.update(conn, fake)["embedded"] == 0  # nothing changed: no work
    conn.execute("UPDATE excerpts SET text_hash = 'changed' WHERE prompt_uuid = 'b-u1'")
    assert embed.update(conn, fake)["embedded"] == 1  # only the changed exchange
    conn.execute("DELETE FROM excerpts WHERE prompt_uuid = 'b-u8'")
    assert embed.update(conn, fake)["removed"] == 1
    assert fake.calls == 8  # 7 + 1


def test_meaning_finds_what_keywords_miss(conn):
    fake = FakeEmbedder()
    embed.update(conn, fake)
    query = "sign in with credentials"  # no word in common with the login exchange
    assert excerpts.search(conn, query, tz=UTC) == []
    hits = embed.search(conn, query, fake, limit=3, tz=UTC)
    # The login exchange, and the follow-up whose header carries it ("after 'Build a login
    # page...'"): context travels with a short follow-up, so both rank on top.
    assert {h["uuid"] for h in hits[:2]} == {"a-u1", "a-u9"}
    assert hits[0]["similarity"] > hits[-1]["similarity"]
    assert {"header", "prompt", "reply", "outcome", "local_time"} <= set(hits[0])


def test_meaning_search_respects_a_window_and_project(conn):
    fake = FakeEmbedder()
    embed.update(conn, fake)
    april = WorkingSet(tz=UTC, window=WorkingSet.for_period("2026-04", UTC).window)
    with april.lens(conn):
        hits = embed.search(conn, "sign in with credentials", fake, tz=UTC)
    assert hits and all(h["ts"].startswith("2026-04") for h in hits)
    only_demo = embed.search(conn, "broken", fake, project="demo-app", tz=UTC)
    assert only_demo and all(h["project_name"] == "demo-app" for h in only_demo)


def test_models_keep_separate_vectors(conn):
    embed.update(conn, FakeEmbedder("model-a"))
    assert embed.search(conn, "login", FakeEmbedder("model-b"), tz=UTC) == []
    assert embed.count(conn, "model-a") == 7 and embed.count(conn, "model-b") == 0


def test_a_stale_vector_is_never_returned(conn):
    fake = FakeEmbedder()
    embed.update(conn, fake)
    conn.execute("UPDATE excerpts SET text_hash = 'edited' WHERE prompt_uuid = 'a-u1'")
    uuids = {h["uuid"] for h in embed.search(conn, "login password", fake, limit=10, tz=UTC)}
    assert "a-u1" not in uuids  # its text changed since it was embedded
