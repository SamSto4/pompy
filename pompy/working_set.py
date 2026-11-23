"""Short-term memory: the WorkingSet (layer 1, the context window made explicit).

An agent can't think about everything it remembers at once. For each job it assembles a small
working set: which slice of memory to look at (the window), what it's trying to answer (the
question), and the few pieces of evidence it pulled in (snippets), within a budget. Deciding
what goes in, and noting what was left out and why, is the core skill of agent memory design.

Every insight function (stats, keywords, score) takes a WorkingSet. The default is all-time.
A period such as "this-month" or "2026-10" narrows the window, so periods can be compared.

How the window is applied: `lens(conn)` adds temporary SQL views named like the real tables
(`prompts`, `turns`, ...) that show only in-window rows. SQLite looks up temporary objects
first, so existing queries see the window without changing; `main.prompts` still reaches
the full table. Use the lens on reads only, and always through `with`.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo

# Tables the lens narrows. Each has a `ts` (UTC) and a `session_id`.
TIMED_TABLES = ("prompts", "turns", "tool_uses", "replies", "events", "excerpts", "episode_cards")
LENSED_TABLES = (*TIMED_TABLES, "sessions")

CHARS_PER_TOKEN = 4  # rough estimate: Pompy has no tokenizer, so budgets are approximate
DEFAULT_BUDGET_TOKENS = 1500
PER_SESSION_LIMIT = 2  # more than this from one chat crowds out other evidence
SNIPPET_CHARS = 400
REPLY_SNIPPET_CHARS = 240  # how much of Claude's reply a snippet carries


# ── The window: which slice of memory to look at ─────────────────────────────


@dataclass
class Window:
    """A time range (UTC timestamps, start inclusive, end exclusive) and an optional project."""

    label: str = "all-time"
    since: str | None = None
    until: str | None = None
    project: str | None = None

    @property
    def all_time(self) -> bool:
        return self.since is None and self.until is None and self.project is None


def _utc(dt: datetime) -> str:
    """Format like Claude Code's own timestamps, so plain string comparison works."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _midnight(d: date, tz: tzinfo | None) -> datetime:
    local = datetime(d.year, d.month, d.day)
    return local.replace(tzinfo=tz) if tz is not None else local.astimezone()


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


_DAYS_RE = re.compile(r"^(\d+)d$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEAR_RE = re.compile(r"^(\d{4})$")

PERIOD_HELP = (
    "all · 7d / 30d (last N days) · today · this-week · this-month · last-month · "
    "2026-10 · 2026 · 2026-10-05 · A..B (from the start of A to the end of B; either side "
    "may be empty)"
)


def _span(token: str, tz: tzinfo | None, today: date) -> tuple[date, date]:
    """A named period as [first day, day after the last day], in local dates."""
    if token == "today":
        return today, today + timedelta(days=1)
    if token == "this-week":
        start = today - timedelta(days=today.weekday())  # weeks start on Monday
        return start, start + timedelta(days=7)
    if token == "this-month":
        start = _month_start(today.year, today.month)
        return start, _next_month(start)
    if token == "last-month":
        end = _month_start(today.year, today.month)
        return _month_start((end - timedelta(days=1)).year, (end - timedelta(days=1)).month), end
    if m := _MONTH_RE.match(token):
        year, month = int(m[1]), int(m[2])
        if not 1 <= month <= 12:
            raise ValueError(f"no month {month} in {token!r}")
        start = _month_start(year, month)
        return start, _next_month(start)
    if m := _DAY_RE.match(token):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return d, d + timedelta(days=1)
    if m := _YEAR_RE.match(token):
        return date(int(m[1]), 1, 1), date(int(m[1]) + 1, 1, 1)
    raise ValueError(f"unknown period {token!r}. Try: {PERIOD_HELP}")


def parse_period(
    period: str | None,
    tz: tzinfo | None = None,
    now: datetime | None = None,
    project: str | None = None,
) -> Window:
    """Turn "this-month", "30d", "2026-10", "2026-09..2026-10" into a Window.

    Periods follow your local calendar (tz, default this Mac's), so "this-month" starts at
    local midnight on the 1st, matching the monthly buckets in the reports.
    """
    period = (period or "all").strip().lower()
    now = now or datetime.now(UTC)
    local_now = now.astimezone(tz) if tz is not None else now.astimezone()
    today = local_now.date()

    if period in ("all", "all-time"):
        return Window(label="all-time", project=project)
    if m := _DAYS_RE.match(period):
        return Window(
            label=f"last {m[1]} days",
            since=_utc(local_now - timedelta(days=int(m[1]))),
            project=project,
        )
    if ".." in period:
        a, b = (s.strip() for s in period.split("..", 1))
        since = _utc(_midnight(_span(a, tz, today)[0], tz)) if a else None
        until = _utc(_midnight(_span(b, tz, today)[1], tz)) if b else None
        return Window(label=period, since=since, until=until, project=project)
    start, end = _span(period, tz, today)
    return Window(
        label=period,
        since=_utc(_midnight(start, tz)),
        until=_utc(_midnight(end, tz)),
        project=project,
    )


def before(window: Window) -> Window:
    """Everything before a window starts: the natural thing to compare a period with."""
    if window.since is None:
        raise ValueError(f"{window.label!r} has no start, so nothing comes before it")
    return Window(label=f"before {window.label}", until=window.since, project=window.project)


# ── Snippets: the evidence pulled into the working set ──────────────────────


@dataclass
class Snippet:
    uuid: str  # source reference: the transcript record...
    session_id: str  # ...and the chat it came from
    local_time: str | None
    project: str | None
    text: str
    rank: int  # position in the keyword search (1 = best match)
    tokens: int  # estimated
    reply: str = ""  # the start of Claude's reply (exchange excerpts only)
    outcome: str = ""  # what happened next: friction, or "moved on"


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def estimate_tokens(text: str) -> int:
    return max(1, -(-len(text) // CHARS_PER_TOKEN))  # ceiling division


# ── The working set itself ───────────────────────────────────────────────────


@dataclass
class WorkingSet:
    """What Pompy holds in mind for one job. Plain data: building one touches no database."""

    window: Window = field(default_factory=Window)
    tz: tzinfo | None = None
    question: str | None = None
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    snippets: list[Snippet] = field(default_factory=list)
    left_out: list[dict] = field(default_factory=list)  # {uuid, rank, reason}
    window_counts: dict = field(default_factory=dict)  # prompts in the window vs in memory
    retrieval: str = ""  # how the snippets were found: keyword | hybrid

    @classmethod
    def for_period(
        cls,
        period: str | None = None,
        tz: tzinfo | None = None,
        project: str | None = None,
        **kwargs,
    ) -> WorkingSet:
        return cls(window=parse_period(period, tz, project=project), tz=tz, **kwargs)

    @property
    def tokens_used(self) -> int:
        return sum(s.tokens for s in self.snippets)

    # ── The lens: apply the window to a connection ──

    @contextmanager
    def lens(self, conn: sqlite3.Connection):
        """Within this block, `prompts`, `turns`, ... show only rows inside the window."""
        if self.window.all_time:
            yield conn
            return
        conditions = []
        if self.window.since:
            conditions.append(f"ts >= '{_checked_ts(self.window.since)}'")
        if self.window.until:
            conditions.append(f"ts < '{_checked_ts(self.window.until)}'")
        if self.window.project:
            from pompy.search import project_keys

            keys = project_keys(conn, self.window.project)
            quoted = ",".join(f"'{_checked_key(k)}'" for k in keys) or "NULL"
            conditions.append(
                "session_id IN (SELECT session_id FROM main.sessions "
                f"WHERE project_key IN ({quoted}))"
            )
        where = " AND ".join(conditions)
        try:
            for table in TIMED_TABLES:
                conn.execute(
                    f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} WHERE {where}"
                )
            conn.execute(
                "CREATE TEMP VIEW sessions AS SELECT * FROM main.sessions WHERE session_id IN "
                "(SELECT session_id FROM temp.prompts UNION SELECT session_id FROM temp.turns)"
            )
            yield conn
        finally:
            for table in LENSED_TABLES:
                conn.execute(f"DROP VIEW IF EXISTS temp.{table}")

    # ── Retrieval: fill the working set with evidence for the question ──

    def retrieve(
        self, conn: sqlite3.Connection, candidates: int = 25, embedder="auto"
    ) -> WorkingSet:
        """Pull the best-matching exchanges for `question` into the set, within the budget.

        Searches exchange excerpts (your prompt + Claude's reply + what happened next) when
        they exist, else bare prompts. With a meaning index, keyword and meaning results are
        fused (hybrid); `embedder=None` forces keyword-only, "auto" uses the local model.
        Every candidate that doesn't make it is kept in `left_out` with the reason, so the
        Memory Lab can show what was excluded and why.
        """
        from pompy import excerpts, hybrid
        from pompy.search import search

        self.snippets, self.left_out = [], []
        use_excerpts = excerpts.count(conn) > 0
        if embedder == "auto":
            embedder = hybrid.default_embedder(conn) if use_excerpts else None
        self.retrieval = "hybrid" if use_excerpts and embedder else "keyword"
        with self.lens(conn):
            in_window = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
            q = self.question or ""
            if use_excerpts:
                hits = hybrid.search(conn, q, embedder, limit=candidates, tz=self.tz)
            else:
                hits = search(conn, q, limit=candidates, tz=self.tz, match_any=True)
                marks = ",".join("?" * len(hits))
                texts = dict(
                    conn.execute(
                        f"SELECT uuid, own_text FROM prompts WHERE uuid IN ({marks})",
                        [h["uuid"] for h in hits],
                    ).fetchall()
                )
                for h in hits:
                    h["prompt"] = texts.get(h["uuid"]) or h["snippet"]
        self.window_counts = {
            "prompts_in_window": in_window,
            "prompts_in_memory": conn.execute("SELECT COUNT(*) FROM main.prompts").fetchone()[0],
            "candidates": len(hits),
        }
        per_session: dict[str, int] = {}
        for rank, h in enumerate(hits, 1):
            text = _clip(" ".join(h["prompt"].split()), SNIPPET_CHARS)
            reply = _clip(h.get("reply") or "", REPLY_SNIPPET_CHARS)
            outcome = h.get("outcome") or ""
            tokens = estimate_tokens(text + reply + outcome)
            sid = h["session_id"]
            if per_session.get(sid, 0) >= PER_SESSION_LIMIT:
                reason = f"already {PER_SESSION_LIMIT} snippets from this chat"
            elif self.tokens_used + tokens > self.budget_tokens:
                left = self.budget_tokens - self.tokens_used
                reason = f"over budget (needs ~{tokens} tokens, {left} left)"
            else:
                per_session[sid] = per_session.get(sid, 0) + 1
                self.snippets.append(
                    Snippet(
                        uuid=h["uuid"],
                        session_id=sid,
                        local_time=h["local_time"],
                        project=h["project_name"] or h["project_key"],
                        text=text,
                        rank=rank,
                        tokens=tokens,
                        reply=reply,
                        outcome=outcome,
                    )
                )
                continue
            self.left_out.append({"uuid": h["uuid"], "rank": rank, "reason": reason})
        return self

    # ── For people: what went in, what was left out ──

    def manifest(self) -> dict:
        return {
            "window": asdict(self.window),
            "timezone": str(self.tz) if self.tz is not None else "local",
            "question": self.question,
            "budget_tokens_estimate": self.budget_tokens,
            "tokens_used_estimate": self.tokens_used,
            "counts": self.window_counts,
            "retrieval": self.retrieval,
            "snippets": [asdict(s) for s in self.snippets],
            "left_out": self.left_out,
            "note": "Token counts are estimates (about 4 characters per token).",
        }


_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_KEY_RE = re.compile(r"^[0-9a-f]{12}$")


def _checked_ts(ts: str) -> str:
    """Timestamps are built by this module, but check before putting one inside SQL."""
    if not _TS_RE.match(ts):
        raise ValueError(f"bad window timestamp {ts!r}")
    return ts


def _checked_key(key: str) -> str:
    if not _KEY_RE.match(key):
        raise ValueError(f"bad project key {key!r}")
    return key


def render(ws: WorkingSet) -> str:
    m = ws.manifest()
    c = m["counts"]
    out = [
        f"Working set for: {ws.question!r}",
        f"  window: {ws.window.label}"
        + (f" · project {ws.window.project}" if ws.window.project else "")
        + f" · {c.get('prompts_in_window', 0)} of {c.get('prompts_in_memory', 0)} prompts in view"
        + (f" · {ws.retrieval} search" if ws.retrieval else ""),
        f"  budget: ~{ws.tokens_used} of ~{ws.budget_tokens} tokens used (estimate)",
        "",
        f"What went in ({len(ws.snippets)}):",
    ]
    for s in ws.snippets:
        out.append(f"  #{s.rank} {s.local_time}  {s.project}  ~{s.tokens} tok")
        out.append(f"     You: {s.text[:160]}{'…' if len(s.text) > 160 else ''}")
        if s.reply:
            out.append(f"     Claude: {s.reply[:160]}{'…' if len(s.reply) > 160 else ''}")
        if s.outcome:
            out.append(f"     Then: {s.outcome}")
    if not ws.snippets:
        out.append("  nothing matched")
    if ws.left_out:
        out += ["", f"What was left out ({len(ws.left_out)}):"]
        reasons: dict[str, int] = {}
        for item in ws.left_out:
            key = re.sub(r"\(.*\)", "", item["reason"]).strip()
            reasons[key] = reasons.get(key, 0) + 1
        out += [f"  {n} × {reason}" for reason, n in reasons.items()]
    return "\n".join(out)
