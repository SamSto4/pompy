"""Pompy Pre-flight: review a prompt the moment you send it, in every Claude Code session.

Runs as a Claude Code `UserPromptSubmit` hook (`pompy preflight`): Claude Code hands it the
prompt as JSON on stdin before Claude sees it. A vague task prompt (3/7 or less) is held with
a short review; everything else goes straight through.

Hot-path rules (CLAUDE.md): under 100 ms, no network, no model, and it fails open. Any error,
a missing profile, an unreadable input: the prompt goes through untouched.

How memory shows up here:
  - long-term memory, compiled: `out/preflight.json` holds your weakest habits, written ahead
    of time by `pompy publish`, so the hook reads one small file instead of the database;
  - working memory: this one prompt, scored and compared with those habits;
  - episodic trace: each verdict is appended to `data/preflight.jsonl` (score, parts, held or
    not; never the prompt text), so a later step can measure whether the advice helps.

Escapes: send the same text again within 10 minutes and it goes through; start a prompt with
`!!` to skip the review; `pompy preflight --off` turns it off everywhere.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

HOLD_AT_OR_BELOW = 3
SKIP_PREFIX = "!!"
RESEND_WINDOW_S = 10 * 60
PROFILE_NAME = "preflight.json"


# ── The profile: long-term memory compiled for the hot path ──────────────────


def build_profile(score_result: dict) -> dict:
    """From `pompy score`'s all-time result: the rubric parts, weakest habit first."""
    from pompy import score

    share = {name: f["present_pct"] for name, f in score_result["features"].items()}
    names = [f.name for f in score.FEATURES]
    order = sorted(names, key=lambda n: (n != "goal", share.get(n, 0.0)))
    return {
        "rubric_version": score.RUBRIC_VERSION,
        "order": order,
        "share": share,
        "parts": {
            f.name: {"label": f.label.replace("-", " "), "tip": f.tip} for f in score.FEATURES
        },
    }


def write_profile(profile: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / PROFILE_NAME
    path.write_text(json.dumps(profile, indent=1))
    return path


def _default_profile() -> dict:
    from pompy import score

    names = [f.name for f in score.FEATURES]
    return {
        "order": names,
        "share": {},
        "parts": {
            f.name: {"label": f.label.replace("-", " "), "tip": f.tip} for f in score.FEATURES
        },
    }


# ── The review: working memory at the point of action ────────────────────────


def review(prompt: str, profile: dict | None) -> dict:
    """Score one prompt. Returns {role, style, score, missing (weakest first), hold, reason}.

    Questions ("why was it blocked", "how do I…") are never held: they ask for an answer, so
    a done-definition or output format rarely applies. Requests phrased as questions ("can you
    fix…?") open as requests, not questions, and are still reviewed.
    """
    from pompy import keywords, score

    profile = profile or _default_profile()
    text = prompt.strip()
    r = score.score_text(text)
    missing = [n for n in profile["order"] if n in set(r["missing"])]
    style = keywords.opening_style(text)
    hold = (
        r["role"] == "task"
        and style != "question"
        and r["score"] is not None
        and r["score"] <= HOLD_AT_OR_BELOW
    )
    reason = ""
    if hold:
        parts = profile["parts"]
        lines = [f"Pompy held this prompt ({r['score']}/7). Before you send it:"]
        for n in missing[:2]:
            lines.append(f"  + Add {parts[n]['label'].lower()}: {parts[n]['tip']}")
        lines.append(
            "Edit and send, or send the same text again to send it as is "
            f"(start with {SKIP_PREFIX} to skip the review)."
        )
        reason = "\n".join(lines)
    return {
        "role": r["role"],
        "style": style,
        "score": r["score"],
        "missing": missing,
        "hold": hold,
        "reason": reason,
    }


# ── The hook: stdin JSON in, a decision out ──────────────────────────────────


def _digest(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:16]


def run_hook(stdin_text: str, data_dir: Path, out_dir: Path, now: float | None = None) -> str:
    """Return what the hook prints: '' to let the prompt through, or a block decision as JSON.

    Fails open: callers wrap this in a broad except and print nothing on any error.
    """
    now = time.time() if now is None else now
    event = json.loads(stdin_text)
    prompt = (event.get("prompt") or "").strip()
    if not prompt or prompt.startswith(("/", SKIP_PREFIX)) or (data_dir / "preflight_off").exists():
        return ""

    state_path = data_dir / "preflight_state.json"
    digest = _digest(prompt)
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("held") == digest and now - state.get("at", 0) < RESEND_WINDOW_S:
        _log(data_dir, event, now, None, resent=True)
        return ""  # sent again on purpose: let it through

    try:
        profile = json.loads((out_dir / PROFILE_NAME).read_text())
    except (OSError, ValueError):
        profile = None
    verdict = review(prompt, profile)
    _log(data_dir, event, now, verdict)
    if not verdict["hold"]:
        return ""
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"held": digest, "at": now}))
    return json.dumps({"decision": "block", "reason": verdict["reason"]})


def _log(data_dir: Path, event: dict, now: float, verdict: dict | None, resent: bool = False):
    """Episodic trace of the advice given. Scores and parts only: never the prompt text."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        row = {"at": round(now, 3), "session": event.get("session_id"), "resent": resent}
        if verdict:
            row.update(
                role=verdict["role"],
                style=verdict["style"],
                score=verdict["score"],
                missing=verdict["missing"],
                held=verdict["hold"],
            )
        with (data_dir / "preflight.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def main_hook() -> None:
    """Entry point for the hook: read stdin, print a decision or nothing, never fail."""
    try:
        from pompy import config

        out = run_hook(sys.stdin.read(), config.data_dir(), config.out_dir())
        if out:
            print(out)
    except Exception:  # noqa: BLE001 — fail open: a broken check must never block a prompt
        pass
