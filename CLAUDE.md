# Pompy: notes for Claude

An agent-memory lab (short-term/context, episodic, long-term) built on pattern recognition over the user's Claude Code transcripts. Plan: `docs/ROADMAP.md`. Current work: `docs/STARTER_PLAN.md`.

## Commands
- Setup: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
- Test: `.venv/bin/pytest -q`
- Lint/format: `.venv/bin/ruff check . && .venv/bin/ruff format .`
- CLI: `.venv/bin/pompy --help`

## Rules (the repo is PUBLIC)
- Never commit real transcripts, `data/`, `out/`, databases, embeddings, `.env`, or any real prompt text, project name, email, or account ID.
- Tests use synthetic fixtures in `tests/fixtures/` only.
- Before each commit, grep the staged tree for private info.
- Transcript timestamps are UTC; convert to local time for time-of-day patterns.
- The Pre-flight hook path must stay fast (<100 ms, no network, no LLM) and fail open.
- For each step, name the memory concept being built. The user is here to learn.
