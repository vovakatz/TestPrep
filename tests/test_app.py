import json
import re
import sqlite3
from collections import Counter

from ddprep import exams
from ddprep.db import connect


def db(app) -> sqlite3.Connection:
    return connect(app.config["DATABASE"])


def start_exam(client, mode="random") -> int:
    res = client.post("/exams", data={"mode": mode})
    assert res.status_code == 302
    return int(re.search(r"/exams/(\d+)", res.headers["Location"]).group(1))


def exam_rows(app, exam_id):
    conn = db(app)
    rows = conn.execute(
        "SELECT eq.position, eq.question_id, eq.choice_order, q.topic_id FROM exam_questions eq "
        "JOIN questions q ON q.id = eq.question_id WHERE eq.exam_id = ? ORDER BY position",
        (exam_id,),
    ).fetchall()
    correct = {
        r["question_id"]: r["id"] for r in conn.execute("SELECT id, question_id FROM choices WHERE is_correct = 1")
    }
    conn.close()
    return rows, correct


def answer(client, exam_id, position, choice_id):
    return client.post(f"/api/exams/{exam_id}/answer", json={"position": position, "choice_id": choice_id})


def test_pages_render(client):
    for path in ["/", "/exams", "/flashcards", "/progress", "/profile"]:
        assert client.get(path).status_code == 200, path


def test_seed_is_idempotent_and_deactivates_removed_items(app, content_dir):
    from ddprep.seed import seed

    conn = db(app)
    assert seed(conn, content_dir) is None  # unchanged content: no-op
    bank = json.loads((content_dir / "bank.json").read_text())
    bank["questions"] = bank["questions"][:-1]
    (content_dir / "bank.json").write_text(json.dumps(bank))
    seed(conn, content_dir)
    assert conn.execute("SELECT COUNT(*) FROM questions WHERE active = 0").fetchone()[0] == 1
    conn.close()


def test_random_exam_has_50_unique_questions_with_shuffled_choices(client, app):
    exam_id = start_exam(client)
    rows, _ = exam_rows(app, exam_id)
    assert len(rows) == exams.EXAM_SIZE
    assert len({r["question_id"] for r in rows}) == exams.EXAM_SIZE
    orders = [json.loads(r["choice_order"]) for r in rows]
    assert all(len(o) == 4 for o in orders)
    assert any(o != sorted(o) for o in orders)
    page = client.get(f"/exams/{exam_id}")
    assert page.status_code == 200
    # Unanswered questions never leak their correct answer into the page.
    data = json.loads(re.search(rb'id="exam-data">(.*?)</script>', page.data, re.S).group(1))
    assert all("correct_choice" not in q for q in data)


def test_answer_flow_records_result_and_explanation(client, app):
    exam_id = start_exam(client)
    rows, correct = exam_rows(app, exam_id)
    first, second = rows[0], rows[1]

    res = answer(client, exam_id, first["position"], correct[first["question_id"]])
    body = res.get_json()
    assert res.status_code == 200 and body["is_correct"] is True
    assert "synthetic fixture" in body["explanation"]

    wrong = next(c for c in json.loads(second["choice_order"]) if c != correct[second["question_id"]])
    body = answer(client, exam_id, second["position"], wrong).get_json()
    assert body["is_correct"] is False and body["correct_choice"] == correct[second["question_id"]]

    # Can't change a submitted answer or use a choice from another question.
    assert answer(client, exam_id, first["position"], wrong).status_code == 400
    assert answer(client, exam_id, rows[2]["position"], wrong).status_code == 400

    res = client.post(f"/exams/{exam_id}/finish")
    assert res.status_code == 302
    conn = db(app)
    exam = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    assert exam["correct"] == 1 and exam["score"] == 2.0  # 1 of 50; unanswered count as wrong
    conn.close()

    results = client.get(f"/exams/{exam_id}/results")
    assert results.status_code == 200 and b"1 of 50 correct" in results.data
    assert answer(client, exam_id, rows[3]["position"], correct[rows[3]["question_id"]]).status_code == 400


def test_targeted_exam_emphasises_missed_topic(client, app):
    # Take a full exam: miss every question in one topic, get the rest right.
    exam_id = start_exam(client)
    rows, correct = exam_rows(app, exam_id)
    weak_topic = Counter(r["topic_id"] for r in rows).most_common(1)[0][0]
    for r in rows:
        right = correct[r["question_id"]]
        choice = right if r["topic_id"] != weak_topic else next(c for c in json.loads(r["choice_order"]) if c != right)
        assert answer(client, exam_id, r["position"], choice).status_code == 200
    client.post(f"/exams/{exam_id}/finish")

    conn = db(app)
    topic_count = conn.execute("SELECT COUNT(DISTINCT topic_id) FROM questions").fetchone()[0]
    weak_pool = conn.execute("SELECT COUNT(*) FROM questions WHERE topic_id = ?", (weak_topic,)).fetchone()[0]
    conn.close()

    counts = Counter()
    for _ in range(5):
        rows, _ = exam_rows(app, start_exam(client, "targeted"))
        counts.update(r["topic_id"] for r in rows)
    average_topic = sum(counts.values()) / topic_count
    assert counts[weak_topic] / 5 >= min(weak_pool, 13) * 0.8
    assert counts[weak_topic] > average_topic * 1.5


def test_flashcard_session_review_and_retry(client, app):
    res = client.post("/flashcards", data={"mode": "random", "size": "10"})
    assert res.status_code == 302
    session_id = int(res.headers["Location"].rsplit("/", 1)[1])
    page = client.get(f"/flashcards/{session_id}")
    cards = json.loads(re.search(rb'id="cards-data">(.*?)</script>', page.data, re.S).group(1))
    assert len(cards) == 10

    for i, card in enumerate(cards):
        rating = "learning" if i < 3 else "known"
        res = client.post(f"/api/flashcards/{session_id}/review", json={"card_id": card["id"], "rating": rating})
        assert res.status_code == 200
    assert client.post(f"/api/flashcards/{session_id}/review", json={"card_id": 999999, "rating": "known"}).status_code == 400

    res = client.post(f"/flashcards/{session_id}/retry")
    retry_id = int(res.headers["Location"].rsplit("/", 1)[1])
    conn = db(app)
    retry = conn.execute("SELECT card_ids FROM flashcard_sessions WHERE id = ?", (retry_id,)).fetchone()
    conn.close()
    assert sorted(json.loads(retry["card_ids"])) == sorted(c["id"] for c in cards[:3])


def test_flashcards_scoped_to_topic(client, app):
    conn = db(app)
    topic_id = conn.execute("SELECT topic_id FROM flashcards LIMIT 1").fetchone()[0]
    conn.close()
    res = client.post("/flashcards", data={"mode": "random", "size": "50", "scope": f"topic:{topic_id}"})
    session_id = int(res.headers["Location"].rsplit("/", 1)[1])
    conn = db(app)
    ids = json.loads(conn.execute("SELECT card_ids FROM flashcard_sessions WHERE id = ?", (session_id,)).fetchone()[0])
    topics = {r[0] for r in conn.execute(f"SELECT topic_id FROM flashcards WHERE id IN ({','.join(map(str, ids))})")}
    conn.close()
    assert topics == {topic_id} and len(ids) == 3


def test_profiles_are_isolated(client, signed_in_client):
    exam_id = start_exam(client)
    other = signed_in_client("other", "other@example.com", "Other")
    assert other.get(f"/exams/{exam_id}").status_code == 404
    assert other.post(f"/exams/{exam_id}/delete").status_code == 404
    assert b"taken an exam yet" in other.get("/exams").data

    assert client.get(f"/exams/{exam_id}").status_code == 200
    client.post("/profile", data={"action": "reset"})
    assert client.get(f"/exams/{exam_id}").status_code == 404


def test_rich_text_filters_escape_html_and_format_code():
    from ddprep.views import format_question, format_rich

    assert str(format_rich("Run `datadog-agent status` <b>")) == "Run <code>datadog-agent status</code> &lt;b&gt;"
    html = str(format_question("Given:\nkey: <v>\n  - item\nWhat is it?"))
    assert html == (
        '<span>Given:</span><pre class="snippet">key: &lt;v&gt;\n  - item</pre><span>What is it?</span>'
    )
