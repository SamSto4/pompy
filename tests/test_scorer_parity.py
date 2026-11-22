"""Step 8: the dashboard's in-browser scorer must agree with Python's `score_text` exactly.

The browser copy (pompy/dashboard/scorer.js) gets its regexes and word lists from Python
(`publish.export_rules`); this test runs it in Node on many prompts and compares every field.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pompy import publish, score

NODE = shutil.which("node")
RUNNER = Path(__file__).parent / "js" / "score_cases.js"

CASES = [
    # roles
    "yes",
    "ok go ahead",
    "no, use the other button",
    "try again",
    "commit and push",
    "continue",
    "npm run dev",
    "git status",
    "https://example.com/docs",
    "",
    "   ",
    # rubric features, one at a time and together
    "fix the nav bar",
    "fix the bug",
    "add a reset link to the login form",
    "it's for the checkout page because users drop off",
    "don't change the colors and keep the layout",
    "done when the tests pass",
    "summarize it as a bullet list",
    "make it look like the pricing page",
    "fix the crash in src/app/page.tsx",
    "why am I getting a 404 on /api/notes?",
    "TypeError: cannot read properties of undefined at line 42",
    "rename getUserName to fetch_user_name everywhere",
    "update `config.yaml` with the new port",
    'make the header say "Welcome back, friend" in bold',
    '@"src/app/page.tsx" redesign this hero section',
    "Can you help me plan the A.I. memory feature? We're building a coach app, it should be "
    "short, a table is fine, and don't touch the database. Done means the plan lists 3 steps.",
    "I want the dashboard to match our brand, e.g. the Stripe docs, so that it feels premium",
    "ok so now please refactor the auth flow without breaking the tests",
    "let's make a plan for step 8",
    "what is the point of the fake chat files? shouldnt we build the system first?",
    "hello there, quick question about the API pagination",
    "The analysis window should be more than 14 days and include all chats since inception.",
    "Restructure the website: 1) hero 2) pricing 3) contact form. Use ```html``` blocks only.",
    "it says there are conflicts that need to be resolved before push",
    "café résumé naïve: update the menu page",  # non-ASCII letters
    "fix it’s broken on mobile",  # curly apostrophe
]


def _python(text, paste=False, image=False):
    return score.score_text(text, paste, image)


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_browser_scorer_matches_python(tmp_path):
    cases = [{"text": t, "paste": False, "image": False} for t in CASES]
    cases += [
        {"text": "make the notes list look like this", "paste": False, "image": True},
        {"text": "what does this error mean", "paste": True, "image": False},
    ]
    (tmp_path / "rules.json").write_text(json.dumps(publish.export_rules()))
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    out = subprocess.run(
        [NODE, str(RUNNER), str(tmp_path / "rules.json"), str(tmp_path / "cases.json")],
        capture_output=True,
        text=True,
        check=True,
    )
    browser = json.loads(out.stdout)
    for case, js in zip(cases, browser, strict=True):
        assert js == _python(case["text"], case["paste"], case["image"]), case["text"]
