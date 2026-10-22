"""Checks against the real question bank in content/."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from testprep.db import connect, init_schema
from testprep.exams import EXAM_SIZE
from testprep.seed import seed

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
BANKS = sorted(p for p in CONTENT.glob("*.json") if p.name != "taxonomy.json")

pytestmark = pytest.mark.skipif(not BANKS, reason="no content files yet")


def test_content_passes_validator():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_content.py")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout[-3000:]


def test_real_content_seeds_and_covers_every_topic(tmp_path):
    conn = connect(str(tmp_path / "content.sqlite"))
    init_schema(conn)
    counts = seed(conn, CONTENT)
    assert counts["questions"] >= EXAM_SIZE
    empty = conn.execute(
        "SELECT t.name FROM topics t LEFT JOIN questions q ON q.topic_id = t.id GROUP BY t.id HAVING COUNT(q.id) = 0"
    ).fetchall()
    assert not empty, f"topics without questions: {[r[0] for r in empty]}"
    conn.close()


def test_every_question_has_exactly_one_correct_choice():
    for path in BANKS:
        for q in json.loads(path.read_text(encoding="utf-8")).get("questions", []):
            assert 0 <= q["answer"] < len(q["choices"]), q["id"]
