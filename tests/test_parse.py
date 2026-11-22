"""Unit tests for single-record rules found in real data after the fixtures were written."""

from pompy.parse import Prompt, parse_record


def _user(content, **extra):
    return {
        "type": "user",
        "uuid": "u1",
        "sessionId": "s1",
        "message": {"content": content},
        **extra,
    }


def test_browser_extension_instructions_are_stripped():
    record = _user(
        "<browser_instruction># Browser automation rules</browser_instruction>\nbook a table"
    )
    [prompt] = [i for i in parse_record(record, "s1") if isinstance(i, Prompt)]
    assert prompt.text == "book a table"


def test_instruction_only_message_is_not_a_prompt():
    record = _user("<browser_instruction># Browser automation rules</browser_instruction>")
    assert not [i for i in parse_record(record, "s1") if isinstance(i, Prompt)]


def test_pasted_markup_is_kept():
    record = _user("why does this fail? <key>CFBundleName</key>", promptSource="typed")
    [prompt] = [i for i in parse_record(record, "s1") if isinstance(i, Prompt)]
    assert "<key>CFBundleName</key>" in prompt.text
