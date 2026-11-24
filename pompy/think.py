"""`pompy think`: a Claude agent that looks for patterns in your memory (RAG layers 6 and 7).

The agent reads a short briefing (what the miner found, the themes, what is already known),
then explores your exchanges through read-only tools and proposes new hypotheses in plain
words. It may store one only with evidence on BOTH sides: at least 3 excerpts that support
it and at least 1 that contradicts it, all of which it actually retrieved in this run, so
it cannot invent evidence. Agent patterns stay "hypothesis": the language model proposes,
statistics (`pompy mine`) decide.

Privacy: excerpts go to the Claude API only while this command runs. `--dry-run` prints the
instructions, briefing and tools and sends nothing. The API key is read from a git-ignored
`.env` (ANTHROPIC_API_KEY=...) or the environment.

Cost: the fixed instructions and tools are cached (prompt caching); usage and an estimated
cost are printed at the end. Turn and token limits stop a runaway loop.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from pompy import config, episodes, hybrid, patterns

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TURNS = 24
MAX_OUTPUT_TOKENS = 16000
MAX_PROPOSALS = 3
MIN_SUPPORT = 3
MIN_CONTRADICT = 1
# USD per million tokens: input, output, cache read, cache write (5-minute TTL is 1.25x input).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

SYSTEM = """You are Pompy's hypothesis agent. Pompy is a private memory of one developer's \
conversations with Claude Code. Each "exchange" is one prompt they wrote, the start of \
Claude's reply, and what happened next ("moved on", or friction: they corrected Claude, \
reported a problem, interrupted, rejected a change, asked again, or Claude had to ask what \
they meant).

Your job: find patterns in how this developer works with Claude that would help them get \
better results, and store the ones you can back with evidence on both sides.

How to work:
- Start from the briefing. The statistical miner has already tested the obvious rubric \
claims (missing context, done-definition, output format...) and most did NOT hold. Do not \
re-propose those. Look for other explanations: kinds of task, wording, what the prompt \
assumed Claude knew, what happened just before, how a chat ended.
- Use `search` (keywords + meaning) to gather exchanges, `get_excerpt` to read one in full, \
`get_episode_card` to see the chat it belongs to, `list_patterns` to see what is known.
- Before proposing, search deliberately for counter-examples: exchanges that fit the \
pattern's condition but went fine, or that lack it and still went wrong.
- `propose_pattern` needs a short, testable statement in plain words, its scope, your \
reasoning, at least 3 supporting and at least 1 contradicting exchange ids, all taken from \
tool results in this session. Propose at most 3 patterns. Fewer, better-supported ones beat \
many weak ones; proposing none is acceptable if nothing holds.
- Never quote secrets, keys, emails or personal details in a statement or rationale.
- Finish with a short plain summary of what you stored and why, and what you ruled out."""

TOOLS = [
    {
        "name": "search",
        "description": (
            "Find exchanges by keywords and meaning (hybrid search). Returns up to `limit` "
            "exchanges: id, date, project, prompt, Claude's reply start, and outcome."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_excerpt",
        "description": "One exchange in full: header, prompt, Claude's reply start, outcome.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Exchange id."}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_episode_card",
        "description": "The card of the chat an exchange belongs to: intent, length, outcome.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Exchange id."}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "list_patterns",
        "description": "Patterns Pompy already holds, with status and confidence.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "propose_pattern",
        "description": (
            "Store a new hypothesis with evidence on both sides. Rejected unless it cites at "
            "least 3 supporting and 1 contradicting exchange ids retrieved in this session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "statement": {"type": "string", "description": "Short, testable, plain words."},
                "scope": {"type": "string", "description": "'all', or the kind of task."},
                "rationale": {"type": "string", "description": "Why the evidence fits."},
                "supports": {"type": "array", "items": {"type": "string"}},
                "contradicts": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["statement", "scope", "rationale", "supports", "contradicts"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def load_env() -> None:
    """Read KEY=VALUE lines from the repo's git-ignored .env into the environment."""
    path = config.REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Run:
    """One agent session: what it retrieved, stored, and spent."""

    seen: set[str] = field(default_factory=set)
    proposed: list[dict] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    excerpts_sent: int = 0
    usage: dict = field(
        default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    )
    turns: int = 0
    summary: str = ""
    stopped: str = ""


def briefing(conn: sqlite3.Connection) -> str:
    """The volatile part of the prompt: what Pompy knows right now."""
    held = patterns.load(conn)
    tested = [p for p in held if p["source"] == "miner"]
    near = sorted(tested, key=lambda p: p["stats"]["q"])[:5]
    lines = [f"Memory: {conn.execute('SELECT COUNT(*) FROM excerpts').fetchone()[0]} exchanges."]
    th = config.out_dir() / "themes.json"
    if th.exists():
        data = json.loads(th.read_text())
        lines.append("Themes (task prompts grouped by meaning):")
        for t in data.get("themes", []):
            name = " · ".join(t["label"]) or "mixed short prompts"
            lines.append(
                f"- {name}: {t['size']} prompts, {t['went_wrong_pct']}% went wrong, "
                f"specificity {t['mean_score']}/7"
            )
    lines.append(
        f"The miner tested {len(tested)} claims; "
        f"{sum(p['status'] == 'supported' for p in tested)} supported. Closest calls:"
    )
    for p in near:
        s = p["stats"]
        lines.append(
            f"- {p['statement']} ({s['rate_a']:.0%} vs {s['rate_b']:.0%}, "
            f"n {s['n_a']} vs {s['n_b']}, q {s['q']:.2f}, {p['status']})"
        )
    mine_ = [p for p in held if p["source"] == "agent"]
    if mine_:
        lines.append("Hypotheses from earlier agent runs (do not repeat):")
        lines += [f"- {p['statement']}" for p in mine_]
    return "\n".join(lines)


def _excerpt_view(h: dict) -> dict:
    return {
        "id": h["uuid"],
        "date": (h.get("local_time") or "")[:10],
        "project": h.get("project_name") or h.get("project_key"),
        "prompt": h["prompt"][:400],
        "claude": (h.get("reply") or "")[:300],
        "outcome": h["outcome"],
    }


def run_tool(conn: sqlite3.Connection, embedder, run: Run, name: str, args: dict, now=None):
    """Execute one tool call. Returns (result_text, is_error)."""
    if name == "search":
        hits = hybrid.search(
            conn, args["query"], embedder, limit=min(int(args.get("limit", 8)), 10)
        )
        views = [_excerpt_view(h) for h in hits]
        run.seen.update(v["id"] for v in views)
        run.excerpts_sent += len(views)
        return json.dumps(views), False
    if name == "get_excerpt":
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            "SELECT prompt_uuid, header, prompt, reply, outcome FROM excerpts "
            "WHERE prompt_uuid = ?",
            (args["id"],),
        ).fetchone()
        if not r:
            return f"No exchange with id {args['id']}.", True
        run.seen.add(r["prompt_uuid"])
        run.excerpts_sent += 1
        return json.dumps({**dict(r), "id": r["prompt_uuid"]}), False
    if name == "get_episode_card":
        row = conn.execute(
            "SELECT session_id FROM excerpts WHERE prompt_uuid = ?", (args["id"],)
        ).fetchone()
        card = next(
            (c for c in episodes.cards(conn, limit=1000) if row and c["session_id"] == row[0]), None
        )
        if not card:
            return f"No chat found for exchange {args['id']}.", True
        keep = ("title", "intent", "local_start", "minutes", "exchanges", "tasks", "mean_score",
                "friction_kinds", "files_changed", "outcome")  # fmt: skip
        return json.dumps({k: card[k] for k in keep}), False
    if name == "list_patterns":
        held = patterns.load(conn, now=now)
        return json.dumps(
            [
                {
                    "statement": p["statement"],
                    "status": p["status"],
                    "source": p["source"],
                    "confidence": p["confidence_now"],
                }
                for p in held
            ]  # fmt: skip
        ), False
    if name == "propose_pattern":
        return _propose(conn, run, args, now)
    return f"Unknown tool {name}.", True


def _propose(conn: sqlite3.Connection, run: Run, args: dict, now) -> tuple[str, bool]:
    if len(run.proposed) >= MAX_PROPOSALS:
        return f"Limit reached: at most {MAX_PROPOSALS} patterns per run.", True
    sup = list(dict.fromkeys(args["supports"]))
    con = list(dict.fromkeys(args["contradicts"]))
    unseen = [u for u in sup + con if u not in run.seen]
    if unseen:
        return f"Cite only exchanges you retrieved in this session. Not retrieved: {unseen}", True
    if len(sup) < MIN_SUPPORT or len(con) < MIN_CONTRADICT:
        return (
            f"Need at least {MIN_SUPPORT} supporting and {MIN_CONTRADICT} contradicting "
            f"exchanges (got {len(sup)} and {len(con)}). Search for counter-examples too.",
            True,
        )
    if set(sup) & set(con):
        return "An exchange cannot both support and contradict the same pattern.", True
    statement = " ".join(args["statement"].split())[:240]
    key = "agent:" + "-".join(statement.lower().split())[:80]
    stats = {"effect": 0.0, "n_a": len(sup), "n_b": len(con), "p": 1.0, "q": 1.0,
             "rate_a": 0.0, "rate_b": 0.0, "rationale": args["rationale"][:600]}  # fmt: skip
    stored = patterns.record(
        conn,
        key=key,
        statement=statement,
        scope=args["scope"][:80] or "all",
        kind="habit",
        source="agent",
        stats=stats,
        supports=sup,
        contradicts=con,
        now=now,
    )
    run.proposed.append(
        {"statement": statement, **stored, "supports": len(sup), "contradicts": len(con)}
    )
    return f"Stored as a hypothesis (id {stored['id']}). The miner can test it later.", False


def cost(run: Run, model: str) -> float | None:
    price = PRICES.get(model)
    if not price:
        return None
    u = run.usage
    pin, pout, pread, pwrite = price
    return round(
        (
            u["input"] * pin
            + u["output"] * pout
            + u["cache_read"] * pread
            + u["cache_write"] * pwrite
        )
        / 1_000_000,
        4,
    )


def think(
    conn: sqlite3.Connection,
    client,
    embedder=None,
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    now: datetime | None = None,
) -> Run:
    """Run the agent loop until it finishes, hits the turn limit, or is refused."""
    run = Run()
    system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": "Briefing:\n" + briefing(conn)}]
    while run.turns < MAX_TURNS:
        run.turns += 1
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        u = response.usage
        run.usage["input"] += u.input_tokens or 0
        run.usage["output"] += u.output_tokens or 0
        run.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        run.usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        if response.stop_reason == "refusal":
            run.stopped = "refused"
            break
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            run.summary = "\n".join(b.text for b in response.content if b.type == "text").strip()
            run.stopped = "done" if response.stop_reason == "end_turn" else response.stop_reason
            break
        results = []
        for b in calls:
            run.tool_calls.append(b.name)
            try:
                text, is_error = run_tool(conn, embedder, run, b.name, dict(b.input), now)
            except Exception as e:  # noqa: BLE001 — a tool failure goes back to the agent
                text, is_error = f"Tool failed: {e}", True
            results.append(
                {"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": is_error}
            )
        messages.append({"role": "user", "content": results})
    else:
        run.stopped = "turn limit"
    return run


def render(run: Run, model: str) -> str:
    c = cost(run, model)
    out = [
        f"pompy think: {run.turns} turns, {len(run.tool_calls)} tool calls, "
        f"{run.excerpts_sent} excerpts sent to the API ({run.stopped}).",
        f"Tokens: {run.usage['input']} in, {run.usage['output']} out, "
        f"{run.usage['cache_read']} read from cache, {run.usage['cache_write']} written to cache"
        + (f" · about ${c:.3f}" if c is not None else ""),
        "",
    ]
    if run.proposed:
        out.append(f"Stored {len(run.proposed)} hypothesis(es):")
        out += [
            f"  + {p['statement']}  ({p['supports']} for, {p['contradicts']} against)"
            for p in run.proposed
        ]
    else:
        out.append("Stored nothing new.")
    if run.summary:
        out += ["", run.summary]
    return "\n".join(out)
