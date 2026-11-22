# Agentic Memory: Concepts Pompy Implements

A study guide that grows with the project. Each concept is linked to where Pompy builds it.

| Memory type | Human analogy | In an agent | In Pompy | Phase |
|---|---|---|---|---|
| **Working / short-term (context)** | What you're holding in mind right now | Whatever is in the prompt / context window this turn | `WorkingSet`: the analysis window + current question + retrieved snippets | 1 |
| **Episodic** | "I remember that Tuesday when…" | Time-stamped records of specific past interactions | One episode per session: intent, outcome, friction, summary, embedding | 2 |
| **Semantic / long-term** | General facts you know | Distilled, durable knowledge about the user or world | Consolidated facts: preferences, habits, strengths, with confidence | 3 |
| **Procedural** | Knowing *how* to ride a bike | Learned rules/skills for acting | Which coaching styles and rewrites actually improve your prompts | 4 |

## Key mechanisms
- **Encoding:** what gets stored, and in what shape (raw text, summary, embedding, structured fields).
- **Retrieval:** how memories come back. Keyword (FTS) + semantic (vectors) + scoring by **recency × relevance × importance** (Park et al., *Generative Agents*, 2023).
- **Consolidation:** periodically compressing many episodes into a few long-term facts (like sleep moving memories from hippocampus to cortex).
- **Reflection:** building higher-level conclusions from lower-level facts ("you underspecify when you're in a hurry").
- **Forgetting / decay:** facts lose confidence when they aren't reinforced. Without this, memory gets stale and noisy.
- **Context management:** deciding what goes into the limited window. This is the main engineering trade-off in every agent.

## Further reading
- Park et al., *Generative Agents: Interactive Simulacra of Human Behavior* (2023): memory stream, retrieval scoring, reflection.
- Packer et al., *MemGPT* (2023): an OS-style split between main context and external memory.
- Sumers et al., *Cognitive Architectures for Language Agents (CoALA)* (2023): a taxonomy of working, episodic, semantic, and procedural memory.
- Lewis et al., *Retrieval-Augmented Generation* (2020): the original RAG paper.
