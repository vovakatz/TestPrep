-- testprep/db.py SCHEMA before certifications existed (commit b097769). Used to test the migration.
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

CREATE TABLE IF NOT EXISTS domains (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY,
    domain_id   INTEGER NOT NULL REFERENCES domains(id),
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
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
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode         TEXT NOT NULL CHECK (mode IN ('random', 'targeted')),
    started_at   TEXT NOT NULL,
    completed_at TEXT,
    total        INTEGER NOT NULL,
    correct      INTEGER,
    score        REAL
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
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode       TEXT NOT NULL CHECK (mode IN ('random', 'targeted')),
    scope      TEXT NOT NULL DEFAULT '',
    card_ids   TEXT NOT NULL,
    started_at TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_exams_user ON exams(user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_exam_questions_question ON exam_questions(question_id);
CREATE INDEX IF NOT EXISTS idx_flashcard_reviews_user ON flashcard_reviews(user_id, flashcard_id, reviewed_at);
