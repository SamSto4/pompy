# Starter Plan: Phase 1 (MVP)

Goal: by the end of Phase 1, Pompy ingests your Claude Code history into a durable store, computes usage and prompt-quality metrics, and shows a first insights dashboard as a private artifact.

Each step ends with a **"done when"** test and a **learning checkpoint**.

---

### Step 1: Project setup
- `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Tooling: `pytest`, `ruff`.
- **Done when:** `pytest` runs green on an empty test.

### Step 2: Synthetic fixtures (before touching real data)
- Hand-write 2–3 small fake `.jsonl` transcripts in `tests/fixtures/` that mimic the real schema: user/assistant records, tool results, a meta record, a compaction summary, a sub-agent turn.
- **Done when:** fixtures cover every record type the parser must keep or skip.
- 🧠 *Learning:* real agent logs are mostly **not** human prompts. Knowing what to filter out is half of memory hygiene.

### Step 3: `pompy ingest` (the durable store)
- Find `~/.claude/projects/**/*.jsonl`. Copy any new or changed files into `data/raw/` (content-hashed), because Claude Code deletes old transcripts after about 30 days by default.
- Parse into `data/pompy.db`:
  - `sessions(id, project_hash, started_at, ended_at, entrypoint, title)`
  - `prompts(id, session_id, ts_utc, text, source, char_len, word_len)`
  - `turns(id, session_id, ts_utc, role, model, effort, tool_calls, is_error)`
- Filtering rules for a **real human prompt**: `type == "user"`, not `isMeta` / `isCompactSummary` / `isSidechain`, content isn't a `tool_result`, and it isn't injected `<...>` markup.
- Hash project paths, so project names never appear in exports.
- Make it idempotent: running it twice adds nothing.
- **Done when:** fixture tests pass, and a real run prints counts (sessions, prompts, date range).

### Step 4: Usage analytics (`pompy stats`)
- Model mix (by turns and by session), effort levels, active hours and weekdays (**converted from UTC to local time**), session length, prompt length distribution.
- **Done when:** `pompy stats` prints a readable summary and writes `out/stats.json`.

### Step 5: Keywords and themes
- Tokenize prompts and remove stopwords, then compute TF-IDF top terms overall and per month. Also find frequent action verbs and slash-command usage.
- **Done when:** `out/keywords.json` lists top terms and verbs, with counts.
- 🧠 *Learning:* this is the simplest kind of "retrieval index". Phase 2 adds vectors on top of it.

### Step 6: Prompt Specificity Score v0 + friction signals
- Rule-based rubric, one point each: **goal** stated · **context** given · **constraints** · **acceptance criteria / done-definition** · **output format** · **example** · **concrete reference** (file path, URL, code, error text).
- Friction after a prompt: the next human turn is a correction ("no", "actually", "instead", "that's not"), an interrupt, or a rephrase.
- Correlate score with friction: *do your low-scoring prompts actually cause more back-and-forth?*
- **Done when:** each prompt has a score and a friction flag, and the correlation shows up in `out/insights.json`.

### Step 7: Short-term memory object
- A `WorkingSet` class holds the analysis window (e.g. last 14 days or last N sessions), the current question, and the retrieved snippets. Every insight function takes a `WorkingSet` as input.
- **Done when:** insights can be generated for "all time" or "last 2 weeks" just by changing the working set.
- 🧠 *Learning:* this is the context window made explicit. Deciding what goes in it is the core skill of agent memory design.

### Step 8: Insights v0 + first artifact
- Generate the top 3 habits to improve, each with: the evidence (counts), one real before/after rewrite (shown only in the private artifact), and a one-line tip.
- Publish a **Data-type dashboard** artifact: usage charts, specificity trend, keywords, habits.
- **Done when:** you can open the Pompy artifact on claude.ai and learn something new about your prompting.

---

## Decisions to make along the way
1. **Claude API key** for LLM-written summaries and rewrites. Not needed until Step 8 (optional) or Phase 2.
2. **Prompt snippets in the artifact**: yes or no? The artifact is private, but this is the one place raw text leaves the local store.
3. **Analysis window** for "recent" insights (default: 14 days).
