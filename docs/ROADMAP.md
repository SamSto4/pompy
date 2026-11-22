# Pompy Roadmap

**What Pompy is:** an experimental lab for understanding how **agent memory** works (short-term/context window, episodic, long-term), built around **pattern recognition** on your own Claude history.

**What it's for:** while we explore memory, Pompy puts it to work as a prompting coach. It recognizes your patterns, knows what you're working on in each project, and reviews your prompts *at the moment you send them*.

---

## The three parts of Pompy

| Part | Where you see it | What it does |
|---|---|---|
| **1. Memory pipeline** | Runs quietly on your Mac | Captures every chat, recognizes patterns, and builds the three memory layers |
| **2. Pompy Dashboard** | A private artifact, opened in **Claude Code's artifact pane** | Your keywords, model usage, when you use AI, your patterns, plus a **Memory Lab** that shows what Pompy remembers in each layer |
| **3. Pompy Pre-flight** | Inside Claude Code, every time you send a prompt | Scores the prompt, spots patterns ("you usually forget X here"), and suggests what to add based on your goal *in that project* |

```
  you type a prompt in Claude Code
            │
            ▼
  ┌─ Pompy Pre-flight (UserPromptSubmit hook, < 100 ms, no network) ─┐
  │  reads: project profile + your long-term habits (precomputed)    │
  │  shows: score · patterns spotted · "consider adding…"            │
  │         → macOS notification + Dashboard feed (default)          │
  │         → or blocks low scores so you can revise (strict mode)   │
  │  tells Claude: relevant project context (whisper mode, optional) │
  └──────────────────────────────────────────────────────────────────┘
  (Hooks can't edit a prompt before it's sent; for true review before
   sending, use /pompy-check or "Ask Pompy" in the Dashboard.)
            │                                   ▲
            ▼                                   │ precomputed profiles
  Claude answers ── session ends ──► incremental ingest ──► memory layers
                                                         (SQLite, local)
                                                                │
                                                                ▼
                                        Pompy Dashboard (private artifact)
```

---

## Memory layers (the experiment)

| Layer | Concept | In Pompy |
|---|---|---|
| **Short-term / context window** | What the agent is "thinking about" right now. It's limited, so what goes in matters. | The **working set** Pompy assembles for each job: for Pre-flight, the prompt you're sending + this project's profile + your top habits. The Memory Lab shows exactly what went in and why. |
| **Episodic** | Specific past events, with time and outcome | One **episode per session**: what you were trying to do, how it went, how many corrections, which model |
| **Long-term (semantic)** | General facts distilled from many episodes | **Your profile and project profiles**: preferences, habits, recurring gaps, each project's goal, all with confidence scores that strengthen or fade |

Your history is analyzed **all-time**, from Pompy's first ingest onward. Nothing ages out.

## Pattern recognition (the focus)

| Pattern family | Example insight | Technique (what you'll learn) |
|---|---|---|
| **Time** | "You prompt most 10pm–1am; late-night prompts are 30% shorter." | Histograms, local-time bucketing |
| **Model ↔ task** | "You pick Opus for planning, Sonnet for small edits." | Co-occurrence / contingency tables |
| **Vocabulary & themes** | "Your top themes: UI design, APIs, agents." | TF-IDF, n-grams, then embedding **clustering** |
| **Prompt structure** | "You rarely say what 'done' looks like." | Rule-based feature extraction → specificity score |
| **Friction sequences** | "Prompts with no file reference lead to 2× more corrections." | Sequence analysis (prompt → next turn), correlation |
| **Project patterns** | "In this project you're building X; prompts that mention Y go well." | Per-project aggregation + episode summaries |
| **Change over time** | "Your average specificity rose from 2.1 to 3.4 this month." | Trend lines, before/after comparisons |

---

## Phases

### Phase 0: Foundations ✅
Repo, plan, privacy rules, memory study guide.

### Phase 1: Capture, patterns v1, Dashboard v1, Pre-flight v0
- **Ingest:** copy chats into Pompy's permanent store, then parse real prompts out of the noise.
- **Patterns v1:** time, models, keywords, prompt structure (specificity score), friction.
- **Short-term memory:** an explicit working-set object, so you can see a context window being built.
- **Dashboard v1** (private artifact in Claude Code): keywords, model usage, activity times, top habits with before/after snippets of your real prompts.
- **Pre-flight v0:** a hook that scores each prompt as you send it and shows tips. Rule-based, instant, no AI call.

### Phase 2: Episodic memory + hybrid retrieval
- Built to the hybrid RAG design in [RAG_ARCHITECTURE.md](RAG_ARCHITECTURE.md): context-aware excerpts, keyword + vector indexes, merge and rerank, and source references back to the original.
- Summaries follow the **three representations** rule: original source, searchable excerpts (the main evidence), and short episode cards (for locating and filtering only).
- Each session becomes an episode: intent, outcome, friction, and a short summary written by Claude (Haiku, batched).
- Embeddings plus hybrid search (keyword + meaning), and theme clustering.
- **Pre-flight v1:** "Last time you asked something like this, it took 4 corrections because…"
- **Memory Lab:** browse your episodes and see which ones get retrieved for a prompt, and why (recency × relevance × importance).

### Phase 3: Long-term memory and consolidation
- A hypothesis agent compares cases, looks for supporting **and** contradicting evidence, and stores each pattern with its evidence (layers 6–8 of the RAG architecture).
- A consolidation job turns many episodes into durable facts: your preferences, habits, and **each project's goal**. Facts gain confidence when reinforced and decay when they stop showing up.
- **Pre-flight v2:** project-aware advice, e.g. "Your goal in this project is X. Consider stating which page and the acceptance criteria."
- **Memory Lab:** watch facts form, strengthen and fade over time.

### Phase 4: Closing the loop
- Measure whether Pre-flight advice helps: are your scores rising and corrections falling?
- **Procedural memory:** Pompy learns which kinds of tips you actually act on.
- "Ask Pompy" in the Dashboard: paste a draft and get a full rewrite.
- Experiment: Pompy as an MCP server, so Claude can query your memory mid-session.

### Phase 5: All your material (source adapters)
- Pompy's memory grows to cover everything: Claude chats, notes and documents, and business records, each through a source adapter (see [RAG_ARCHITECTURE.md](RAG_ARCHITECTURE.md#sources-everything-through-source-adapters--decided)).
- Order: claude.ai export → a documents folder (Markdown, text, PDF, Word) → business records (CSV and spreadsheets).
- Business-record snippets stay out of the artifact unless you allow a source.

---

## Efficiency: three speeds

| Speed | When it runs | What it does | Cost |
|---|---|---|---|
| **Hot** | Every prompt you send | Rule-based scoring + reading one small precomputed profile file. No database scan, no network, no AI call. Target: **under 100 ms**. | Free |
| **Warm** | When a session ends (hook starts it in the background; Claude Code allows the hook only ~1.5 s) | Incremental ingest that reads only **new lines** since last time (tracked by byte offset), updates stats, embeds new prompts | Free (local) |
| **Cold** | Nightly, weekly or on demand | Claude writes episode summaries and runs consolidation. Uses Haiku for bulk work, the **Batch API** (50% cheaper) for the first backfill, and **prompt caching** for repeated instructions. | Cents |

The Dashboard reads precomputed results, so opening it never triggers analysis.

## Tech choices
| Need | Choice | Notes |
|---|---|---|
| Language | Python 3.14, `venv` + `pip` | Already installed |
| Store | SQLite (+ FTS5 keyword search) | A few hundred to a few thousand prompts: one file, zero ops |
| Vectors | `sqlite-vec` or NumPy | Phase 2 decision; check 3.14 wheels first |
| Embeddings | Local model (e.g. `fastembed`) or Voyage API | Phase 2 decision gate |
| AI steps | Claude API: `claude-haiku-5-5` (bulk), `claude-sonnet-5-5` / `claude-opus-5-5` (consolidation, rewrites) | Key in git-ignored `.env` |
| Pre-flight | Claude Code `UserPromptSubmit` hook | Fast, local, non-blocking by default |
| Dashboard | Private claude.ai artifact, opened in Claude Code's artifact pane | Data delivered via the artifact's `db`; reading project files directly via the `files` capability is an experiment |

## Privacy rules (the repo is public)
1. Raw chats, the database, embeddings and generated insights are **never committed**. `.gitignore` enforces this.
2. Tests use **synthetic** chats only.
3. Docs and code never contain real prompts, project names, emails or account IDs.
4. Prompt snippets appear **only** in the private Pompy artifact.
5. The Claude API key lives in a git-ignored `.env` file.
