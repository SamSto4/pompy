# Pompy's Hybrid RAG Architecture

This is how Pompy finds evidence in your history, forms hypotheses about your patterns, and remembers what it learns. Every layer maps to a memory concept.

```
  Claude Code chats (now) · claude.ai export · notes & documents · business records
                       ↓
  1. Preserve original source ................ data/raw/ (byte-for-byte)   ✅ Step 3
                       ↓
  2. Context-aware chunks + metadata ......... "excerpts" table            Phase 2
                       ↓
        ┌──────────────┴──────────────┐
        │                             │
  3a. Keyword / exact match      3b. Vector / meaning                     Phase 2
      SQLite FTS5 (BM25)             embeddings (sqlite-vec / NumPy)
        │                             │
        └──────────────┬──────────────┘
                       ↓
  4. Merge + rerank .......................... reciprocal rank fusion → reranker
                       ↓
  5. Relevant excerpts + source references ... every hit points back to layer 1
                       ↓
  6. Agent compares cases, forms hypotheses .. Claude, with search tools    Phase 3
                       ↓
  7. Retrieves more evidence when needed ..... looks for supporting AND contradicting cases
                       ↓
  8. Stores pattern + evidence ............... "patterns" table = long-term memory
```

## The layers

**1. Preserve the original source** ✅
`pompy ingest` keeps a byte-for-byte copy of every transcript in `data/raw/`, then parses it into SQLite (`sessions`, `prompts`, `turns`, `tool_uses`, `events`). Nothing is ever summarized away. `pompy ingest --rebuild` can recreate everything from these copies.

**2. Context-aware chunks + metadata**
- **Chunk unit for conversations: an exchange** ✅ decided: one of your prompts + an excerpt of Claude's reply + what happened next (a correction, an interrupt, or moving on). This captures cause and effect, which suits pattern-finding.
- **Chunk unit for documents and records:** a section or paragraph (documents) or a row or record group (business records), each with the same context header.
- **Context-aware:** before indexing, each chunk gets a short header such as *project · session title · what came just before*. A bare "fix it" then still finds the right meaning. This is Anthropic's *Contextual Retrieval* technique.
- **Metadata** for filtering and comparing: session, project, local time, model, effort, prompt kind, specificity score, friction flags.

**3. Two indexes, side by side**
- **Keyword (3a):** SQLite FTS5 with BM25 ranking. Best for exact terms: file names, error text, tool names, slash commands.
- **Meaning (3b):** embeddings, ✅ computed **locally on your Mac** (free, and text never leaves the machine for this step). Best for paraphrases: "make the button nicer" ≈ "improve the CTA styling."

**4. Merge + rerank**
- **Reciprocal Rank Fusion** combines both result lists without tuning weights.
- Then an optional **reranker** reorders the top ~30. Experiment: compare none vs. Claude Haiku vs. a local cross-encoder.
- Final score can blend in **recency × importance**, the Generative Agents retrieval formula.

**5. Excerpts with source references**
Each hit carries a pointer: `session_id`, record `uuid`, raw file. The agent can always drill down to the original.

**6–7. Agent compares cases, forms hypotheses, gathers more evidence**
A Claude agent runs ✅ **weekly + on demand** (`pompy think`), on the cold path and never in Pre-flight. It gets tools: `search(query, filters)`, `get_excerpt(ref)`, `get_original(ref)`, `get_episode_card(session)`. It forms a hypothesis, e.g. *"UI prompts without acceptance criteria lead to more corrections."* Then it deliberately searches for **supporting and contradicting** cases before deciding.

**8. Store the pattern + its evidence**
```
patterns(id, statement, scope (global | project), status (hypothesis | supported | contradicted | retired),
         confidence, supporting_refs[], contradicting_refs[], first_seen, last_confirmed)
```
This is **long-term memory**. New evidence reinforces a pattern or weakens it, and unconfirmed patterns decay. Pre-flight reads only the precomputed patterns, so the hot path stays instant.

---

## Sources: everything, through source adapters ✅ decided

Pompy's memory will cover **all your material**: Claude chats, notes and documents, and business records. Each source plugs in through one small interface, so layers 2–8 work the same for every source:

```
class Source:                      # one adapter per kind of material
    name: str                      # "claude_code", "claude_ai", "docs", "records"
    def discover() -> files        # what's new or changed
    def preserve(file)             # layer 1: keep the original
    def records(file) -> items     # parsed units with timestamps + metadata
    def chunks(items) -> excerpts  # layer 2: context-aware excerpts
```

| Adapter | Material | Order |
|---|---|---|
| `claude_code` | Claude Code transcripts | ✅ built (Step 3) |
| `claude_ai` | claude.ai data export | after Phase 2 |
| `docs` | a folder you choose: Markdown, text, PDF, Word | after Phase 2 |
| `records` | business records: CSV / spreadsheets / exports you choose | after `docs` |

**Order of work:** the memory engine (excerpts, both indexes, rerank, agent, patterns) is built and proven on your Claude chats first. Each new adapter then plugs in without changing the engine. Cross-source patterns become possible, e.g. *"prompts that reference your spec doc need fewer corrections."*

**Privacy for business records:** they stay local like everything else, and nothing from them is ever committed. They may include other people's data (clients, customers), so **record snippets are left out of the artifact by default**. The Dashboard shows patterns and counts from records, and a snippet appears only for sources you explicitly allow.

## Summaries: three representations, each with its own job

Summaries are never the only version Pompy can consult.

| Representation | Purpose | What the agent receives | Where in Pompy |
|---|---|---|---|
| **Original source** | Preserve the full evidence | Only when deeper inspection is needed (`get_original`) | `data/raw/` + full `prompts` / `turns` rows |
| **Searchable excerpts** | Retrieve specific statements and details | **Usually the main evidence** | `excerpts` table (layer 2), both indexes |
| **Short descriptions + structured fields** | Help locate, filter and compare material | To decide *where to look*, never as proof | `episode_cards`: title, 1–2 sentence description, intent, outcome, project, model, counts |

**Rule:** every stored pattern must cite **excerpts** (or originals) as evidence. A summary can point the agent somewhere, but it can't support a claim.

## How this maps to the memory layers

| Memory layer | In this architecture |
|---|---|
| **Short-term / context window** | What the agent loads for one task: the question + top-k excerpts + a few episode cards, within a token budget. The Memory Lab shows this. |
| **Episodic** | Excerpts and episode cards: specific, time-stamped events |
| **Long-term** | Patterns with supporting and contradicting evidence |

## Efficiency notes
- Chunking, keyword indexing and embedding run on the **warm path** (after each session, incremental, local).
- Episode-card descriptions and the hypothesis agent run on the **cold path** (batched, Haiku first, prompt caching).
- Pre-flight never runs retrieval or the agent. It reads precomputed patterns, plus at most a millisecond-fast FTS lookup for "similar past prompts in this project."
