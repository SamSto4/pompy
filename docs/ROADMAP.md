# Pompy Roadmap

**North star:** Pompy helps you write sharper, more specific prompts for Claude, and builds a growing picture of how you like to work with AI.

**How it learns:** a local pipeline reads your Claude transcripts, turns them into **short-term → episodic → long-term memory**, and uses retrieval (RAG) over that memory to produce insights. A private Claude artifact shows those insights.

Each phase teaches one memory concept and ships something you can use. Prompt-coaching insights come in Phase 1, not at the end.

---

## Architecture at a glance

```
 ~/.claude/projects/**/*.jsonl          (Claude Code transcripts; auto-deleted after ~30 days by default)
              │  ingest (copy + parse, idempotent)
              ▼
 ┌──────────────────────── local machine (never committed) ────────────────────────┐
 │  data/raw/        durable copy of transcripts                                    │
 │  data/pompy.db    SQLite: sessions · prompts · turns · metrics                   │
 │                                                                                  │
 │  SHORT-TERM  ── working set: the current analysis window + question in context   │
 │  EPISODIC    ── one "episode" per session: goal, outcome, friction, summary,     │
 │                 embedding, timestamp                                              │
 │  LONG-TERM   ── consolidated facts: preferences, habits, prompt weaknesses,      │
 │                 each with confidence + evidence links + decay                     │
 │                                                                                  │
 │  retrieval = keyword (FTS5) + vector similarity + recency × importance           │
 └──────────────────────────────────────────────────────────────────────────────────┘
              │  export derived insights (aggregates + chosen examples)
              ▼
   Pompy artifact on claude.ai (private)  ── dashboard + "Ask Pompy" coach
```

**Why a local pipeline?** A published artifact can't read files on your Mac. Pompy does the heavy work locally, then pushes only the derived insights to the artifact. The artifact can store them in its database (`db` capability) and ask Claude questions directly from the page (`sample` capability).

**Scope:** Claude Code transcripts come first because they're already on disk and include the model, effort, timestamps, and prompts. A claude.ai data export is added in Phase 5.

---

## Phase 0: Foundations ✅ (this session)
- Repo, roadmap, starter plan, privacy rules (`.gitignore` blocks all personal data).
- **Concept:** what "memory" means for an agent (see [MEMORY_CONCEPTS.md](MEMORY_CONCEPTS.md)).

## Phase 1: Ingest, short-term memory, first prompt insights (MVP)
- `pompy ingest`: copy transcripts into a durable store before Claude Code's cleanup deletes them, then parse them into SQLite.
- Separate **real human prompts** from noise: tool results, injected/meta content, compaction summaries, and sub-agent turns.
- **Usage analytics:** model mix, effort levels, time of day and weekday (in your local timezone), session length, and prompt length.
- **Keyword and topic analysis:** TF-IDF terms, recurring verbs ("fix", "make", "build"), slash commands.
- **Prompt Specificity Score v0** (rule-based rubric): goal · context · constraints · acceptance criteria · output format · examples · file/code references.
- **Friction signals:** what happens right after a prompt. Corrections ("no", "actually", "I meant"), interrupts, rejected tool calls, rephrasing.
- **Short-term memory:** a working-set object (last N sessions plus the current question) that every analysis step reads from. This is the "context window" concept made explicit.
- **Ships:** the first Pompy dashboard artifact, with usage charts, a specificity score, and your top 3 prompt habits with before/after rewrites.

## Phase 2: Episodic memory
- Turn each session into an **episode**: intent, outcome (did it land?), number of corrections, model used, duration, and a 2–3 sentence summary written by Claude.
- Embed episodes and prompts. Hybrid retrieval: SQLite FTS5 plus vector similarity.
- **RAG query:** "show me past sessions like this one, and what went wrong."
- **Concept:** episodic vs. semantic memory, plus the retrieval score *recency × relevance × importance* from the Generative Agents paper.
- **Ships:** a "Similar past sessions" panel and per-episode drill-down.

## Phase 3: Long-term memory and consolidation
- A **consolidation job** (like sleep for the brain) distills many episodes into durable facts:
  - *Preferences:* "Prefers Opus for architecture, Sonnet for quick edits."
  - *Habits:* "UI prompts rarely state acceptance criteria → 2.3× more corrections."
  - *Strengths:* "Prompts that cite file paths succeed on the first try 80% of the time."
- Each fact has a confidence score and links to its evidence. Facts are **reinforced** when new episodes agree with them and **decay** when they're not seen again.
- **Reflection:** periodic higher-level insights built from lower-level facts.
- **Ships:** a "Your AI profile" page that evolves over time.

## Phase 4: The coaching loop
- **Ask Pompy** (in the artifact): paste a draft prompt. Pompy retrieves your relevant habits and past episodes, then rewrites the prompt and explains each change.
- A weekly insight report: what improved, what to try next.
- Track your specificity score over time and check whether coaching actually helps.
- **Concept:** procedural memory, meaning Pompy learns which coaching styles work for *you*.

## Phase 5: More sources and automation
- Import claude.ai conversations (Settings → Privacy → Export data).
- Automatic ingest via a Claude Code `SessionEnd` hook or a scheduled job.
- **Pompy as an MCP server**, so Claude itself can query your Pompy memory while you work. This closes the loop: your preferences shape future sessions directly.

---

## Tech choices (and why)
| Need | Choice | Notes |
|---|---|---|
| Language | Python 3.14, `venv` + `pip` | Already installed. |
| Store | SQLite (+ FTS5) | A few hundred prompts doesn't need a vector database. One file, zero ops. |
| Vectors | `sqlite-vec` or NumPy cosine search | Decide in Phase 2. Check that Python 3.14 wheels exist first. |
| Embeddings | Local model (e.g. `fastembed`) **or** Voyage API | Phase 2 decision gate: verify 3.14 wheels. Fallback is a 3.12 venv. |
| LLM steps | Claude API (`claude-haiku-5-5` for bulk summaries, `claude-sonnet-5-5` / `claude-opus-5-5` for consolidation and coaching) | API key from an env var, never committed. |
| UI | Claude artifact (Data type dashboard → custom page with `db` + `sample`) | Private by default. |

## Privacy rules (non-negotiable; the repo is public)
1. Raw transcripts, the database, embeddings, and generated insights are **never committed**. `.gitignore` enforces this.
2. Tests use **synthetic** fixture transcripts only.
3. Docs and code never contain real prompts, project names, emails, or account IDs.
4. Exports to the artifact default to aggregates. Prompt snippets are opt-in and stay in the private artifact.
