"""Load each certification's question bank and flash cards from content/<slug>/*.json into SQLite.

Every folder with a taxonomy.json is one certification; the folder name is its slug. Content is upserted
by its stable string id, so editing the JSON files never breaks recorded exam history. Items removed from
the files are deactivated rather than deleted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

TAXONOMY_FILE = "taxonomy.json"

log = logging.getLogger(__name__)


def certification_dirs(content_dir: Path) -> list[Path]:
    return sorted(p.parent for p in content_dir.glob(f"*/{TAXONOMY_FILE}"))


def bank_files(cert_dir: Path) -> list[Path]:
    return sorted(p for p in cert_dir.glob("*.json") if p.name != TAXONOMY_FILE)


def content_hash(content_dir: Path) -> str:
    digest = hashlib.sha256()
    for cert_dir in certification_dirs(content_dir):
        for path in [cert_dir / TAXONOMY_FILE, *bank_files(cert_dir)]:
            digest.update(path.relative_to(content_dir).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def seed(conn: sqlite3.Connection, content_dir: Path, force: bool = False) -> dict[str, int] | None:
    """Sync content into the database. Returns counts, or None when already up to date."""
    current = content_hash(content_dir)
    row = conn.execute("SELECT value FROM meta WHERE key = 'content_hash'").fetchone()
    if row and row["value"] == current and not force:
        return None

    conn.execute("UPDATE certifications SET active = 0")
    # (topic id, item) pairs across every certification.
    questions: list[tuple[int, dict]] = []
    flashcards: list[tuple[int, dict]] = []
    cert_dirs = certification_dirs(content_dir)
    for cert_dir in cert_dirs:
        taxonomy = json.loads((cert_dir / TAXONOMY_FILE).read_text(encoding="utf-8"))
        cert_id = _sync_certification(conn, cert_dir.name, taxonomy)
        topic_ids = _sync_taxonomy(conn, cert_id, taxonomy)
        for path in bank_files(cert_dir):
            try:
                bank = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                log.warning("Skipping %s/%s: invalid JSON (%s). Run scripts/validate_content.py.",
                            cert_dir.name, path.name, exc)
                continue
            for kind, target in (("questions", questions), ("flashcards", flashcards)):
                for item in bank.get(kind, []):
                    if item.get("topic") in topic_ids:
                        target.append((topic_ids[item["topic"]], item))
                    else:
                        log.warning("Skipping %s '%s': unknown %s topic %r",
                                    kind, item.get("id"), cert_dir.name, item.get("topic"))

    q_count = _sync_questions(conn, questions)
    f_count = _sync_flashcards(conn, flashcards)

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('content_hash', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (current,),
    )
    conn.commit()
    return {"certifications": len(cert_dirs), "questions": q_count, "flashcards": f_count}


def _sync_certification(conn: sqlite3.Connection, slug: str, taxonomy: dict) -> int:
    conn.execute(
        "INSERT INTO certifications (slug, name, vendor, description, docs_url, active) VALUES (?, ?, ?, ?, ?, 1) "
        "ON CONFLICT(slug) DO UPDATE SET name = excluded.name, vendor = excluded.vendor, "
        "description = excluded.description, docs_url = excluded.docs_url, active = 1",
        (slug, taxonomy["name"], taxonomy.get("vendor", ""), taxonomy.get("description", ""),
         taxonomy.get("docs_url")),
    )
    return conn.execute("SELECT id FROM certifications WHERE slug = ?", (slug,)).fetchone()["id"]


def _sync_taxonomy(conn: sqlite3.Connection, cert_id: int, taxonomy: dict) -> dict[str, int]:
    topic_ids: dict[str, int] = {}
    for d_order, domain in enumerate(taxonomy["domains"]):
        conn.execute(
            "INSERT INTO domains (certification_id, name, slug, description, sort_order) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(certification_id, name) DO UPDATE SET slug = excluded.slug, "
            "description = excluded.description, sort_order = excluded.sort_order",
            (cert_id, domain["name"], domain["slug"], domain.get("description", ""), d_order),
        )
        domain_id = conn.execute(
            "SELECT id FROM domains WHERE certification_id = ? AND name = ?", (cert_id, domain["name"])
        ).fetchone()["id"]
        for t_order, topic in enumerate(domain["topics"]):
            conn.execute(
                "INSERT INTO topics (certification_id, domain_id, name, description, sort_order) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(certification_id, name) DO UPDATE SET domain_id = excluded.domain_id, "
                "description = excluded.description, sort_order = excluded.sort_order",
                (cert_id, domain_id, topic["name"], topic.get("description", ""), t_order),
            )
            topic_ids[topic["name"]] = conn.execute(
                "SELECT id FROM topics WHERE certification_id = ? AND name = ?", (cert_id, topic["name"])
            ).fetchone()["id"]
    return topic_ids


def _sync_questions(conn: sqlite3.Connection, questions: list[tuple[int, dict]]) -> int:
    conn.execute("UPDATE questions SET active = 0")
    for topic_id, q in questions:
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


def _sync_flashcards(conn: sqlite3.Connection, flashcards: list[tuple[int, dict]]) -> int:
    conn.execute("UPDATE flashcards SET active = 0")
    for topic_id, card in flashcards:
        conn.execute(
            "INSERT INTO flashcards (key, topic_id, front, back, reference, active) VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(key) DO UPDATE SET topic_id = excluded.topic_id, front = excluded.front, "
            "back = excluded.back, reference = excluded.reference, active = 1",
            (card["id"], topic_id, card["front"], card["back"], card.get("reference")),
        )
    return len(flashcards)
