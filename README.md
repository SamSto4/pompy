# Pompy 🧠

**A personal pattern recognizer for how you prompt Claude.**

Pompy reads your Claude chat transcripts locally and builds a layered memory from them: short-term, episodic, and long-term. It then uses retrieval (RAG) over that memory to show you:

- **How you use Claude:** model mix, effort levels, when you work, session patterns
- **What you ask about:** keywords, recurring themes, action verbs
- **How specific your prompts are:** a specificity score, the friction it causes, before/after rewrites
- **Your AI preferences:** a profile that grows over time

Insights appear in a private Claude artifact.

> Status: **Phase 0, planning.** See the [Roadmap](docs/ROADMAP.md) and the [Starter Plan](docs/STARTER_PLAN.md).

## Why
1. To get better at prompting, using evidence from your own history.
2. To learn agentic memory by building it: working/context memory, episodic memory, consolidation, and RAG. See [Memory Concepts](docs/MEMORY_CONCEPTS.md).

## Privacy
Everything runs locally. Transcripts, the database, embeddings, and generated insights are git-ignored and never leave your machine except as the insights you choose to publish to your own private artifact. This repo contains code and synthetic test data only.

## Quick start (Phase 1, coming soon)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pompy ingest   # copy + parse ~/.claude/projects transcripts into data/pompy.db
pompy stats    # usage analytics
```

## License
MIT
