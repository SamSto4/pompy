"""Step 15: themes, k-means over prompt-only vectors, named by their most distinctive words."""

import shutil
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from fakes import FakeEmbedder

from pompy import excerpts, themes
from pompy.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    shutil.copytree(FIXTURES / "projects", tmp_path / "projects")
    ingest(tmp_path / "projects", tmp_path / "data")
    c = sqlite3.connect(tmp_path / "data" / "pompy.db")
    excerpts.build(c, ZoneInfo("UTC"))
    yield c
    c.close()


def _blobs(seed=0):
    """Three tight, well-separated groups of unit vectors."""
    rng = np.random.default_rng(seed)
    centres = np.eye(8)[:3]
    x = np.concatenate([c + 0.05 * rng.standard_normal((10, 8)) for c in centres])
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def test_kmeans_finds_separate_groups_and_is_reproducible():
    x = _blobs()
    labels, centroids = themes.kmeans(x, 3)
    groups = [set(labels[i * 10 : (i + 1) * 10]) for i in range(3)]
    assert all(len(g) == 1 for g in groups) and len(set().union(*groups)) == 3
    assert np.array_equal(labels, themes.kmeans(x, 3)[0])  # fixed seed
    assert np.allclose(np.linalg.norm(centroids, axis=1), 1)


def test_silhouette_rewards_the_right_number_of_groups():
    x = _blobs()
    right = themes.silhouette(x, themes.kmeans(x, 3)[0])
    assert right > 0.8
    assert right > themes.silhouette(x, themes.kmeans(x, 2)[0])
    assert right > themes.silhouette(x, themes.kmeans(x, 6)[0])


def test_too_few_task_prompts_gives_no_themes(conn):
    result = themes.compute(conn, FakeEmbedder())
    assert result["themes"] == [] and result["note"] == "too few task prompts"


def test_themes_cover_every_task_prompt_once(conn, monkeypatch):
    monkeypatch.setattr(themes, "MIN_TASKS", 2)
    result = themes.compute(conn, FakeEmbedder(), k=2)
    members = [u for t in result["themes"] for u in t["members"]]
    tasks = {p["prompt_uuid"] for p in themes.task_prompts(conn)}
    assert sorted(members) == sorted(tasks)
    for t in result["themes"]:
        assert t["size"] == len(t["members"]) and t["examples"]
        assert 0 <= t["went_wrong_pct"] <= 100 and 0 <= t["mean_score"] <= 7
    assert "themes across" in themes.render(result)


def test_prompt_vectors_are_cached_and_kept_apart_from_search(conn):
    fake = FakeEmbedder()
    prompts = themes.task_prompts(conn)
    themes.prompt_vectors(conn, fake, prompts)
    first = fake.calls
    themes.prompt_vectors(conn, fake, prompts)
    assert fake.calls == first  # unchanged prompts are not embedded again
    models = {r[0] for r in conn.execute("SELECT DISTINCT model FROM embeddings")}
    assert models == {"fake-concepts#prompt"}  # never mixed into the search index


def test_labels_prefer_words_distinctive_to_the_theme():
    texts = ["fix the login page", "login button broken", "login form styles"]
    others = texts + ["deploy the api", "deploy to vercel", "page layout"]
    from collections import Counter

    from pompy import keywords

    all_counts = Counter(t for x in others for t in set(keywords.content_terms(x)))
    assert themes._label(texts, all_counts, len(others))[0] == "login"
