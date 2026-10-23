"""Several certifications side by side: the picker, per-certification pages and history, and the migration."""

import json
import shutil
from pathlib import Path

from conftest import CERT, EMPTY, OTHER, OTHER_TOPICS
from testprep import create_app
from testprep.db import connect
from testprep.seed import seed


def cert_id(conn, slug: str) -> int:
    return conn.execute("SELECT id FROM certifications WHERE slug = ?", (slug,)).fetchone()[0]


def test_home_lists_every_certification(client):
    page = client.get("/").get_data(as_text=True)
    for name, slug in [("Datadog Fundamentals", CERT), ("Other Cert", OTHER), ("Empty Cert", EMPTY)]:
        assert f"{name} Exam" in page and f'href="/{slug}/"' in page, slug
    assert page.count("Coming soon") == 1
    assert 'class="nav"' not in page  # no exam navigation until one is chosen


def test_every_certification_has_its_own_pages(client):
    for slug in (CERT, OTHER, EMPTY):
        for path in ["/", "/exams", "/flashcards", "/progress"]:
            res = client.get(f"/{slug}{path}")
            assert res.status_code == 200, (slug, path)
            assert f'href="/{slug}/exams"' in res.get_data(as_text=True)  # navigation stays in this certification
    assert client.get("/nope/").status_code == 404
    assert client.get("/nope/exams").status_code == 404


def test_overlapping_topic_names_stay_separate(app):
    conn = connect(app.config["DATABASE"])
    rows = conn.execute(
        "SELECT c.slug FROM topics t JOIN certifications c ON c.id = t.certification_id "
        "WHERE t.name = 'Dashboards' ORDER BY c.slug"
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == [CERT, OTHER]


def test_exams_and_decks_stay_in_their_certification(client, app):
    res = client.post(f"/{OTHER}/exams", data={"mode": "random"})
    exam_id = int(res.headers["Location"].rsplit("/", 1)[1])
    assert res.headers["Location"] == f"/{OTHER}/exams/{exam_id}"
    conn = connect(app.config["DATABASE"])
    other = cert_id(conn, OTHER)
    exam_certs = conn.execute(
        "SELECT DISTINCT t.certification_id FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
        "JOIN topics t ON t.id = q.topic_id WHERE eq.exam_id = ?",
        (exam_id,),
    ).fetchall()
    assert [r[0] for r in exam_certs] == [other]
    assert conn.execute("SELECT total FROM exams WHERE id = ?", (exam_id,)).fetchone()[0] == len(OTHER_TOPICS) * 5

    assert client.get(f"/{OTHER}/exams/{exam_id}").status_code == 200
    assert client.get(f"/{CERT}/exams/{exam_id}").status_code == 404
    assert client.post(f"/{CERT}/exams/{exam_id}/delete").status_code == 404
    assert b"taken an exam yet" in client.get(f"/{CERT}/exams").data
    assert b"taken an exam yet" not in client.get(f"/{OTHER}/exams").data

    # A Datadog topic matches no cards in the other certification.
    datadog_topic = conn.execute(
        "SELECT id FROM topics WHERE certification_id = ? LIMIT 1", (cert_id(conn, CERT),)
    ).fetchone()[0]
    res = client.post(f"/{OTHER}/flashcards", data={"mode": "random", "scope": f"topic:{datadog_topic}"})
    assert res.headers["Location"] == f"/{OTHER}/flashcards"
    res = client.post(f"/{OTHER}/flashcards", data={"mode": "random", "size": "50"})
    deck_id = int(res.headers["Location"].rsplit("/", 1)[1])
    card_ids = json.loads(conn.execute("SELECT card_ids FROM flashcard_sessions WHERE id = ?", (deck_id,)).fetchone()[0])
    deck_certs = conn.execute(
        f"SELECT DISTINCT t.certification_id FROM flashcards f JOIN topics t ON t.id = f.topic_id "
        f"WHERE f.id IN ({','.join('?' * len(card_ids))})",
        card_ids,
    ).fetchall()
    conn.close()
    assert len(card_ids) == len(OTHER_TOPICS) * 3 and [r[0] for r in deck_certs] == [other]
    assert client.get(f"/{OTHER}/flashcards/{deck_id}").status_code == 200
    assert client.get(f"/{CERT}/flashcards/{deck_id}").status_code == 404


def test_empty_certification_is_coming_soon(client):
    assert "Coming soon" in client.get(f"/{EMPTY}/").get_data(as_text=True)
    res = client.post(f"/{EMPTY}/exams", data={"mode": "random"})
    assert res.headers["Location"] == f"/{EMPTY}/exams"
    assert "no questions for this exam yet" in client.get(f"/{EMPTY}/exams").get_data(as_text=True)


def test_removed_certification_disappears(client, app, content_dir):
    shutil.rmtree(content_dir / EMPTY)
    conn = connect(app.config["DATABASE"])
    seed(conn, content_dir)
    conn.close()
    assert client.get(f"/{EMPTY}/").status_code == 404
    assert b"Empty Cert" not in client.get("/").data


def test_migrates_a_database_from_before_certifications(tmp_path, content_dir):
    path = str(tmp_path / "legacy.sqlite")
    conn = connect(path)
    conn.executescript((Path(__file__).parent / "legacy_schema.sql").read_text(encoding="utf-8"))
    user = conn.execute("INSERT INTO users (name, created_at) VALUES ('Vova', '2026-01-01')").lastrowid
    conn.execute("INSERT INTO domains (id, name, slug) VALUES (7, 'Essential Concepts', 'essential')")
    conn.execute("INSERT INTO topics (id, domain_id, name) VALUES (9, 7, 'Linux Basics')")
    question = conn.execute(
        "INSERT INTO questions (key, topic_id, text, explanation) VALUES ('t1-q-0', 9, 'Old?', 'Old.')"
    ).lastrowid
    right = conn.execute(
        "INSERT INTO choices (question_id, position, text, is_correct) VALUES (?, 0, 'right answer', 1)", (question,)
    ).lastrowid
    exam = conn.execute(
        "INSERT INTO exams (user_id, mode, started_at, completed_at, total, correct, score) "
        "VALUES (?, 'random', '2026-01-01T10:00:00+00:00', '2026-01-01T10:30:00+00:00', 1, 1, 100.0)",
        (user,),
    ).lastrowid
    conn.execute(
        "INSERT INTO exam_questions (exam_id, position, question_id, choice_order, selected_choice_id, is_correct, "
        "answered_at) VALUES (?, 1, ?, ?, ?, 1, '2026-01-01T10:05:00+00:00')",
        (exam, question, json.dumps([right]), right),
    )
    card = conn.execute(
        "INSERT INTO flashcards (key, topic_id, front, back) VALUES ('t1-fc-0', 9, 'Old front', 'Old back')"
    ).lastrowid
    deck = conn.execute(
        "INSERT INTO flashcard_sessions (user_id, mode, card_ids, started_at) VALUES (?, 'random', ?, '2026-01-02')",
        (user, json.dumps([card])),
    ).lastrowid
    conn.execute(
        "INSERT INTO flashcard_reviews (user_id, session_id, flashcard_id, rating, reviewed_at) "
        "VALUES (?, ?, ?, 'known', '2026-01-02')",
        (user, deck, card),
    )
    conn.commit()
    conn.close()

    config = {"TESTING": True, "DATABASE": path, "CONTENT_DIR": str(content_dir), "SECRET_KEY": "t"}
    app = create_app(config)
    create_app(config)  # starting again doesn't migrate twice

    conn = connect(path)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    datadog = cert_id(conn, CERT)
    # Existing ids now belong to Datadog Fundamentals, and the content sync reused them rather than adding copies.
    assert tuple(conn.execute("SELECT certification_id, domain_id FROM topics WHERE id = 9").fetchone()) == (datadog, 7)
    assert conn.execute(
        "SELECT COUNT(*) FROM topics WHERE name = 'Linux Basics' AND certification_id = ?", (datadog,)
    ).fetchone()[0] == 1
    assert conn.execute("SELECT text FROM questions WHERE id = ?", (question,)).fetchone()[0].startswith("Synthetic")
    assert conn.execute("SELECT certification_id FROM exams WHERE id = ?", (exam,)).fetchone()[0] == datadog
    assert conn.execute("SELECT certification_id FROM flashcard_sessions WHERE id = ?", (deck,)).fetchone()[0] == datadog
    assert conn.execute("SELECT COUNT(*) FROM certifications").fetchone()[0] == 3
    conn.close()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user
    assert b"1 of 1 correct" in client.get(f"/{CERT}/exams/{exam}/results").data
    assert client.get(f"/{CERT}/flashcards/{deck}").status_code == 200
    assert b"Cards reviewed</span><span class=\"stat-value\">1<" in client.get(f"/{CERT}/").data
