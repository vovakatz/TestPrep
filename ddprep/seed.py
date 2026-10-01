"""Load the question bank and flash cards from content/*.json into SQLite.

Content is upserted by its stable string id, so editing the JSON files never
breaks recorded exam history. Items removed from the files are deactivated
rather than deleted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

TAXONOMY_FILE = "taxonomy.json"

log = logging.getLogger(__name__)


def _content_files(content_dir: Path) -> list[Path]:
    return sorted(p for p in content_dir.glob("*.json") if p.name != TAXONOMY_FILE)


def content_hash(content_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in [content_dir / TAXONOMY_FILE, *_content_files(content_dir)]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def seed(conn: sqlite3.Connection, content_dir: Path, force: bool = False) -> dict[str, int] | None:
    """Sync content into the database. Returns counts, or None when already up to date."""
    current = content_hash(content_dir)
    row = conn.execute("SELECT value FROM meta WHERE key = 'content_hash'").fetchone()
    if row and row["value"] == current and not force:
        return None

    taxonomy = json.loads((content_dir / TAXONOMY_FILE).read_text(encoding="utf-8"))
    topic_ids = _sync_taxonomy(conn, taxonomy)

    questions: list[dict] = []
    flashcards: list[dict] = []
    for path in _content_files(content_dir):
        try:
            bank = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("Skipping %s: invalid JSON (%s). Run scripts/validate_content.py.", path.name, exc)
            continue
        for kind, target in (("questions", questions), ("flashcards", flashcards)):
            for item in bank.get(kind, []):
                if item.get("topic") in topic_ids:
                    target.append(item)
                else:
                    log.warning("Skipping %s '%s': unknown topic %r", kind, item.get("id"), item.get("topic"))

    q_count = _sync_questions(conn, questions, topic_ids)
    f_count = _sync_flashcards(conn, flashcards, topic_ids)

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('content_hash', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (current,),
    )
    conn.commit()
    return {"questions": q_count, "flashcards": f_count}


def _sync_taxonomy(conn: sqlite3.Connection, taxonomy: dict) -> dict[str, int]:
    topic_ids: dict[str, int] = {}
    for d_order, domain in enumerate(taxonomy["domains"]):
        conn.execute(
            "INSERT INTO domains (name, slug, description, sort_order) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET slug = excluded.slug, "
            "description = excluded.description, sort_order = excluded.sort_order",
            (domain["name"], domain["slug"], domain.get("description", ""), d_order),
        )
        domain_id = conn.execute("SELECT id FROM domains WHERE name = ?", (domain["name"],)).fetchone()["id"]
        for t_order, topic in enumerate(domain["topics"]):
            conn.execute(
                "INSERT INTO topics (domain_id, name, description, sort_order) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET domain_id = excluded.domain_id, "
                "description = excluded.description, sort_order = excluded.sort_order",
                (domain_id, topic["name"], topic.get("description", ""), t_order),
            )
            topic_ids[topic["name"]] = conn.execute(
                "SELECT id FROM topics WHERE name = ?", (topic["name"],)
            ).fetchone()["id"]
    return topic_ids


def _sync_questions(conn: sqlite3.Connection, questions: list[dict], topic_ids: dict[str, int]) -> int:
    conn.execute("UPDATE questions SET active = 0")
    for q in questions:
        topic_id = topic_ids[q["topic"]]
        conn.execute(
            "INSERT INTO questions (key, topic_id, text, explanation, reference, difficulty, active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(key) DO UPDATE SET topic_id = excluded.topic_id, text = excluded.text, "
            "explanation = excluded.explanation, reference = excluded.reference, "
            "difficulty = excluded.difficulty, active = 1",
            (q["id"], topic_id, q["question"], q["explanation"], q.get("reference"), q.get("difficulty", 2)),
        )
        question_id = conn.execute("SELECT id FROM questions WHERE key = ?", (q["id"],)).fetchone()["id"]
        conn.execute("UPDATE choices SET active = 0 WHERE question_id = ?", (question_id,))
        for position, text in enumerate(q["choices"]):
            conn.execute(
                "INSERT INTO choices (question_id, position, text, is_correct, active) VALUES (?, ?, ?, ?, 1) "
                "ON CONFLICT(question_id, position) DO UPDATE SET text = excluded.text, "
                "is_correct = excluded.is_correct, active = 1",
                (question_id, position, text, int(position == q["answer"])),
            )
    return len(questions)


def _sync_flashcards(conn: sqlite3.Connection, flashcards: list[dict], topic_ids: dict[str, int]) -> int:
    conn.execute("UPDATE flashcards SET active = 0")
    for card in flashcards:
        conn.execute(
            "INSERT INTO flashcards (key, topic_id, front, back, reference, active) VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(key) DO UPDATE SET topic_id = excluded.topic_id, front = excluded.front, "
            "back = excluded.back, reference = excluded.reference, active = 1",
            (card["id"], topic_ids[card["topic"]], card["front"], card["back"], card.get("reference")),
        )
    return len(flashcards)
