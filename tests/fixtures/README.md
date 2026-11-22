# Synthetic test chats

Everything here is **made up**. It copies the *shape* of Claude Code transcripts (`~/.claude/projects/<project>/<session>.jsonl`) so tests can prove Pompy reads them correctly, without any real chat ever entering this public repo.

- Resumed sessions copy earlier records into a new file; ingest de-duplicates by record id (tested in `test_ingest.py`).
- `projects/`: two fake sessions laid out like the real folder, plus one sub-agent file that ingest must skip.
- `expected.json`: the **answer key**. It lists which records are real human prompts, which are noise and why, and what sessions, events and model turns ingest must produce.

Real transcripts are mostly not human prompts. These files deliberately mix the hard cases found in real data:

| Case | Example | Keep as prompt? |
|---|---|---|
| Typed prompt | plain text, `promptSource: typed` | ✅ `typed` |
| Desktop / IDE prompt | text blocks, `promptSource: sdk`, with an injected `<ide_opened_file>` block | ✅ `typed` (injected block stripped) |
| Pasted text | `<pasted_content>` wrapper | ✅ `typed`, `has_paste` |
| Image + text | image block + `[Image: …]` marker | ✅ `typed`, `has_image` |
| Slash command | `<command-name>/compact</command-name>` | ✅ `command` |
| Shell command | `<bash-input>npm test</bash-input>` | ✅ `bash` |
| Accepted suggestion | `promptSource: suggestion_accepted` | ✅ `suggestion` |
| Queued prompt | `promptSource: queued` | ✅ `typed` |
| Older-version prompt | no `promptSource` | ✅ `typed` |
| Tool result | `tool_result` block | ❌ |
| Tool rejected | `tool_result` with `is_error` + rejection text | ❌ → event `tool_rejected` |
| Interrupt | `[Request interrupted by user…]` | ❌ → event `interrupt` |
| Compaction summary | `isCompactSummary` | ❌ → event `compaction` |
| Injected reminders | `isMeta`, `<system-reminder>`, `<local-command-caveat>` | ❌ |
| Shell output | `<bash-stdout>` | ❌ |
| App-injected text | `textNotTyped: true` (wake notices, relays, PR commands) | ❌ |
| Agent/system turns | `promptSource: system`, peer/task-notification origin, `host_synthetic`, `<wake>` | ❌ |
| Sub-agent turns | `isSidechain`, or files in `subagents/` | ❌ |
