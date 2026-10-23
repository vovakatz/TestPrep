"""SQLite connection handling and schema."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from flask import Flask, current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);

-- A Google or Microsoft account that signs in to a profile (users row).
CREATE TABLE IF NOT EXISTS identities (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider      TEXT NOT NULL,
    subject       TEXT NOT NULL,
    email         TEXT,
    created_at    TEXT NOT NULL,
    last_login_at TEXT NOT NULL,
    UNIQUE (provider, subject)
);

-- A certification exam with its own question bank, e.g. Datadog Fundamentals. One per content/<slug>/ folder.
CREATE TABLE IF NOT EXISTS certifications (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    vendor      TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    docs_url    TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS domains (
    id               INTEGER PRIMARY KEY,
    certification_id INTEGER NOT NULL REFERENCES certifications(id),
    name             TEXT NOT NULL,
    slug             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    sort_order       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (certification_id, name)
);

CREATE TABLE IF NOT EXISTS topics (
    id               INTEGER PRIMARY KEY,
    certification_id INTEGER NOT NULL REFERENCES certifications(id),
    domain_id        INTEGER NOT NULL REFERENCES domains(id),
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    sort_order       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (certification_id, name)
);

CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    topic_id    INTEGER NOT NULL REFERENCES topics(id),
    text        TEXT NOT NULL,
    explanation TEXT NOT NULL,
    reference   TEXT,
    difficulty  INTEGER NOT NULL DEFAULT 2,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS choices (
    id          INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    position    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    is_correct  INTEGER NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (question_id, position)
);

CREATE TABLE IF NOT EXISTS flashcards (
    id        INTEGER PRIMARY KEY,
    key       TEXT NOT NULL UNIQUE,
    topic_id  INTEGER NOT NULL REFERENCES topics(id),
    front     TEXT NOT NULL,
    back      TEXT NOT NULL,
    reference TEXT,
    active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS exams (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    certification_id INTEGER NOT NULL REFERENCES certifications(id),
    mode             TEXT NOT NULL CHECK (mode IN ('random', 'targeted')),
    started_at       TEXT NOT NULL,
    completed_at     TEXT,
    total            INTEGER NOT NULL,
    correct          INTEGER,
    score            REAL
);

-- One row per question in an exam; holds the user's recorded answer.
CREATE TABLE IF NOT EXISTS exam_questions (
    id                 INTEGER PRIMARY KEY,
    exam_id            INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    position           INTEGER NOT NULL,
    question_id        INTEGER NOT NULL REFERENCES questions(id),
    choice_order       TEXT NOT NULL,
    selected_choice_id INTEGER REFERENCES choices(id),
    is_correct         INTEGER,
    answered_at        TEXT,
    UNIQUE (exam_id, position),
    UNIQUE (exam_id, question_id)
);

CREATE TABLE IF NOT EXISTS flashcard_sessions (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    certification_id INTEGER NOT NULL REFERENCES certifications(id),
    mode             TEXT NOT NULL CHECK (mode IN ('random', 'targeted')),
    scope            TEXT NOT NULL DEFAULT '',
    card_ids         TEXT NOT NULL,
    started_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flashcard_reviews (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id   INTEGER REFERENCES flashcard_sessions(id) ON DELETE CASCADE,
    flashcard_id INTEGER NOT NULL REFERENCES flashcards(id),
    rating       TEXT NOT NULL CHECK (rating IN ('known', 'learning')),
    reviewed_at  TEXT NOT NULL,
    UNIQUE (session_id, flashcard_id)
);

CREATE INDEX IF NOT EXISTS idx_identities_user ON identities(user_id);
CREATE INDEX IF NOT EXISTS idx_exams_user ON exams(user_id, certification_id, started_at);
CREATE INDEX IF NOT EXISTS idx_flashcard_sessions_user ON flashcard_sessions(user_id, certification_id, started_at);
CREATE INDEX IF NOT EXISTS idx_exam_questions_question ON exam_questions(question_id);
CREATE INDEX IF NOT EXISTS idx_flashcard_reviews_user ON flashcard_reviews(user_id, flashcard_id, reviewed_at);
"""

# Slug and name given to existing content and history when an older database is migrated.
LEGACY_CERTIFICATION = ("datadog-fundamentals", "Datadog Fundamentals")
CERTIFICATION_TABLES = ("domains", "topics", "exams", "flashcard_sessions")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_schema(conn: sqlite3.Connection) -> None:
    if _columns(conn, "domains") and "certification_id" not in _columns(conn, "domains"):
        _migrate_to_certifications(conn)
    conn.executescript(SCHEMA)
    conn.commit()


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def _create_sql(table: str, as_name: str) -> str:
    """SCHEMA's CREATE TABLE statement for `table`, creating `as_name` instead."""
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {table} (\(.*?\n\));", SCHEMA, re.S)
    return f"CREATE TABLE {as_name} {match.group(1)}"


def _migrate_to_certifications(conn: sqlite3.Connection) -> None:
    """Give every domain, topic, exam and flash card deck a certification_id, keeping all ids.

    Databases from before certifications existed hold only Datadog Fundamentals content and history.
    SQLite can't add a NOT NULL foreign key column or change UNIQUE constraints in place, so each table
    is rebuilt from its SCHEMA definition (https://www.sqlite.org/lang_altertable.html#otheralter).
    """
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")  # no effect inside a transaction
    try:
        conn.execute("BEGIN")
        conn.execute(_create_sql("certifications", "certifications"))
        cert_id = conn.execute(
            "INSERT INTO certifications (slug, name) VALUES (?, ?)", LEGACY_CERTIFICATION
        ).lastrowid
        for table in CERTIFICATION_TABLES:
            columns = ", ".join(_columns(conn, table))
            if not columns:
                continue  # older than this table; SCHEMA creates it
            conn.execute(_create_sql(table, f"{table}_new"))
            conn.execute(
                f"INSERT INTO {table}_new ({columns}, certification_id) SELECT {columns}, ? FROM {table}", (cert_id,)
            )
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)
