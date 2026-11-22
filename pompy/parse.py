"""Turn raw transcript records into things worth remembering.

This is Pompy's *encoding* step: deciding what counts as a memory. Most records in a
Claude Code transcript are not you: they're tool results, injected reminders, agent
messages and summaries. Only a small share are prompts you actually wrote.

Pure functions, no I/O, so every rule here is covered by tests/fixtures/expected.json.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Blocks Claude Code injects into "user" messages that you didn't write.
INJECTED_TAGS = (
    "system-reminder",
    "ide_opened_file",
    "ide_selection",
    "local-command-caveat",
    "local-command-stdout",
    "local-command-stderr",
    "bash-stdout",
    "bash-stderr",
    "task-notification",
    "wake",
    "relay",
    "create-pr-command",
    "project_claude_message",
    "browser_instruction",
)
_INJECTED_RE = re.compile(
    r"<(" + "|".join(INJECTED_TAGS) + r")\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_PASTE_RE = re.compile(r"</?pasted_content\b[^>]*>", re.IGNORECASE)
_PASTE_BLOCK_RE = re.compile(
    r"<pasted_content\b[^>]*>.*?</pasted_content\b[^>]*>", re.DOTALL | re.IGNORECASE
)
_IMAGE_MARKER_RE = re.compile(r"\[Image[: #][^\]]*\]")
_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_BASH_INPUT_RE = re.compile(r"<bash-input>(.*?)</bash-input>", re.DOTALL)

# Bracketed status lines Claude Code writes on your behalf.
_INTERRUPT_PREFIX = "[Request interrupted by user"
_SYSTEM_BRACKET_PREFIXES = ("[Usage limit", "[Earlier usage-limit")

_REJECTION_MARKERS = ("doesn't want to proceed", "tool use was rejected")

# Who produced a turn. Anything other than a human is skipped.
_NON_HUMAN_TURN_ORIGINS = {
    "peer",
    "task_notification",
    "system",
    "auto_continuation",
    "scheduled",
    "host_synthetic",
}


@dataclass
class Prompt:
    uuid: str
    session_id: str
    ts: str | None
    kind: str  # typed | command | bash | suggestion
    source: str | None  # Claude Code's promptSource: typed, sdk, queued, ...
    text: str  # everything you sent, pasted text included (for search)
    own_text: str = ""  # only the words you wrote yourself (for keyword and style analysis)
    has_paste: bool = False
    has_image: bool = False
    parent_uuid: str | None = None

    @property
    def char_len(self) -> int:
        return len(self.text)

    @property
    def word_len(self) -> int:
        """How much you wrote yourself (pasted text doesn't count)."""
        return len(self.own_text.split())


@dataclass
class Turn:
    """One assistant message. Claude Code may split one message across several records."""

    message_id: str
    session_id: str
    ts: str | None
    model: str | None
    effort: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    tool_uses: list[tuple[str, str]] = field(default_factory=list)  # (tool_use_id, name)
    record_uuid: str | None = None  # this record's uuid: one message can span several records
    text: str = ""  # what Claude wrote in this record (text blocks only, no thinking)


@dataclass
class Event:
    """A friction signal or session milestone, kept for pattern recognition later."""

    uuid: str
    session_id: str
    ts: str | None
    kind: str  # interrupt | tool_rejected | compaction


@dataclass
class SessionInfo:
    session_id: str
    ts: str | None = None
    cwd: str | None = None
    entrypoint: str | None = None
    version: str | None = None
    title: str | None = None


Item = Prompt | Turn | Event | SessionInfo


def parse_record(r: dict, fallback_session_id: str) -> list[Item]:
    """Classify one transcript record into zero or more memory items."""
    session_id = r.get("sessionId") or fallback_session_id
    items: list[Item] = []

    info = SessionInfo(
        session_id=session_id,
        ts=r.get("timestamp"),
        cwd=r.get("cwd"),
        entrypoint=r.get("entrypoint"),
        version=r.get("version"),
        title=r.get("aiTitle") if r.get("type") == "ai-title" else None,
    )
    if any((info.ts, info.cwd, info.entrypoint, info.version, info.title)):
        items.append(info)

    rtype = r.get("type")
    if rtype == "user":
        items.extend(_parse_user(r, session_id))
    elif rtype == "assistant":
        turn = _parse_assistant(r, session_id)
        if turn:
            items.append(turn)
    return items


def _parse_user(r: dict, session_id: str) -> list[Item]:
    uuid, ts = r.get("uuid", ""), r.get("timestamp")
    if r.get("isSidechain") or r.get("isMeta"):
        return []
    if r.get("isCompactSummary"):
        return [Event(uuid, session_id, ts, "compaction")]

    content = (r.get("message") or {}).get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]

    tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
    if tool_results:
        return [
            Event(uuid, session_id, ts, "tool_rejected")
            for b in tool_results
            if b.get("is_error") and _is_rejection(b.get("content"))
        ][:1]

    if r.get("promptSource") == "system":
        return []
    if (r.get("origin") or {}).get("kind", "human") != "human":
        return []
    if r.get("turnOrigin") in _NON_HUMAN_TURN_ORIGINS:
        return []
    if r.get("textNotTyped"):  # text the app inserted (wake notices, relays, PR commands)
        return []

    has_image = any(isinstance(b, dict) and b.get("type") == "image" for b in blocks)
    raw = "\n".join(
        b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    ).strip()

    if raw.startswith(_INTERRUPT_PREFIX):
        return [Event(uuid, session_id, ts, "interrupt")]
    if raw.startswith(_SYSTEM_BRACKET_PREFIXES):
        return []

    kind, text, own_text, has_paste, marker_image = _clean_text(raw)
    if not text:
        return []
    if kind == "typed" and r.get("promptSource") == "suggestion_accepted":
        kind = "suggestion"

    return [
        Prompt(
            uuid=uuid,
            session_id=session_id,
            ts=ts,
            kind=kind,
            source=r.get("promptSource"),
            text=text,
            own_text=own_text,
            has_paste=has_paste,
            has_image=has_image or marker_image,
            parent_uuid=r.get("parentUuid"),
        )
    ]


def _clean_text(raw: str) -> tuple[str, str, str, bool, bool]:
    """Return (kind, text, own_text, has_paste, has_image_marker)."""
    name = _COMMAND_NAME_RE.search(raw)
    if name:
        args = _COMMAND_ARGS_RE.search(raw)
        arg_text = args.group(1).strip() if args else ""
        command = f"{name.group(1).strip()} {arg_text}".strip()
        return "command", command, command, False, False

    bash = _BASH_INPUT_RE.search(raw)
    if bash:
        return "bash", bash.group(1).strip(), bash.group(1).strip(), False, False

    text = _INJECTED_RE.sub("", raw)
    has_image = bool(_IMAGE_MARKER_RE.search(text))
    text = _IMAGE_MARKER_RE.sub("", text)
    has_paste = bool(_PASTE_RE.search(text))
    own_text = _PASTE_BLOCK_RE.sub(" ", text)
    text = _PASTE_RE.sub("", text)
    return "typed", text.strip(), " ".join(own_text.split()), has_paste, has_image


def _is_rejection(content) -> bool:
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return isinstance(content, str) and any(m in content for m in _REJECTION_MARKERS)


def _parse_assistant(r: dict, session_id: str) -> Turn | None:
    if r.get("isSidechain"):
        return None
    msg = r.get("message") or {}
    model = msg.get("model")
    if not msg.get("id") or model == "<synthetic>":
        return None
    usage = msg.get("usage") or {}
    blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
    return Turn(
        message_id=msg["id"],
        session_id=session_id,
        ts=r.get("timestamp"),
        model=model,
        effort=r.get("perTurnEffort") or r.get("effort"),
        input_tokens=usage.get("input_tokens") or 0,
        output_tokens=usage.get("output_tokens") or 0,
        cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
        cache_creation_tokens=usage.get("cache_creation_input_tokens") or 0,
        tool_uses=[
            (b.get("id") or f"{msg['id']}:{i}", b.get("name", "?"))
            for i, b in enumerate(blocks)
            if b.get("type") == "tool_use"
        ],
        record_uuid=r.get("uuid"),
        text="\n\n".join(
            b["text"].strip()
            for b in blocks
            if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip()
        ),
    )
