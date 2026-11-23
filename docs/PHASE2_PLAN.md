# Phase 2 Plan: Episodic memory + hybrid retrieval

Goal: Pompy remembers **what happened** in each exchange (not just what you typed), can find it **by meaning as well as by words**, and uses that to coach you: "last time you asked something like this, Claude had to ask which page you meant."

Built to [RAG_ARCHITECTURE.md](RAG_ARCHITECTURE.md), layers 2 to 5. Each step ends with a **done when** test and a 🧠 learning checkpoint.

## Decisions (checked on this Mac, Python 3.14.4)

| Need | Choice | Why |
|---|---|---|
| Embeddings | `fastembed` with `BAAI/bge-small-en-v1.5` (384 numbers per text, ~67 MB model, downloaded once) | Runs locally on the CPU, no API key, text never leaves the Mac. Installs on 3.14 (onnxruntime 1.32). |
| Vector store | NumPy arrays saved as SQLite BLOBs, searched by brute-force cosine | This Python's `sqlite3` can't load extensions, so `sqlite-vec` is out. At a few thousand exchanges a full scan takes a few milliseconds. |
| Merge | Reciprocal Rank Fusion (k = 60) | Combines keyword and meaning rankings without tuning weights. |
| Episode summaries | Structured fields computed locally now; Claude-written descriptions later (Haiku, batched) | Needs the API key in `.env`; the rest of Phase 2 doesn't. |

Embeddings are optional: `pip install -e ".[memory]"`. Without them every command still works in keyword-only mode.

## Steps

### Step 10: Exchange excerpts (layer 2) ✅
- New `excerpts` table, one row per **exchange**: your prompt (own words) + the start of Claude's reply + what happened next (friction kinds, Claude's question if it asked one).
- Each excerpt gets a **context header**: project · local date · the prompt just before it. A bare "fix it" then carries its meaning.
- FTS5 index over excerpts, rebuilt incrementally after `pompy ingest`. Source references (session, record uuid) on every row.
- `pompy search --in excerpts` and `pompy context` read excerpts instead of bare prompts.
- **Done when:** fixtures produce the expected excerpts (header, reply start, outcome), and `pompy context` cites what Claude said back.
- **Built:** `pompy/excerpts.py`. Excerpts are derived data, rebuilt in full at the end of every `pompy ingest` (still under 0.3 s on a few hundred prompts); `text_hash` lets Step 11 embed only changed text. `pompy search --exchanges` prints whole exchanges; `pompy context` snippets now carry Claude's reply and the outcome, so they cost more tokens each and fewer fit a budget. The Dashboard redacts reply text like prompt text.
- **First real run:** 352 exchanges. On the fixtures, a search for "bug" already finds "why does this happen" through its header (it followed "fix the bug"): context-aware retrieval working before any embeddings.
- 🧠 *Learning:* an episode is cause and effect, not a single message. Contextual retrieval: a chunk is only findable if it carries its context.

### Step 11: Local embeddings (layer 3b) ✅
- `pompy embed`: embeds new excerpts only (incremental, tracked by model name and text hash), stores float32 vectors in an `embeddings` table.
- `pompy search --mode meaning "make the button nicer"` finds "improve the CTA styling".
- **Done when:** a fixture test with a fake embedder proves the plumbing; a real run embeds every excerpt and a paraphrase query finds its match.
- 🧠 *Learning:* semantic memory is a geometry: similar meanings sit close together. See where it beats keywords and where it fails (exact file names, error codes).
- **Built:** `pompy/embed.py`, `pompy embed`, `pompy search --mode meaning`. Install with `pip install -e ".[memory]"`; the model lives in `~/.cache/pompy/models` (override with `POMPY_MODELS`). Queries get BGE's search instruction (`query_embed`), excerpts don't (`passage_embed`). A vector is reused while its excerpt's `text_hash` is unchanged, and a stale vector is never returned.
- **First real run:** 352 exchanges embedded in 25 s; a second run does nothing. "make the page look nicer on phones" finds website-redesign prompts where keyword search finds none; "save my work to github" finds "commit it, remind me which github repo…". BGE scores sit in a narrow band (0.6 to 0.8), so rank matters, not the raw number.
- **Found on the way:** artifact comments sent to Claude arrive as typed prompts. They are app-injected, so a later parse rule should drop them from your prompts.
- **Seen in tests:** a short follow-up inherits its predecessor's meaning through the context header, so it can outrank the original. Step 12's eval should check whether that helps or hurts.

### Step 12: Hybrid retrieval (layers 4 and 5)
- `pompy search --mode hybrid` (the new default): keyword and meaning results merged by RRF, with an optional recency × importance blend (importance = friction followed, or a high score).
- `WorkingSet.retrieve` uses hybrid, so `pompy context` and the Memory Lab get better picks without changing shape.
- A small **retrieval eval**: hand-labelled queries over the fixtures, scored for keyword vs meaning vs hybrid (hit rate at 5). Run on real data too, printed, never committed.
- **Done when:** hybrid matches or beats both halves on the eval.
- 🧠 *Learning:* fusion, and why you measure retrieval before trusting it.

### Step 13: Episode cards (three representations)
- `episode_cards`: one per chat: title, intent (first task prompt), outcome (how it ended: committed, abandoned, corrected), counts, friction, project.
- Cards are for **locating** only; evidence always cites excerpts.
- Later, with the API key: a 1–2 sentence description per card from Haiku, batched and cached.
- **Done when:** every chat has a card; `pompy episodes` lists them.
- 🧠 *Learning:* the three representations (original, excerpts, cards) each have their own job; a summary may point, never prove.

### Step 14: Pre-flight v1 and the Dashboard
- **Notch:** "Prompts like this one" from your history, with what happened after ("3 of 5 needed a correction"). Hot path rule kept: the notch reads a precomputed neighbour file; no model runs while you type.
- **Warm path:** a `SessionEnd` hook starts `pompy ingest && pompy embed` in the background.
- **Dashboard:** Ask your memory shows hybrid picks and why each was chosen; Ask Pompy shows similar past prompts by meaning.
- **Done when:** typing a prompt shows a relevant past exchange within the hot-path budget.
- 🧠 *Learning:* episodic recall in real time: heavy work ahead of time, a cheap lookup at the moment of use.

### Step 15: Theme clustering
- Cluster excerpt embeddings into your recurring themes; label each with its top keywords. Shown in the Dashboard's Overview.
- 🧠 *Learning:* unsupervised structure in episodic memory, the first step toward long-term memory (Phase 3).

## Privacy
Excerpts, embeddings and cards live in `data/pompy.db` (git-ignored). The model files live in the user cache, outside the repo. Tests use synthetic fixtures and a fake embedder, so CI never downloads a model.
