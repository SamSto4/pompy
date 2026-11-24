# Phase 3 Plan: Long-term memory

Goal: Pompy keeps **patterns** about how you work with Claude, each with the evidence for it **and against it**, a confidence that grows when new episodes agree and fades when they stop agreeing, and a status (hypothesis → supported → contradicted or retired). This is layers 6–8 of [RAG_ARCHITECTURE.md](RAG_ARCHITECTURE.md).

Rule carried over from Phase 2: every pattern cites **excerpts** (or originals) as evidence. A summary or a theme may point somewhere; it never proves anything.

## Order of work

The steps that need no API key come first. The Claude-powered agent (Step 18) waits for a key in a git-ignored `.env`.

### Step 16: The patterns store (layer 8) ✅
- Tables: `patterns` (statement, scope, kind, status, confidence, counts, first seen, last confirmed, source), `pattern_evidence` (which excerpts support or contradict it), `pattern_history` (confidence and status each time it was checked).
- `pompy/patterns.py`: upsert a pattern with its evidence; recompute status from the numbers; record history.
- **Done when:** a pattern can be stored, re-checked, strengthened, weakened and retired, and its history read back, in fixture tests.
- 🧠 *Learning:* long-term memory is not a summary. It is a claim plus its evidence plus how sure you are, kept up to date.
- **Built:** `pompy/patterns.py`. Status comes from the numbers: *supported* needs the claim's direction, q < 0.10 and at least 15 prompts on each side; the same evidence against it makes it *contradicted*; anything else stays a *hypothesis*. Confidence is (1 − q), scaled down while samples are small, and halves every 90 days without confirmation; a supported pattern that fades below 0.15 is *retired*. Every check is appended to its history.

### Step 17: The pattern miner (`pompy mine`, no LLM) ✅
- Forms hypotheses from your episodes and tests each one: for every rubric part, overall and inside each theme, "prompts without X went wrong more often than prompts with X". Also time of day (late night), prompt length (very short), and session openers vs follow-ups.
- Each test is a two-proportion comparison with a p-value, and a minimum sample on both sides. Supporting cases: prompts that lacked X and went wrong. Contradicting cases: prompts that lacked X and went fine, and ones that had X and still went wrong. A few of each are cited.
- Re-running `pompy mine` is consolidation: a pattern that still holds is confirmed; one whose effect shrinks loses confidence; one that reverses is marked contradicted.
- **Done when:** fixture tests cover supported, contradicted, not-enough-evidence and decay; a real run lists patterns with their evidence.
- 🧠 *Learning:* a hypothesis is only as good as the search for counter-evidence. Testing every candidate also means some "findings" are chance, so p-values are reported, not hidden.
- **Built:** `pompy/mine.py`, `pompy mine` (`--all` lists every claim), `pompy patterns` (each pattern with its evidence on both sides; `--all` includes unproven ones). q-values use Benjamini–Hochberg.
- **First real run: nothing survived.** 14 claims over 282 task prompts; none supported, none contradicted. The rubric parts barely move how often something goes wrong for you (prompts without context 17% vs with 13%; without a done-definition 16% vs with 27%, but only 11 prompts had one). The closest call: inside the agents-and-models theme, prompts without context went wrong 22% vs 6% (p 0.15, n 73 vs 16), which is worth re-checking as more prompts arrive.
- **What that teaches:** the rubric came from general prompting advice, and your own history doesn't (yet) show it predicting friction. Either the effect is small, the friction signal is too noisy, or the samples on the "with" side are too thin (only 11 prompts had a done-definition). This is exactly why claims are tested before Pompy believes them, and the reason Step 18's agent should look for other explanations instead of re-asserting the rubric.

### Step 18: The hypothesis agent (`pompy think`, needs the API key)
- A Claude agent with tools over Pompy's memory: `search`, `get_excerpt`, `get_episode_card`, `get_pattern`, `propose_pattern`. It reads the miner's strongest patterns and the themes, proposes new hypotheses in plain words, and must find supporting **and** contradicting excerpts before it can store one.
- Weekly and on demand; Haiku for bulk work, a larger model to judge; prompt caching for the fixed instructions.
- **Done when:** a run stores at least one new pattern with cited evidence on both sides, and its cost is printed.
- **Built:** `pompy/think.py`, `pompy think` (`--dry-run` prints the instructions, briefing and tools and sends nothing; `--model`, `--effort`). A manual tool loop on the Messages API with `claude-opus-5-5` by default, effort `high`, server-side refusal fallback (`fallbacks: "default"`), and the fixed instructions cached. Read-only tools (`search`, `get_excerpt`, `get_episode_card`, `list_patterns`) plus `propose_pattern`, which refuses a pattern unless it cites at least 3 supporting and 1 contradicting exchange, all retrieved in the same run (no invented evidence), and stores it as a *hypothesis*: the model proposes, `pompy mine` decides. At most 3 proposals and 24 turns per run; tokens, cache reads and an estimated cost are printed. The key is read from the git-ignored `.env`. Tests drive it with a scripted fake client.
- **First real run:** 22 turns, 47 tool calls, 267 excerpts sent, 2:10 minutes, about **$3.55**. It stored 2 hypotheses with evidence on both sides, and ruled out five other ideas with reasons (unchecked visual tweaks, handoffs, missing links, "continue" during long runs, multi-part messages):
  1. Asking Claude to plan first *in the same message* that lists changes leads it to build straight away, and friction follows (3 for, 3 against; plan-only messages went fine).
  2. In Next.js work, page or nav changes checked only by the type check, not by loading the pages, are often followed by a 404 report (3 for, 2 against; limited to routing, since unchecked visual tweaks went fine).
  Both are hypotheses, not findings: three cases each.
- **Fixed after that run:**
  - Only the fixed instructions were cached, so 96% of 828k input tokens were paid in full as the conversation grew. The whole conversation is now cached each turn (top-level `cache_control`).
  - Statements came back long and were cut mid-sentence. The instructions now ask for one sentence of at most 200 characters, and an over-long one is trimmed at a word.
  - The API rejected `minimum`/`maximum` on an integer in a strict tool schema. The bound moved into code, and a test checks the schemas.
  - `pompy patterns` now shows agent hypotheses as "not yet tested by statistics" instead of empty rates.
- 🧠 *Learning:* the agent's value showed up exactly where the miner's could not. It found claims about *situations* (a plan request mixed with build instructions; routing changes checked by type check alone), not about missing rubric parts. And it ruled ideas out using counter-examples, not just in favour of them.

### Step 19: Consolidation into facts, and decay ✅
- A schedule (`pompy consolidate`): re-check every pattern against new episodes; confidence decays with time since last confirmation; patterns below a floor retire.
- Project facts: each project's goal and your preferences there, from its episode cards (Haiku, with the key).
- **Done when:** a pattern that stops showing up visibly fades and retires in a fixture timeline.
- **Built:**
  - `pompy/facts.py`, `pompy facts` (`--dry-run` shows each project brief and sends nothing). For each project with at least 2 chats and 5 exchanges, one structured-output call (`claude-haiku-5-5`, JSON schema) returns its goal and up to 3 preferences that recur across chats. Each is stored as a fact citing the chats' first exchanges. Briefs pass through the same redaction as the dashboard, so home paths, keys and emails never leave the Mac as typed. A preference that stops recurring is retired.
  - Facts start at a confidence set by how many chats back them, and their `last_confirmed` is the project's last activity, so they fade with the same 90-day half-life as patterns and retire below 0.15.
  - `pompy/consolidate.py`, `pompy consolidate` (`--facts` to refresh project facts with the API): re-checks the statistical claims, retires agent hypotheses not re-checked within 180 days, fades and retires stale patterns and facts, and reports what is new, strengthened, weakened and retired.
- **First real pass:** 6 project goals for about $0.001 (Haiku, 4.3k tokens in). No recurring preferences yet. Fading already shows: projects last active in September sit near 0.20, against 0.48 for one active last week.
- 🧠 *Learning:* forgetting is a feature. A memory that only grows keeps confident facts about projects you abandoned. Here every fact has to be re-earned by new episodes or let go.

### Step 20: Showing what Pompy has learned
- Dashboard: "What Pompy has learned": each pattern, its confidence, and the evidence on both sides. Memory Lab: watch a pattern's confidence over time.
- The notch's after-send line uses a supported pattern when one applies to the prompt just sent.

## Privacy
Patterns and evidence live in `data/pompy.db` (git-ignored). The agent sends excerpts to the Claude API only when you run `pompy think`, and prints what it sent.
