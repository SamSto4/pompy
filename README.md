# Pompy 🧠

**An experimental lab for agent memory, built on pattern recognition over your own Claude history.**

Pompy reads your Claude chat transcripts locally and builds three memory layers from them: short-term (the context window), episodic, and long-term. It then puts that memory to work as a prompting coach:

- **Pompy Dashboard** (a private artifact inside Claude Code): your keywords, model usage, when you use AI, your recurring patterns, and a Memory Lab that shows what Pompy remembers and why
- **Pompy Pre-flight** (a Claude Code hook): reviews each prompt as you send it, spots your patterns, and suggests what to add based on your goal in that project

> Status: **Phase 2 done.** Phase 1 (Steps 1–9): ingest, stats, keywords, search, specificity score, the short-term memory WorkingSet, the Pompy Dashboard, and Pre-flight. Phase 2 (Steps 10–15): exchange excerpts and episode cards (episodic memory), local embeddings, hybrid retrieval with an eval, recall of similar prompts, and themes. See the [Roadmap](docs/ROADMAP.md), the [Starter Plan](docs/STARTER_PLAN.md), the [Phase 2 Plan](docs/PHASE2_PLAN.md), and the [Hybrid RAG Architecture](docs/RAG_ARCHITECTURE.md).

## Why
1. To get better at prompting, using evidence from your own history.
2. To learn agentic memory by building it: working/context memory, episodic memory, consolidation, and RAG. See [Memory Concepts](docs/MEMORY_CONCEPTS.md).

## Privacy
Everything runs locally. Transcripts, the database, embeddings, and generated insights are git-ignored and never leave your machine except as the insights you choose to publish to your own private artifact. This repo contains code and synthetic test data only.

## Quick start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pompy ingest                 # copy + parse ~/.claude/projects transcripts into data/pompy.db
pompy stats                  # usage analytics (all-time; add --period this-month to narrow)
pompy keywords               # your words, phrases and opening style
pompy score                  # prompt specificity + friction
pompy compare this-month     # this month vs everything before: are you improving?
pompy context "nav bar bug"  # build a working set (short-term memory) and see what went in
pompy check "fix the bug"    # score a draft prompt
pompy publish                # build the Pompy Dashboard in out/dashboard/ (local only)
```

## License
MIT
