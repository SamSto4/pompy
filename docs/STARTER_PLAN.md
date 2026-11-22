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
  - `replies`: what Claude wrote back, one row per transcript record with text (thinking excluded)
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

### Step 5: Keywords and themes ✅
- **Your own words only:** prompts now store `own_text` (pasted blocks removed) next to the full `text`, so a pasted error log never counts as your vocabulary. Code, URLs, file paths, JSON keys and camelCase/snake_case identifiers are references, not words (Step 6 uses them).
- `pompy keywords` → `out/keywords.json`:
  - **Top terms**, ranked by how many prompts mention them (so one long prompt can't dominate), plus how many sessions; plurals folded ("pages" → "page").
  - **Recurring phrases** (two-word pairs like "nav bar", "free trial").
  - **Action verbs** tracked separately: which verb opens your prompts ("fix", "commit", "continue", …).
  - **Opening style:** command · question · request · reply · statement.
  - **Style markers:** "let's", "I want", constraints like "don't / never", "please", "thanks".
  - **Themes per project and per month** with TF-IDF (what's distinctive, not just frequent).
- `pompy search <words>`: **keyword search over every prompt** (SQLite FTS5, BM25 ranking, word stems match: "fixing bugs" finds "fix the bug"). Every hit carries its source reference (session + record id). This is layer 3a of the [RAG architecture](RAG_ARCHITECTURE.md).
- The store is versioned: when the schema or parsing rules change, the next `pompy ingest` rebuilds automatically from `data/raw/`.
- 🧠 *Learning:* keyword search is the "exact match" half of hybrid retrieval. Phase 2 adds the "meaning" half (embeddings) and merges the two.

### Step 6: Prompt Specificity Score v0 + friction signals ✅
- **Roles first:** only *task* prompts are scored. Replies ("yes"), short corrections ("try again"), routine commands ("commit and push") and wordless prompts are complete for what they do, so they aren't judged.
- **Rubric (0–7), rule-based and instant:** goal · context · constraints · done-definition · output format · example · concrete reference (file, error text, URL, code; pasted text and screenshots count).
- **Friction after a prompt** (between it and your next prompt in the session): **correction** ("no, …", "try again"), **problem report** ("why am I getting a 404", "isn't responding"; calibrated on real follow-ups), **interrupt**, **rejected tool call**, **rephrase** (nearly the same request within 10 min), **clarify** (Claude had to ask you: the AskUserQuestion tool, or a reply ending on a clarifying question like "which bug do you mean?" while it changed nothing; offers like "want me to commit?" and questions you asked for don't count).
- `pompy score` → `out/specificity.json` + a `prompt_scores` table: score distribution, session openers vs follow-ups, monthly trend, how often each feature appears, friction with vs without each feature, friction by score and length, and the low-scoring prompts that led to friction. Rates from fewer than 15 prompts are flagged "thin".
- `pompy check "<draft>"`: scores a draft before you send it and suggests what to add (the seed of Pre-flight, Step 9).
- **Clarify signal added:** Pompy now stores Claude's reply text (schema v4, auto-rebuild), so it sees when Claude had to ask. Friction coverage went from 8% to 18% of task prompts, and clarify became the largest kind. The same reply text feeds Phase 2's exchange excerpts. Limit: some AskUserQuestion calls are blockers (a missing app, a full disk) rather than gaps in the prompt.
- 🧠 *Learning:* a pattern is only as good as its signal. The first friction detector caught 3.8% of prompts; calibrating it against real follow-ups more than doubled that, and reading Claude's side of the conversation doubled it again. Always check what a detector misses before trusting its correlations.

### Step 7: Short-term memory object ✅
- `pompy/working_set.py`: a `WorkingSet` holds the analysis **window** (default **all-time**; or a period: `this-month`, `30d`, `2026-10`, `2026-09..2026-10`, plus an optional project), the current **question**, and the retrieved **snippets**, within an estimated token **budget**. `stats`, `keywords` and `score` all take a `WorkingSet`.
- **The lens:** the window is applied as temporary SQL views named like the real tables, so every existing query sees only in-window rows without being rewritten. Periods follow your local calendar.
- **Two rules kept by tests:** a windowed run never overwrites the all-time results (`prompt_scores`, `out/*.json`), and facts about one prompt (its friction, whether it opened a session) always come from full memory, so a prompt gets the same verdict in every window.
- `--period` / `--project` on `stats`, `keywords`, `score` and `search`. `pompy compare this-month [before|<period>]` shows two working sets side by side, flagging periods with too few prompts.
- `pompy context "<question>"` fills a working set: the best keyword matches (any word may match, best first), at most 2 per chat, until the budget runs out. It lists what went in, what was left out and why, and writes `out/working_set.json` for the Memory Lab (Step 8).
- First real comparison: this month's prompts are shorter (median 7 vs 13 words) and less specific (1.32 vs 1.74), mostly because examples (6% vs 26%) and concrete references (10% vs 21%) dropped.
- 🧠 *Learning:* this is the context window made explicit. Deciding what goes in it (the window, the budget, the per-chat cap) and recording what was left out is the core skill of agent memory design.

### Step 8: Insights v0 + the Pompy Dashboard ✅
- `pompy publish` **builds** the dashboard in `out/dashboard/` (page + scripts + `data.json`). Nothing leaves your Mac until you ask Claude to publish that folder to your **private** artifact (it updates the same artifact each time).
- **Data bundle:** all-time stats, keywords, scores, the top 3 habits, this-month-vs-before, the last working set, and one row per prompt. Every per-prompt fact (role, score, features, friction, session opener, local date) is decided in Python; the page only filters and aggregates. Only your own words are bundled (pastes excluded), shortened to 600 characters, and passed through a redaction pass (key-like values, emails, home paths, ids). Chats are numbered instead of named by id. Claude's replies are not bundled, except the short question Claude asked when a prompt is flagged "Claude had to ask".
- **Top 3 habits:** the rubric parts your task prompts include least often (rarity is solid evidence; friction differences are shown but don't rank, since they rest on few prompts). Each cites real prompts of yours.
- **The page** (a custom artifact, since the ready-made Data type can't ask Claude):
  - **Overview:** headline numbers, hour × weekday heatmap, prompts and specificity by month (with the gap months explained), score distribution, rubric coverage, words and phrases, opening style, models, projects.
  - **Habits:** each habit with evidence, a tip, and a real "before" prompt; **Rewrite with Claude** produces the "after" and scores it (saved privately).
  - **Prompts:** search and filter every prompt; open one to see why Pompy scored it that way, and **correct Pompy** (score too high/low, role wrong, friction missed/false alarm). Corrections are saved privately (verdicts only, no prompt text, tagged with the rubric version) and can be copied out as JSON to recalibrate the rules.
  - **Ask Pompy:** score a draft as you type (Pre-flight in the browser), see which habits it repeats, similar past prompts and how they went, and **Improve with Claude**.
  - **Memory Lab:** the three memory layers, the last working set from `pompy context` (what went in, what was left out, why), and an interactive working-set builder (question, window, token budget, per-chat cap).
  - **Compare:** any two periods side by side, matching `pompy compare`.
- **One scorer, two languages:** the browser scorer (`pompy/dashboard/scorer.js`) gets its regexes and word lists from Python, and `tests/test_scorer_parity.py` runs it in Node against `score_text`. On real data it agreed on all 333 prompts.
- **Not done:** the `files` capability (reading `out/` straight from the project, which would remove the publish step) was not tested: it needs the Claude Code project's id. Phone-width layout is designed for (single column under 560px) but was not checked in a real narrow window.
- 🧠 *Learning:* a dashboard is a **snapshot of long-term memory** taken at one moment, and the corrections you make on it are **human feedback** that the next version of the rules learns from.

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
