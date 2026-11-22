"""Step 5: keywords and themes. The second pattern family: *what* you ask Claude about.

Works on `own_text` (only the words you wrote, pasted text removed) of typed prompts, so
a pasted error log or a slash command never drowns out your own vocabulary.

Techniques (all plain Python, no ML yet):
- tokenizing + stopwords   → content words
- frequency + spread       → top terms (how often, in how many prompts / sessions)
- adjacent pairs           → recurring phrases (bigrams)
- TF-IDF across groups     → what makes each project / month distinctive (themes)
- opening-word analysis    → how you start prompts (commands, questions, requests, replies)
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import tzinfo

from pompy.stats import parse_ts
from pompy.working_set import WorkingSet

# fmt: off
STOPWORDS = frozenset("""
a about above after again against all almost also although always am among an and another any
anything anyway are aren't around as at away back be became because been before being below
between both but by can can't cannot could couldn't did didn't do does doesn't doing don't done
down during each either else enough even ever every everything few for from further get gets
getting go goes going gone got had hadn't has hasn't have haven't having he her here hers herself
him himself his how however i i'd i'll i'm i've if in into is isn't it it's its itself just keep
know let let's lets like ll made many may maybe me might mine more most much must my myself need
needs never no nor not nothing now of off often oh ok okay on once one only or other others our
ours ourselves out over own per please pretty quite rather re really right said same say says see
seem seems shall she should shouldn't since so some something somehow still such sure than thank
thanks that that's the their theirs them themselves then there there's these they they're thing
things think this those though through thus to too toward towards under until up upon us very via
want wants was wasn't way we we'd we'll we're we've well were weren't what what's whatever when
where whether which while who whom whose why will with within without won't would wouldn't yeah
yes yet you you'd you'll you're you've your yours yourself yourselves hi hello hey thx pls ve
etc i.e e.g eg ie lol tho gonna wanna gotta kinda sorta stuff alright based basically actually
literally currently already whole able lot bit anymore using instead
""".split())

# Verbs that open or drive a request. Tracked separately, so topic keywords stay nouns-ish.
ACTION_VERBS = frozenset("""
add adjust analyze answer apply ask audit automate build change check clean clear commit compare
connect continue convert copy create debug delete deploy describe design document draft edit
enable explain explore export fetch find finish fix generate give help implement improve import
install integrate investigate launch list load look make merge migrate move open optimize organize
plan polish publish pull push put read rebuild redesign refactor remove rename replace research
reset restart review rewrite run save scan search send set setup ship show simplify sort split
start stop summarize switch sync test track try turn undo update upgrade upload use verify write
keep ignore get identify restructure redeploy enlarge tailor clone reload rotate allow archive
""".split())

# Words that may precede the real first verb: "ok so now please fix …".
# ("let's" is NOT filler: it opens a request, e.g. "ok let's make a plan".)
OPENING_FILLER = frozenset(
    "ok okay so now also and then please pls just alright great cool perfect awesome next "
    "first hey hi".split()
)
# A short prompt starting with one of these is a shell command typed into the chat.
SHELL_WORDS = frozenset("npm npx pnpm yarn git sudo brew pip pip3 python python3 node wrangler "
                        "cd ls curl docker make cargo uv".split())
REQUEST_OPENERS = ("can you", "could you", "would you", "will you", "i want", "i need",
                   "i'd like", "i would like", "help me", "let's", "lets")
QUESTION_OPENERS = frozenset(
    "what why how when where which who whom whose is are was were do does did can could "
    "should would will has have".split()
)
REPLY_WORDS = frozenset(
    "yes no ok okay sure yep nope continue proceed approved approve accept go done thanks great "
    "perfect correct right agreed".split()
)
SHORT_TOKENS = frozenset("ai ui ux db js ts qa pr ci os ml llm".split())  # kept despite length
# fmt: on

STYLE_MARKERS = {
    "please": re.compile(r"\bplease\b|\bpls\b"),
    "thanks": re.compile(r"\bthanks?\b|\bthank you\b|\bthx\b"),
    "can/could you": re.compile(r"\b(can|could|would) you\b"),
    "let's": re.compile(r"\blet'?s\b"),
    "I want / I need": re.compile(r"\bi (want|need)\b"),
    "make sure": re.compile(r"\bmake sure\b"),
    "don't / never (constraint)": re.compile(r"\b(don'?t|do not|never|avoid)\b"),
    "must / should": re.compile(r"\b(must|should)\b"),
}

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:\S*[/\\]\S+|\b[\w-]+\.(?:[a-z]{1,5})\b)")
_WORD_RE = re.compile(r"[a-z][a-z0-9+#]*(?:'[a-z]+)?")
# Code identifiers and JSON keys from text pasted without paste tags (e.g. editor diagnostics).
_JSON_KEY_RE = re.compile(r'"[^"\n]{1,40}"\s*:')
_IDENTIFIER_RE = re.compile(r"\b(?:[a-z]+[A-Z][A-Za-z0-9]*|\w+_\w+)\b")
# "A.I." / "U.I" → "ai" / "ui", so dotted acronyms aren't split into stopwords "a" and "i".
_DOTTED_ACRONYM_RE = re.compile(r"(?<![\w/.])([A-Za-z])\.([A-Za-z])\.?(?![\w/])")
# @-mentions of files: @"path with spaces" or @path/to/file.
_MENTION_RE = re.compile(r'@"[^"\n]*"|@\S+')


def clean_for_words(text: str) -> str:
    """Remove code, URLs and file paths: they're references, not vocabulary (see Step 6)."""
    text = _DOTTED_ACRONYM_RE.sub(r"\1\2", text.replace("’", "'"))
    for pattern in (
        _CODE_FENCE_RE,
        _INLINE_CODE_RE,
        _URL_RE,
        _MENTION_RE,
        _PATH_RE,
        _JSON_KEY_RE,
    ):
        text = pattern.sub(" ", text)
    return _IDENTIFIER_RE.sub(" ", text).lower()  # camelCase / snake_case = code, not words


def words(text: str) -> list[str]:
    """All word tokens in reading order (stopwords included)."""
    return _WORD_RE.findall(clean_for_words(text))


def is_content(token: str) -> bool:
    if token in STOPWORDS or token in ACTION_VERBS:
        return False
    return len(token) >= 3 or token in SHORT_TOKENS


def content_terms(text: str) -> list[str]:
    return [t for t in words(text) if is_content(t)]


def bigrams(text: str) -> list[str]:
    toks = words(text)
    return [
        f"{a} {b}" for a, b in zip(toks, toks[1:], strict=False) if is_content(a) and is_content(b)
    ]


def opening_verb(text: str) -> str | None:
    for token in words(text)[:6]:
        if token in OPENING_FILLER:
            continue
        return token if token in ACTION_VERBS else None
    return None


def opening_style(text: str) -> str:
    """command · question · request · reply · statement · other (no words: a bare path, URL, id)"""
    first = text.strip().split()[0].lower() if text.strip() else ""
    if first in SHELL_WORDS and len(text.split()) < 8:
        return "command"  # e.g. "npm run dev" typed into the chat
    lowered = " ".join(words(text))
    toks = lowered.split()
    if not toks:
        return "other"
    if len(toks) <= 4 and toks[0] in REPLY_WORDS:
        return "reply"
    stripped = [t for t in toks if t not in OPENING_FILLER] or toks
    joined = " ".join(stripped)
    if joined.startswith(REQUEST_OPENERS) or lowered.startswith(REQUEST_OPENERS):
        return "request"
    if stripped[0] in ACTION_VERBS:
        return "command"
    if stripped[0] in QUESTION_OPENERS or text.rstrip().endswith("?"):
        return "question"
    return "statement"


def fold_plurals(counts: Counter) -> Counter:
    """Merge 'pages' into 'page' when both appear, so one idea is counted once."""
    folded = Counter(counts)
    for term in list(folded):
        if term.endswith("s") and not term.endswith("ss") and term[:-1] in folded:
            folded[term[:-1]] += folded.pop(term)
    return folded


def _fold(term: str, vocab: Counter) -> str:
    return (
        term[:-1] if term.endswith("s") and not term.endswith("ss") and term[:-1] in vocab else term
    )


def distinctive_terms(groups: dict[str, Counter], top: int = 8, min_count: int = 2) -> dict:
    """TF-IDF: terms frequent in one group but rare across the others."""
    n = len(groups)
    df = Counter(term for counts in groups.values() for term in counts)
    result = {}
    for name, counts in groups.items():
        total = sum(counts.values()) or 1
        scored = [
            (counts[t] / total * math.log((1 + n) / (1 + df[t])) + counts[t] / total * 0.01, t)
            for t in counts
            if counts[t] >= min_count
        ]
        result[name] = [t for _, t in sorted(scored, reverse=True)[:top]]
    return result


def compute(conn: sqlite3.Connection, ws: WorkingSet | None = None) -> dict:
    """Keywords, phrases and opening style over the working set's window (default: all-time)."""
    ws = ws or WorkingSet()
    with ws.lens(conn):
        result = _compute(conn, ws.tz)
    result["window"] = ws.window.label
    return result


def _compute(conn: sqlite3.Connection, tz: tzinfo | None = None) -> dict:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT p.uuid, p.session_id, p.ts, p.own_text, s.project_key, s.project_name "
        "FROM prompts p JOIN sessions s USING (session_id) "
        "WHERE p.kind = 'typed' ORDER BY p.ts"
    ).fetchall()

    term_counts: Counter = Counter()
    per_prompt_terms: list[set[str]] = []
    phrase_counts: Counter = Counter()
    by_project: dict[str, Counter] = defaultdict(Counter)
    project_meta: dict[str, dict] = {}
    by_month: dict[str, Counter] = defaultdict(Counter)
    opening_verbs: Counter = Counter()
    verbs_anywhere: Counter = Counter()
    styles: Counter = Counter()
    markers: Counter = Counter()

    for r in rows:
        text = r["own_text"]
        terms = content_terms(text)
        term_counts.update(terms)
        per_prompt_terms.append(set(terms))
        phrase_counts.update(set(bigrams(text)))  # once per prompt: one long prompt can't dominate
        key = r["project_key"] or "unknown"
        by_project[key].update(terms)
        meta = project_meta.setdefault(key, {"key": key, "name": r["project_name"], "prompts": 0})
        meta["prompts"] += 1
        if r["ts"]:
            by_month[parse_ts(r["ts"], tz).strftime("%Y-%m")].update(terms)
        verb = opening_verb(text)
        if verb:
            opening_verbs[verb] += 1
        verbs_anywhere.update(t for t in words(text) if t in ACTION_VERBS)
        styles[opening_style(text)] += 1
        lowered = clean_for_words(text)
        for name, pattern in STYLE_MARKERS.items():
            if pattern.search(lowered):
                markers[name] += 1

    folded = fold_plurals(term_counts)
    prompt_spread: Counter = Counter()
    session_spread: dict[str, set] = defaultdict(set)
    for r, terms in zip(rows, per_prompt_terms, strict=True):
        for t in {_fold(t, term_counts) for t in terms}:
            prompt_spread[t] += 1
            session_spread[t].add(r["session_id"])

    projects = {k: fold_plurals(c) for k, c in by_project.items()}
    project_terms = distinctive_terms(projects)
    months = {k: fold_plurals(c) for k, c in sorted(by_month.items())}
    month_terms = distinctive_terms(months, top=6)

    return {
        "typed_prompts": len(rows),
        "top_terms": [
            {
                "term": t,
                "count": n,
                "prompts": prompt_spread[t],
                "sessions": len(session_spread[t]),
            }
            # Rank by how many prompts mention a term, so one long prompt can't dominate.
            for t, n in sorted(folded.items(), key=lambda kv: (-prompt_spread[kv[0]], -kv[1]))[:30]
        ],
        "top_phrases": [
            {"phrase": p, "prompts": n} for p, n in phrase_counts.most_common(20) if n >= 2
        ],
        "action_verbs": {
            "opening": dict(opening_verbs.most_common(15)),
            "anywhere": dict(verbs_anywhere.most_common(15)),
        },
        "opening_style": dict(styles.most_common()),
        "style_markers": dict(markers.most_common()),
        "by_project": [
            {**project_meta[k], "terms": project_terms.get(k, [])}
            for k in sorted(project_meta, key=lambda k: -project_meta[k]["prompts"])
        ],
        "by_month": month_terms,
    }


def render(k: dict) -> str:
    out: list[str] = []
    add = out.append
    n = k["typed_prompts"] or 1
    add(f"POMPY · KEYWORDS & THEMES ({k['typed_prompts']} typed prompts, your own words only)")
    add("")
    add("Top terms (ranked by how many prompts mention them)")
    for row in k["top_terms"][:20]:
        add(
            f"  {row['term']:<18} {row['count']:>4} · {row['prompts']:>3} prompts · "
            f"{row['sessions']:>2} sessions"
        )
    if k["top_phrases"]:
        add("")
        add("Recurring phrases (in 2+ prompts)")
        add("  " + " · ".join(f"{p['phrase']} ({p['prompts']})" for p in k["top_phrases"][:12]))
    add("")
    add("How you open a prompt")
    add(
        "  " + " · ".join(f"{s} {c} ({round(100 * c / n)}%)" for s, c in k["opening_style"].items())
    )
    add(
        "  opening verbs: " + ", ".join(f"{v} {c}" for v, c in k["action_verbs"]["opening"].items())
    )
    add("")
    add("Style markers (share of prompts)")
    add("  " + " · ".join(f"{m} {round(100 * c / n)}%" for m, c in k["style_markers"].items()))
    add("")
    add("What each project is about (distinctive terms, TF-IDF)")
    for p in k["by_project"][:10]:
        add(f"  {(p['name'] or p['key'])[:24]:<24} {p['prompts']:>3}  {', '.join(p['terms'][:7])}")
    add("")
    add("Themes by month")
    for month, terms in k["by_month"].items():
        add(f"  {month}  {', '.join(terms)}")
    return "\n".join(out)
