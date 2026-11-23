"""Meaning search: local embeddings over exchange excerpts (layer 3b of the RAG architecture).

Each excerpt's text becomes a vector of 384 numbers from a small local model
(BAAI/bge-small-en-v1.5 through fastembed, on the CPU; nothing leaves the Mac). Texts that
mean similar things get nearby vectors, so "make the button nicer" finds "improve the CTA
styling" with no word in common.

Storage: float32 vectors as SQLite BLOBs. This Python's sqlite3 can't load extensions, so no
sqlite-vec; a brute-force cosine scan over a few thousand vectors takes milliseconds in NumPy.

Incremental: a vector is (re)computed only when its excerpt's text_hash changed or the model
did, so `pompy embed` after a normal ingest touches only the new exchanges.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import tzinfo
from typing import Protocol

from pompy.search import project_keys
from pompy.stats import parse_ts

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
BATCH = 64


class Embedder(Protocol):
    name: str

    def passages(self, texts: list[str]):  # -> np.ndarray (n, dim), rows unit length
        ...

    def query(self, text: str):  # -> np.ndarray (dim,), unit length
        ...


class FastEmbedder:
    """The real model. Loaded on first use; downloaded once into config.model_dir()."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self.name = model
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError:
                raise SystemExit(
                    "Meaning search needs the optional embeddings package. Install it with:\n"
                    '  .venv/bin/pip install -e ".[memory]"'
                ) from None
            from pompy import config

            self._model = TextEmbedding(self.name, cache_dir=str(config.model_dir()))
        return self._model

    def passages(self, texts: list[str]):
        import numpy as np

        return _unit(np.array(list(self._load().passage_embed(texts)), dtype=np.float32))

    def query(self, text: str):
        import numpy as np

        # BGE models expect a search instruction in front of queries; query_embed adds it.
        return _unit(np.array(list(self._load().query_embed([text])), dtype=np.float32))[0]


def _unit(m):
    import numpy as np

    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


def update(conn: sqlite3.Connection, embedder: Embedder) -> dict:
    """Embed every excerpt that has no current vector for this model. Returns counts."""
    import numpy as np

    conn.row_factory = sqlite3.Row
    start = time.perf_counter()
    have = {
        r["prompt_uuid"]: r["text_hash"]
        for r in conn.execute(
            "SELECT prompt_uuid, text_hash FROM main.embeddings WHERE model = ?", (embedder.name,)
        )
    }
    rows = conn.execute("SELECT prompt_uuid, text_hash, body FROM main.excerpts").fetchall()
    todo = [r for r in rows if have.get(r["prompt_uuid"]) != r["text_hash"]]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i : i + BATCH]
        vecs = embedder.passages([r["body"] for r in chunk])
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings (prompt_uuid, model, text_hash, dim, vector) "
            "VALUES (?,?,?,?,?)",
            [
                (
                    r["prompt_uuid"],
                    embedder.name,
                    r["text_hash"],
                    v.shape[0],
                    v.astype(np.float32).tobytes(),
                )
                for r, v in zip(chunk, vecs, strict=True)
            ],
        )
    current = {r["prompt_uuid"] for r in rows}
    gone = [u for u in have if u not in current]
    conn.executemany(
        "DELETE FROM embeddings WHERE prompt_uuid = ? AND model = ?",
        [(u, embedder.name) for u in gone],
    )
    conn.commit()
    return {
        "embedded": len(todo),
        "unchanged": len(rows) - len(todo),
        "removed": len(gone),
        "seconds": round(time.perf_counter() - start, 2),
    }


def search(
    conn: sqlite3.Connection,
    query: str,
    embedder: Embedder,
    limit: int = 10,
    project: str | None = None,
    tz: tzinfo | None = None,
) -> list[dict]:
    """The excerpts closest in meaning to `query` (cosine similarity). Respects a lens."""
    import numpy as np

    if not query.strip():
        return []
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT e.prompt_uuid AS uuid, e.session_id, e.ts, e.header, e.prompt, e.reply,
               e.outcome, e.friction, s.project_name, s.project_key, m.vector
        FROM main.embeddings m
        JOIN excerpts e ON e.prompt_uuid = m.prompt_uuid AND e.text_hash = m.text_hash
        JOIN sessions s ON s.session_id = e.session_id
        WHERE m.model = ?
    """
    params: list = [embedder.name]
    if project:
        keys = project_keys(conn, project)
        if not keys:
            return []
        sql += f" AND s.project_key IN ({','.join('?' * len(keys))})"
        params += keys
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []
    matrix = np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
    sims = matrix @ embedder.query(query)
    order = np.argsort(-sims)[:limit]
    out = []
    for i in order:
        r = dict(rows[i])
        r.pop("vector")
        r["similarity"] = round(float(sims[i]), 4)
        r["local_time"] = parse_ts(r["ts"], tz).strftime("%Y-%m-%d %H:%M") if r["ts"] else None
        out.append(r)
    return out


def count(conn: sqlite3.Connection, model: str = DEFAULT_MODEL) -> int:
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM main.embeddings WHERE model = ?", (model,)
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0
