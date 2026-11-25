# Phase 4 Plan: Closing the loop

Goal: Pompy finds out whether its advice helps, and learns from that. Until now Pompy remembered what *you* did. In Phase 4 it also remembers what *it* told you, checks what you did next, and changes which tips it shows.

Rule carried over: Pompy never holds a prompt. Every tip appears **after** you send (the notch runs `pompy similar`), so a tip can only change your **next** prompt. Everything below is measured that way.

## Steps

### Step 21: The advice log ✅
- `pompy similar` (what the notch runs after each task prompt) now appends one line to `data/advice.jsonl` (git-ignored): when, the chat's session id (`--session`), the project (matched from the folder, see Step 24), a **fingerprint** of the prompt (a 16-character hash, not the words), its score and missing parts, every tip that *could* be shown, the ones that *were* shown, and whether this prompt was held back.
- Tip ids: `lesson:<friction kind>` (e.g. `lesson:problem`), `learned:<pattern key>`, `project:<part>` or `project:reminder`.
- `--no-log` skips the log (for trying things in the terminal). Questions and replies are never logged; only task prompts.
- **Memory concept:** episodic memory of the agent's **own actions**. To learn from advice you have to remember giving it.
- **Built:** `pompy/advice.py` (`digest`, `log`, `read_log`), `similar.log_row`, CLI flags `--cwd`, `--session`, `--no-log`.

### Step 22: Did it help? (`pompy impact`) ✅
- Each log line is matched to the prompt it was about (same fingerprint, within 15 minutes), then to the **next task prompt in the same chat** (within 30 minutes). Lines that match nothing, such as a terminal test, are left out.
- **The holdout:** about 1 task prompt in 5 gets no tip. The tips are still worked out and logged, just not shown. Which prompts are held back depends only on the fingerprint, so it is effectively random and the same prompt always gets the same answer. `POMPY_HOLDOUT=0` turns it off; `POMPY_HOLDOUT=0.3` changes the share.
- Reported: did the next exchange go wrong less often after a tip than after a held-back one (with a p-value); for each tip, how often your next prompt did what it asked ("acted on") with and without seeing it; and, separately, the month before advice started against everything since.
- **Memory concept:** a feedback signal, and a counterfactual. The before/after trend is reported but is not a fair test (your projects change too). The holdout is: the only difference between the two groups is the tip.
- **Built:** `advice.link`, `advice.impact`, `advice.trend`, `advice.render`, `pompy impact` (`--json`).

### Step 23: Procedural memory (which tips you act on) ✅
- For each kind of tip, over the **last 90 days**: how often you act on it when shown, and how often you do the same thing anyway (from the holdout, once it has 8 cases).
- **weight** = acted-on rate minus the without-tip rate (or minus 50% until the holdout has enough cases). Rates are smoothed toward 50% while cases are few.
- **status:** *learning* (shown fewer than 8 times), *works*, *quiet* (acted on under 15% of the time, or no more than without it), or *unmeasured* (the tip can't be seen in a prompt, such as "late at night").
- The notch shows tips in weight order and drops quiet ones, except on about 1 prompt in 10, so a quiet tip can earn its place back.
- `pompy impact` and `pompy refresh` write it to `out/advice.json`, a small file the notch reads instead of the database.
- **Memory concept:** procedural memory: "how to act", learned from outcomes. Episodic memory says what happened, semantic memory says what is true, procedural memory says what to do.
- **Built:** `advice.procedure`, `advice.profile`, `advice.decide`, `similar.advise`.

### Step 24: The project-aware notch ✅
- `pompy similar` works out which project the prompt came from: `--cwd`, else Claude Code's `CLAUDE_PROJECT_DIR`, else the current folder. It tries that folder and then its parents, so a chat opened in `src/pages` still finds its project.
- When that project has a goal fact (Step 19) still above 25% confidence, the notch adds: "This project: <goal>". When the project has at least 10 task prompts, it also names the part this prompt lacks that the project's prompts include least often: "Your prompts here include a done-definition only 12% of the time. Say how you'll know it worked."
- **Memory concept:** long-term memory feeding the working set. A consolidated fact is pulled into the small context Pompy builds for one prompt, only when it is relevant and still trusted.
- **Built:** `facts.project_key_for`, `facts.for_cwd`; `similar()` returns `project` and the full `tips` list.

### Step 25: Pompy as an MCP server (experiment, not started)
- Read-only tools so Claude can query your memory mid-session (search, an episode, a pattern, this project's facts).
- **Memory concept:** memory the agent fetches when it decides it needs it, instead of memory pushed into its context.

## What changes for the notch (the local Claude Code mod, outside this repo)
- It needs no change to keep working: the JSON keeps `lesson` and `pattern`, and clears them when a tip is held back or quiet.
- To show the project line, it should read the new `project.text` field (the text output of `pompy similar` already includes it).
- It should pass `--session <session id>`, and `--cwd <the chat's folder>` if it doesn't run in that folder, so the log and project match are right.

## Honest limits
- Steps 22 and 23 need weeks of logged tips before they say anything. Until then `pompy impact` reports "not enough on both sides to compare yet" and every tip stays *learning*.
- "Acted on" is a proxy: the next prompt having the part a tip asked for. A next prompt about something unrelated counts as not acting on it.
- Before this phase, `data/preflight.jsonl` was the only log of advice, written by the old blocking hook, which was removed. The advice log replaces it.

## Privacy
`data/advice.jsonl` holds fingerprints, ids and numbers only, never prompt text. `out/advice.json` holds tip ids and weights. Both are git-ignored.
