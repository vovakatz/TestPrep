"""Flash card decks and self-rated reviews."""

from __future__ import annotations

import json
import random
import sqlite3

from . import selection
from .db import now
from .stats import flashcard_history, topic_stats

DECK_SIZES = (10, 20, 30, 50)
DEFAULT_DECK_SIZE = 20


class FlashcardError(Exception):
    pass


def create_session(
    conn: sqlite3.Connection,
    user_id: int,
    cert_id: int,
    mode: str,
    size: int = DEFAULT_DECK_SIZE,
    domain_id: int | None = None,
    topic_id: int | None = None,
    rng: random.Random | None = None,
) -> int:
    if mode not in ("random", "targeted"):
        raise FlashcardError(f"Unknown deck mode: {mode}")
    rng = rng or random.Random()
    sql = (
        "SELECT f.id, f.topic_id FROM flashcards f JOIN topics t ON t.id = f.topic_id "
        "WHERE f.active = 1 AND t.certification_id = ?"
    )
    params: list[int] = [cert_id]
    scope = ""
    if topic_id:
        sql += " AND f.topic_id = ?"
        params.append(topic_id)
        scope = f"topic:{topic_id}"
    elif domain_id:
        sql += " AND t.domain_id = ?"
        params.append(domain_id)
        scope = f"domain:{domain_id}"
    pool = [selection.Item(r["id"], r["topic_id"]) for r in conn.execute(sql + " ORDER BY f.id", params)]
    if not pool:
        raise FlashcardError("No flash cards match that selection.")

    if mode == "random":
        picked = selection.pick_random(pool, size, rng)
    else:
        picked = selection.pick_targeted_flashcards(
            pool, size, topic_stats(conn, user_id, cert_id), flashcard_history(conn, user_id), rng
        )
        rng.shuffle(picked)

    cur = conn.execute(
        "INSERT INTO flashcard_sessions (user_id, certification_id, mode, scope, card_ids, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, cert_id, mode, scope, json.dumps([i.id for i in picked]), now()),
    )
    conn.commit()
    return cur.lastrowid


def get_session(conn: sqlite3.Connection, session_id: int, user_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM flashcard_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
    ).fetchone()
    if row is None:
        raise FlashcardError("Flash card session not found.")
    return row


def session_cards(conn: sqlite3.Connection, session: sqlite3.Row) -> list[dict]:
    ids = json.loads(session["card_ids"])
    if not ids:
        return []
    rows = {
        r["id"]: r
        for r in conn.execute(
            "SELECT f.id, f.front, f.back, f.reference, t.name AS topic, d.name AS domain, "
            "(SELECT rating FROM flashcard_reviews r WHERE r.session_id = ? AND r.flashcard_id = f.id) AS rating "
            "FROM flashcards f JOIN topics t ON t.id = f.topic_id JOIN domains d ON d.id = t.domain_id "
            f"WHERE f.id IN ({','.join('?' * len(ids))})",
            [session["id"], *ids],
        )
    }
    return [dict(rows[i]) for i in ids if i in rows]


def review(conn: sqlite3.Connection, session_id: int, user_id: int, card_id: int, rating: str) -> None:
    if rating not in ("known", "learning"):
        raise FlashcardError("Rating must be 'known' or 'learning'.")
    session = get_session(conn, session_id, user_id)
    if card_id not in json.loads(session["card_ids"]):
        raise FlashcardError("That card is not part of this session.")
    conn.execute(
        "INSERT INTO flashcard_reviews (user_id, session_id, flashcard_id, rating, reviewed_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id, flashcard_id) DO UPDATE SET "
        "rating = excluded.rating, reviewed_at = excluded.reviewed_at",
        (user_id, session_id, card_id, rating, now()),
    )
    conn.commit()


def retry_session(conn: sqlite3.Connection, session_id: int, user_id: int) -> int:
    """New session containing only the cards marked 'still learning' in the given session."""
    session = get_session(conn, session_id, user_id)
    learning = [c["id"] for c in session_cards(conn, session) if c["rating"] == "learning"]
    if not learning:
        raise FlashcardError("No cards were marked as still learning.")
    random.shuffle(learning)
    cur = conn.execute(
        "INSERT INTO flashcard_sessions (user_id, certification_id, mode, scope, card_ids, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, session["certification_id"], session["mode"], session["scope"], json.dumps(learning), now()),
    )
    conn.commit()
    return cur.lastrowid


def recent_sessions(conn: sqlite3.Connection, user_id: int, cert_id: int, limit: int = 8) -> list[dict]:
    rows = conn.execute(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM flashcard_reviews r WHERE r.session_id = s.id) AS reviewed, "
        "(SELECT COUNT(*) FROM flashcard_reviews r WHERE r.session_id = s.id AND r.rating = 'known') AS known "
        "FROM flashcard_sessions s WHERE s.user_id = ? AND s.certification_id = ? "
        "ORDER BY s.started_at DESC, s.id DESC LIMIT ?",
        (user_id, cert_id, limit),
    ).fetchall()
    return [dict(r, size=len(json.loads(r["card_ids"]))) for r in rows]
