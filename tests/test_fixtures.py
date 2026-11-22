"""Step 2: sanity checks on the synthetic fixtures themselves."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())


def _all_records():
    for path in (FIXTURES / "projects").rglob("*.jsonl"):
        for line in path.read_text().splitlines():
            if line.strip():
                yield json.loads(line)  # every line must be valid JSON


def test_every_line_is_valid_json():
    assert sum(1 for _ in _all_records()) > 30


def test_answer_key_points_at_real_records():
    uuids = {r.get("uuid") for r in _all_records()}
    for uuid in list(EXPECTED["prompts"]) + list(EXPECTED["skipped_because"]):
        assert uuid in uuids, f"{uuid} is in expected.json but not in any fixture"


def test_every_user_record_is_classified():
    """Each user record must be listed as either a prompt or a skip, so nothing is untested."""
    classified = set(EXPECTED["prompts"]) | set(EXPECTED["skipped_because"])
    user_uuids = {r["uuid"] for r in _all_records() if r.get("type") == "user"}
    assert user_uuids == classified
