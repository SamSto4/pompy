export const meta = {
  name: 'parser-audit',
  description: 'Exhaustively audit how Pompy classifies real transcript records (read-only); verify each issue adversarially',
  whenToUse: 'Before building patterns on top of ingest, or after a Claude Code update changes the transcript format',
  phases: [
    { title: 'Scout', detail: 'map current rules and the real data, read-only' },
    { title: 'Find', detail: 'seven search lenses, repeated until two rounds find nothing new' },
    { title: 'Verify', detail: 'three skeptics per finding, each with a different lens' },
    { title: 'Report', detail: 'write out/parser-audit.md, then a completeness critic' },
  ],
}

// ── Ground rules every agent gets ────────────────────────────────────────────
const RULES = `
GROUND RULES (strict):
- READ-ONLY audit. Do NOT edit anything in pompy/, tests/, docs/ or data/. Do NOT run \`pompy ingest\`
  or \`--rebuild\` (another session may be using the database).
- You MAY run read-only Python or sqlite3 against data/pompy.db and read files under data/raw/ and
  ~/.claude/projects/. Use .venv/bin/python.
- Privacy: this repo is public. Never paste real prompt text beyond a 12-word snippet. Refer to
  records by uuid. Never include project names, emails or account IDs in output.
- The classification rules live in pompy/parse.py; the expected behavior is documented in
  tests/fixtures/README.md and tests/fixtures/expected.json.
`

const LENSES = [
  { key: 'false-negatives', prompt: 'Find HUMAN-WRITTEN user records that ingest DROPPED. Scan raw transcripts for type=user records whose uuid is NOT in the prompts table, and judge which ones a person actually typed (look at promptSource, origin, turnOrigin, flags, content shape).' },
  { key: 'false-positives', prompt: 'Find records in the prompts table that a person did NOT write: injected instructions, agent relays, templates, app-generated text, boilerplate repeated verbatim across sessions, very long machine-like texts.' },
  { key: 'markers', prompt: 'Census every <tag> and [bracketed marker] found in kept prompts AND in dropped user records. Classify each tag/marker as injected-by-tooling vs genuinely user content (e.g. pasted XML). Flag injected ones the rules miss and user ones the rules wrongly strip.' },
  { key: 'metadata', prompt: 'Enumerate every combination of promptSource / origin.kind / turnOrigin / textNotTyped / isMeta / entrypoint / Claude Code version seen on user records. Flag combinations the rules in parse.py do not explicitly handle, and version drift (older or newer transcript formats).' },
  { key: 'sessions', prompt: 'Audit session-level correctness: sessions with zero prompts (explain each), resumed/forked files and sessionId mismatches, sessions split or merged wrongly, missing cwd/title/timestamps, first_ts/last_ts anomalies, subagent counts.' },
  { key: 'turns', prompt: 'Audit assistant-side data: messages split across records (merge correctness), <synthetic> or missing models, effort vs perTurnEffort, missing/odd usage numbers, tool_use ids, sidechain leakage into turns.' },
  { key: 'events', prompt: 'Audit friction events: interrupts, rejected tool calls, compactions. Find real occurrences the detector misses (other wordings, list-shaped content) and events that are not actually user friction (e.g. denials by an automatic classifier rather than the user).' },
]

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          key: { type: 'string', description: 'stable slug, e.g. "markers:browser_instruction" or "fn:promptSource=foo"' },
          lens: { type: 'string' },
          kind: { type: 'string', enum: ['false_negative', 'false_positive', 'metadata', 'session', 'turn', 'event'] },
          description: { type: 'string' },
          evidence_query: { type: 'string', description: 'the exact SQL/Python that reproduces it' },
          affected_count: { type: 'number' },
          example_uuids: { type: 'array', items: { type: 'string' }, maxItems: 5 },
          snippet: { type: 'string', description: '12 words max, or empty' },
          proposed_rule: { type: 'string' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
        required: ['key', 'lens', 'kind', 'description', 'evidence_query', 'affected_count', 'proposed_rule', 'severity'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT = {
  type: 'object',
  properties: {
    real: { type: 'boolean' },
    corrected_count: { type: 'number' },
    reasoning: { type: 'string' },
  },
  required: ['real', 'reasoning'],
}

const VERIFY_LENSES = [
  { key: 'reproduce', ask: 'RE-RUN the evidence yourself. Does the issue exist exactly as stated, with roughly that count? If the query fails or the count is off, say so.' },
  { key: 'intent', ask: 'Judge INTENT: for the example records, would a careful human reader agree on who wrote them (the user vs tooling/agents)? Be strict: default to real=false if ambiguous.' },
  { key: 'fix-safety', ask: 'Judge the PROPOSED RULE: would it misclassify other real records or break tests/fixtures/expected.json? Test it against the data. real=false if the fix is unsafe or the issue is not worth a rule.' },
]

// ── Scout ────────────────────────────────────────────────────────────────────
phase('Scout')
const scout = await agent(
  `${RULES}\nYou are scouting for a parser audit of Pompy's ingest step. Read pompy/parse.py, pompy/ingest.py, pompy/store.py and tests/fixtures/README.md. Then profile the real data read-only: counts per table, record types in raw files, and anything surprising. Return a concise briefing (under 400 words) that other auditors will receive: the rules as implemented, table sizes, and leads worth chasing.`,
  { label: 'scout' },
)

// ── Find → Verify, loop until two dry rounds ─────────────────────────────────
const MAX_ROUNDS = (args && args.maxRounds) || 4
const seen = new Set()
const confirmed = []
const rejected = []
let dry = 0
let round = 0

while (dry < 2 && round < MAX_ROUNDS) {
  round++
  const already = [...seen].join(', ') || '(none yet)'
  phase('Find')
  const found = (await parallel(LENSES.map(l => () =>
    agent(
      `${RULES}\nSCOUT BRIEFING:\n${scout}\n\nYOUR LENS (${l.key}): ${l.prompt}\n\nAlready reported (do NOT repeat; find NEW issues only): ${already}\n\nReturn only issues backed by a query you actually ran. An empty list is a valid answer.`,
      { label: `find:${l.key}:r${round}`, phase: 'Find', schema: FINDINGS },
    ),
  ))).filter(Boolean).flatMap(r => r.findings)

  const fresh = []
  for (const f of found) {
    if (!seen.has(f.key)) { seen.add(f.key); fresh.push(f) }
  }
  log(`round ${round}: ${found.length} reported, ${fresh.length} new`)
  if (!fresh.length) { dry++; continue }
  dry = 0

  phase('Verify')
  const judged = await parallel(fresh.map(f => () =>
    parallel(VERIFY_LENSES.map(v => () =>
      agent(
        `${RULES}\nA parser auditor claims:\n${JSON.stringify(f, null, 2)}\n\nYOUR JOB (${v.key}): ${v.ask}`,
        { label: `verify:${v.key}:${f.key}`, phase: 'Verify', schema: VERDICT },
      ),
    )).then(votes => {
      const ok = votes.filter(Boolean)
      const yes = ok.filter(v => v.real).length
      return { finding: f, votes: ok, real: yes >= 2 }
    }),
  ))
  for (const j of judged.filter(Boolean)) (j.real ? confirmed : rejected).push(j)
}
if (round >= MAX_ROUNDS && dry < 2) log(`stopped at the ${MAX_ROUNDS}-round cap before two dry rounds; the tail may be unexplored`)

// ── Report + completeness critic ─────────────────────────────────────────────
phase('Report')
const report = await agent(
  `${RULES}\nYou may WRITE exactly one file: out/parser-audit.md (out/ is git-ignored). Write a report for the project owner, who is learning, in plain language:\n1. Summary: how trustworthy ingest is now (estimated misclassified records / total).\n2. Confirmed issues, highest severity first: what, how many, why it matters for pattern recognition, proposed rule.\n3. For each confirmed issue, a SYNTHETIC fixture case to add to tests/fixtures (made-up text in the real record shape, no real content).\n4. Rejected claims, one line each with the reason (so we learn what is NOT a problem).\n\nCONFIRMED:\n${JSON.stringify(confirmed, null, 2)}\n\nREJECTED:\n${JSON.stringify(rejected.map(r => ({ key: r.finding.key, why: r.votes.map(v => v.reasoning) })), null, 2)}\n\nReturn the report's summary section as your final text.`,
  { label: 'report' },
)

const critic = await agent(
  `${RULES}\nCompleteness critic for a parser audit. Lenses run: ${LENSES.map(l => l.key).join(', ')}; rounds: ${round}; confirmed: ${confirmed.length}; rejected: ${rejected.length}. Read out/parser-audit.md. What is missing: a record type never examined, a claim not verified, a data source unread (e.g. subagents/ files, very old or newest versions)? Return a short list of gaps worth a follow-up run, or "none".`,
  { label: 'critic' },
)

return {
  rounds: round,
  confirmed: confirmed.map(c => ({ key: c.finding.key, severity: c.finding.severity, count: c.finding.affected_count })),
  rejected: rejected.length,
  summary: report,
  gaps: critic,
  report_file: 'out/parser-audit.md',
}
