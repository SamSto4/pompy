"""`pompy ingest`: capture transcripts into Pompy's permanent store, incrementally.

Layer 1 of the RAG architecture: **preserve the original source.** Claude Code deletes
old transcripts (about 30 days by default), so Pompy keeps a byte-for-byte copy in
data/raw/ and parses it into SQLite.

Efficiency: each transcript is append-only, so Pompy remembers how many bytes it has
read and on later runs reads only the new complete lines. A normal run touches only
files that changed.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pompy import store
from pompy.parse import parse_record


@dataclass
class Report:
    files_seen: int = 0
    files_read: int = 0
    bytes_read: int = 0
    bad_lines: int = 0
    excerpts: int = 0  # exchange excerpts after this run (Phase 2, layer 2)
    episodes: int = 0  # episode cards, one per chat with typed exchanges
    new: Counter = field(default_factory=Counter)


def ingest(source: Path, data: Path, copy_raw: bool = True) -> Report:
    """Read new transcript lines from `source` into `data/pompy.db` (and `data/raw/`)."""
    conn = store.connect(data / "pompy.db")
    if store.needs_rebuild(conn):  # made by an older Pompy
        conn.close()
        if copy_raw and (data / "raw").exists():
            rebuild(data, source)  # re-derive everything from the raw copies, then continue
        else:
            _delete_db(data / "pompy.db")
        conn = store.connect(data / "pompy.db")
    report = Report()
    try:
        for path in sorted(source.glob("*/*.jsonl")):
            report.files_seen += 1
            _ingest_file(conn, path, data, copy_raw, report)
        _count_subagents(conn, source)
        conn.commit()
        from pompy import excerpts

        report.excerpts = excerpts.build(conn)["excerpts"]
        from pompy import episodes

        report.episodes = episodes.build(conn)["cards"]
    finally:
        conn.close()
    return report


def rebuild(data: Path, source: Path | None = None) -> Report:
    """Recreate the database from the raw copies alone (works even after Claude Code cleanup).

    Afterwards the bookkeeping points back at `source`, so the next normal ingest
    continues where the raw copy ends instead of re-reading everything.
    """
    db = data / "pompy.db"
    keep_subagents = _read_subagent_counts(db)
    _delete_db(db)
    report = ingest(data / "raw", data, copy_raw=False)
    conn = store.connect(db)
    conn.executemany(
        "UPDATE sessions SET subagent_files = ? WHERE session_id = ?",
        [(n, sid) for sid, n in keep_subagents.items()],
    )
    if source is not None:
        for row in conn.execute("SELECT source_path FROM files").fetchall():
            raw = Path(row["source_path"])
            conn.execute(
                "UPDATE files SET source_path = ?, raw_path = ? WHERE source_path = ?",
                (str(source / raw.parent.name / raw.name), str(raw), str(raw)),
            )
    conn.commit()
    conn.close()
    return report


def _delete_db(db: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)


def _ingest_file(
    conn: sqlite3.Connection, path: Path, data: Path, copy_raw: bool, report: Report
) -> None:
    key = str(path)
    size = path.stat().st_size
    row = conn.execute("SELECT offset, size FROM files WHERE source_path = ?", (key,)).fetchone()
    offset = row["offset"] if row else 0
    if row and size == row["size"]:
        return  # unchanged since last run

    session_id = path.stem
    raw_path = data / "raw" / path.parent.name / path.name if copy_raw else path
    if size < offset:
        # The file shrank (rewritten). Forget what it gave us and start over.
        store.forget_session(conn, session_id)
        offset = 0

    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read(size - offset)
    end = chunk.rfind(b"\n")
    chunk = chunk[: end + 1] if end >= 0 else b""  # leave a half-written last line for later

    if copy_raw and chunk:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if offset == 0 or not raw_path.exists():
            if offset:  # raw copy went missing: copy everything up to here first
                shutil.copyfile(path, raw_path)
                raw_path.write_bytes(raw_path.read_bytes()[: offset + len(chunk)])
            else:
                raw_path.write_bytes(chunk)
        else:
            with raw_path.open("ab") as out:
                out.write(chunk)

    for line in chunk.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            report.bad_lines += 1
            continue
        for item in parse_record(record, session_id):
            report.new[store.save(conn, item)] += 1

    new_offset = offset + len(chunk)
    conn.execute(
        """INSERT INTO files VALUES (?,?,?,?,?)
           ON CONFLICT(source_path) DO UPDATE SET offset = excluded.offset, size = excluded.size,
                                                  raw_path = excluded.raw_path""",
        (key, str(raw_path), session_id, new_offset, new_offset),
    )
    report.files_read += 1
    report.bytes_read += len(chunk)


def _count_subagents(conn: sqlite3.Connection, source: Path) -> None:
    """Sub-agent transcripts aren't your prompts, but how often you delegate is a pattern."""
    for session_dir in source.glob("*/*/subagents"):
        n = len(list(session_dir.glob("*.jsonl")))
        conn.execute(
            "UPDATE sessions SET subagent_files = MAX(subagent_files, ?) WHERE session_id = ?",
            (n, session_dir.parent.name),
        )


def _read_subagent_counts(db: Path) -> dict[str, int]:
    if not db.exists():
        return {}
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT session_id, subagent_files FROM sessions"))
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def summary(data: Path) -> dict:
    """Totals across the whole store (all-time)."""
    conn = store.connect(data / "pompy.db")
    try:
        one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "sessions": one("SELECT COUNT(*) FROM sessions"),
            "projects": one("SELECT COUNT(DISTINCT project_key) FROM sessions"),
            "prompts": one("SELECT COUNT(*) FROM prompts"),
            "prompts_by_kind": dict(
                conn.execute(
                    "SELECT kind, COUNT(*) FROM prompts GROUP BY kind ORDER BY 2 DESC"
                ).fetchall()
            ),
            "assistant_turns": one("SELECT COUNT(*) FROM turns"),
            "events": dict(conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind")),
            "first_prompt": one("SELECT MIN(ts) FROM prompts"),
            "last_prompt": one("SELECT MAX(ts) FROM prompts"),
        }
    finally:
        conn.close()
