# Starter Plan: Phase 1 (MVP)

Goal: by the end of Phase 1, Pompy captures your Claude Code history in a permanent local store, recognizes your first patterns (time, models, keywords, prompt structure, friction), shows them in a private Pompy Dashboard inside Claude Code, and reviews each prompt as you send it (Pre-flight v0).

Each step ends with a **"done when"** test and a **learning checkpoint**.

---

### Step 1: Project setup ✅
- `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Tooling: `pytest`, `ruff`.
- **Done when:** `pytest` runs green on an empty test.

### Step 2: Synthetic fixtures (before touching real data) ✅
- Hand-write 2–3 small fake `.jsonl` transcripts in `tests/fixtures/` that mimic the real schema: user/assistant records, tool results, a meta record, a compaction summary, a sub-agent turn.
- **Done when:** fixtures cover every record type the parser must keep or skip.
- 🧠 *Learning:* real agent logs are mostly **not** human prompts. Knowing what to filter out is half of memory hygiene.

### Step 3: `pompy ingest` (the durable store) ✅
- Find `~/.claude/projects/**/*.jsonl`. Copy any new or changed files into `data/raw/` (content-hashed), because Claude Code deletes old transcripts after about 30 days by default.
- Parse into `data/pompy.db` (see `pompy/store.py`):
  - `sessions`: project (hashed key + local name), entrypoint, title, first/last time, sub-agent count
  - `prompts`: your real prompts: kind (typed, command, bash, suggestion), text, length, paste/image flags
  - `turns` + `tool_uses`: Claude's replies: model, effort, tokens, which tools it called
  - `events`: friction signals: interrupts, rejected tool calls, compactions
- Filtering rules for a **real human prompt**: see the table in `tests/fixtures/README.md`. Real data taught us three extra rules: resumed sessions copy earlier records (de-duplicate by record id), `textNotTyped` marks app-injected text, and browser-extension instructions are injected blocks.
- Hash project paths, so project names never appear in exports.
- Make it idempotent and **incremental**: remember each file's byte offset, and on later runs read only the new lines. Running it twice adds nothing, and a normal run takes well under a second.
- **Done when:** fixture tests pass, and a real run prints counts (sessions, prompts, date range).

### Step 4: Usage analytics (`pompy stats`) ✅
- Model mix (by turns and by session), effort levels, active hours and weekdays (**converted from UTC to local time**), session length, prompt length distribution.
- Also: models per month, model switching within sessions, *active* session minutes (pauses over 30 min don't count), a weekday × hour grid for the Dashboard heatmap, surfaces (CLI, VS Code, desktop), top slash commands, and the tools Claude used.
- `--tz America/New_York` overrides the time zone; `--json` prints the raw data.
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
- A `WorkingSet` class holds the analysis window (**default: all-time**, since Pompy's first ingest), the current question, and the retrieved snippets. Every insight function takes a `WorkingSet` as input.
- **Done when:** insights default to all-time, and an optional filter (e.g. "this month") lets you compare periods to see whether you're improving.
- 🧠 *Learning:* this is the context window made explicit. Deciding what goes in it is the core skill of agent memory design.

### Step 8: Insights v0 + first artifact
- Generate the top 3 habits to improve, each with: the evidence (counts), one real before/after rewrite of your own prompt snippet (shown only in the private artifact), and a one-line tip.
- Publish the **Pompy Dashboard**: a **private** artifact that opens in Claude Code's artifact pane, designed for a good experience:
  - **Headline cards:** total prompts, average specificity score, favorite model, peak hour.
  - **Keywords & themes:** your most-used words and topics, and how they change over time.
  - **Model usage:** which models, how often, and for what kinds of prompts.
  - **When you use AI:** an hour × weekday heatmap, in your time zone.
  - **"Your top 3 habits"** with before/after snippet cards you can expand.
  - **Memory Lab v0:** shows the working set (context window) Pompy built for its last analysis: what went in, what was left out, and why.
  - A searchable list of your prompts with their scores. Works in light and dark mode and on phone width.
- Data delivery: `pompy publish` sends the precomputed results to the artifact's database. We'll also test whether the artifact can read Pompy's output files directly from this project (`files` capability), which would remove the publish step.
- **Done when:** you open the Pompy Dashboard in Claude Code and learn something new about your patterns.

### Step 9: Pompy Pre-flight v0 (prompt review at send time)
- A Claude Code `UserPromptSubmit` hook runs `pompy preflight` on every prompt you send.
- It scores the prompt with the Step 6 rubric, compares it with your known habits, and writes a short Pompy note such as *"Score 2/7. You usually leave out what 'done' looks like. Consider adding acceptance criteria."*
- **What Claude Code hooks allow** (checked against the hooks docs): a hook can't edit or rewrite your prompt, and can't open it for editing before it's sent. It can (a) add hidden context for Claude, or (b) block the prompt with a message you see, then you resend. So Pompy's note reaches you in these ways:
  - **Default (non-blocking):** the prompt goes through immediately. The note appears as a **macOS notification** and in the Dashboard's "Pre-flight feed." Every score is also logged, so Pompy can track whether you're improving.
  - **Strict mode (opt-in):** if a prompt scores below your threshold, Pompy blocks it and shows its suggestions. You revise and resend. A prefix such as `!!` skips the check for that prompt.
  - **Whisper mode (opt-in, an experiment):** Pompy also passes Claude a little project context through `additionalContext`, so Claude itself fills the gaps Pompy spotted. Good for comparing coached and uncoached sessions.
  - **True review before sending:** a `/pompy-check <draft>` command, and "Ask Pompy" in the Dashboard, for when you want feedback before committing to a prompt.
- **Fast by design:** it reads only one small precomputed profile file. No database scan, no network, no AI call. Target: under 100 ms. The hook gets a 2-second timeout and **fails open**: if anything goes wrong, your prompt goes through untouched.
- Session-end ingest: Claude Code gives `SessionEnd` hooks only about 1.5 seconds, so that hook just starts `pompy ingest` in the background and returns.
- Project-aware advice ("your goal in this project is…") arrives in Phase 3, once long-term memory knows each project's goal.
- **Done when:** every prompt you send in Claude Code gets a Pompy note, with no noticeable delay.
- 🧠 *Learning:* this is a memory system serving an agent in real time. The hot path must be tiny, so the heavy thinking happens ahead of time.
- **Done when:** you can open the Pompy artifact on claude.ai and learn something new about your prompting.

---

## Decisions
1. **Claude API key:** you'll provide it. It goes in a local `.env` file (git-ignored) and is needed for Claude-written summaries and rewrites (Step 8 and Phase 2).
2. **Prompt snippets in the artifact:** ✅ **yes.** The artifact stays **private**. Snippets let you see real before/after examples of your own prompts.
3. **Analysis window:** ✅ **all-time.** Pompy analyzes every chat it has ever captured, starting with everything still on disk at the first ingest, and never ages anything out. Shorter windows exist only as optional filters for trend comparisons (e.g. "this month vs. before").
4. **Artifact experience:** a polished, easy-to-read page, not a raw data dump. See Step 8.
