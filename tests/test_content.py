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
BANKS = sorted(p for p in CONTENT.glob("*/*.json") if p.name != "taxonomy.json")

pytestmark = pytest.mark.skipif(not BANKS, reason="no content files yet")


def test_content_passes_validator():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_content.py")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout[-3000:]


def test_real_content_seeds_and_covers_every_topic(tmp_path):
    """Certifications without questions are "coming soon"; any that has questions needs a full exam's worth."""
    conn = connect(str(tmp_path / "content.sqlite"))
    init_schema(conn)
    seed(conn, CONTENT)
    certs = conn.execute(
        "SELECT c.slug, COUNT(q.id) AS questions FROM certifications c "
        "LEFT JOIN topics t ON t.certification_id = c.id LEFT JOIN questions q ON q.topic_id = t.id "
        "GROUP BY c.id"
    ).fetchall()
    assert {r["slug"] for r in certs} == {p.parent.name for p in CONTENT.glob("*/taxonomy.json")}
    for cert in certs:
        if not cert["questions"]:
            continue
        assert cert["questions"] >= EXAM_SIZE, cert["slug"]
        empty = conn.execute(
            "SELECT t.name FROM topics t JOIN certifications c ON c.id = t.certification_id "
            "LEFT JOIN questions q ON q.topic_id = t.id WHERE c.slug = ? GROUP BY t.id HAVING COUNT(q.id) = 0",
            (cert["slug"],),
        ).fetchall()
        assert not empty, f"{cert['slug']} topics without questions: {[r[0] for r in empty]}"
    conn.close()


def test_every_question_has_exactly_one_correct_choice():
    for path in BANKS:
        for q in json.loads(path.read_text(encoding="utf-8")).get("questions", []):
            assert 0 <= q["answer"] < len(q["choices"]), q["id"]
