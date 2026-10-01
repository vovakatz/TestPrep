"""Exam lifecycle: create, answer, finish, review."""

from __future__ import annotations

import json
import random
import sqlite3

from . import selection
from .db import now
from .stats import question_history, topic_stats

EXAM_SIZE = 50


class ExamError(Exception):
    """Invalid exam action (unknown exam, already answered, bad choice...)."""


def create_exam(conn: sqlite3.Connection, user_id: int, mode: str, rng: random.Random | None = None) -> int:
    if mode not in ("random", "targeted"):
        raise ExamError(f"Unknown exam mode: {mode}")
    rng = rng or random.Random()
    pool = [
        selection.Item(row["id"], row["topic_id"])
        for row in conn.execute("SELECT id, topic_id FROM questions WHERE active = 1 ORDER BY id")
    ]
    if not pool:
        raise ExamError("The question bank is empty.")

    if mode == "random":
        picked = selection.pick_random(pool, EXAM_SIZE, rng)
    else:
        picked = selection.pick_targeted_questions(
            pool, EXAM_SIZE, topic_stats(conn, user_id), question_history(conn, user_id), rng
        )
        rng.shuffle(picked)

    cur = conn.execute(
        "INSERT INTO exams (user_id, mode, started_at, total) VALUES (?, ?, ?, ?)",
        (user_id, mode, now(), len(picked)),
    )
    exam_id = cur.lastrowid
    for position, item in enumerate(picked, start=1):
        choice_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM choices WHERE question_id = ? AND active = 1 ORDER BY position", (item.id,)
            )
        ]
        rng.shuffle(choice_ids)
        conn.execute(
            "INSERT INTO exam_questions (exam_id, position, question_id, choice_order) VALUES (?, ?, ?, ?)",
            (exam_id, position, item.id, json.dumps(choice_ids)),
        )
    conn.commit()
    return exam_id


def get_exam(conn: sqlite3.Connection, exam_id: int, user_id: int) -> sqlite3.Row:
    exam = conn.execute("SELECT * FROM exams WHERE id = ? AND user_id = ?", (exam_id, user_id)).fetchone()
    if exam is None:
        raise ExamError("Exam not found.")
    return exam


def _choices_for(conn: sqlite3.Connection, choice_order: str) -> list[sqlite3.Row]:
    ids = json.loads(choice_order)
    rows = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT id, text, is_correct FROM choices WHERE id IN ({','.join('?' * len(ids))})", ids
        )
    }
    return [rows[i] for i in ids if i in rows]


def exam_items(conn: sqlite3.Connection, exam_id: int, include_answers: bool) -> list[dict]:
    """All questions of an exam in order. Correct answers are only included for answered items
    unless include_answers is set (review mode)."""
    rows = conn.execute(
        "SELECT eq.*, q.text, q.explanation, q.reference, q.difficulty, t.name AS topic, d.name AS domain "
        "FROM exam_questions eq "
        "JOIN questions q ON q.id = eq.question_id "
        "JOIN topics t ON t.id = q.topic_id "
        "JOIN domains d ON d.id = t.domain_id "
        "WHERE eq.exam_id = ? ORDER BY eq.position",
        (exam_id,),
    ).fetchall()
    items = []
    for row in rows:
        choices = _choices_for(conn, row["choice_order"])
        answered = row["selected_choice_id"] is not None
        reveal = answered or include_answers
        item = {
            "position": row["position"],
            "question": row["text"],
            "topic": row["topic"],
            "domain": row["domain"],
            "difficulty": row["difficulty"],
            "choices": [{"id": c["id"], "text": c["text"]} for c in choices],
            "answered": answered,
            "selected": row["selected_choice_id"],
            "is_correct": bool(row["is_correct"]) if answered else None,
        }
        if reveal:
            item["correct_choice"] = next((c["id"] for c in choices if c["is_correct"]), None)
            item["explanation"] = row["explanation"]
            item["reference"] = row["reference"]
        items.append(item)
    return items


def answer(conn: sqlite3.Connection, exam_id: int, user_id: int, position: int, choice_id: int) -> dict:
    exam = get_exam(conn, exam_id, user_id)
    if exam["completed_at"]:
        raise ExamError("This exam is already finished.")
    row = conn.execute(
        "SELECT eq.id, eq.choice_order, eq.selected_choice_id, q.explanation, q.reference "
        "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
        "WHERE eq.exam_id = ? AND eq.position = ?",
        (exam_id, position),
    ).fetchone()
    if row is None:
        raise ExamError("Question not found in this exam.")
    if row["selected_choice_id"] is not None:
        raise ExamError("This question has already been answered.")
    choices = _choices_for(conn, row["choice_order"])
    if choice_id not in {c["id"] for c in choices}:
        raise ExamError("That choice does not belong to this question.")

    correct_choice = next(c["id"] for c in choices if c["is_correct"])
    is_correct = choice_id == correct_choice
    conn.execute(
        "UPDATE exam_questions SET selected_choice_id = ?, is_correct = ?, answered_at = ? WHERE id = ?",
        (choice_id, int(is_correct), now(), row["id"]),
    )
    conn.commit()
    return {
        "is_correct": is_correct,
        "selected": choice_id,
        "correct_choice": correct_choice,
        "explanation": row["explanation"],
        "reference": row["reference"],
    }


def finish(conn: sqlite3.Connection, exam_id: int, user_id: int) -> sqlite3.Row:
    """Score the exam. Unanswered questions count as incorrect, as on the real exam."""
    exam = get_exam(conn, exam_id, user_id)
    if not exam["completed_at"]:
        correct = conn.execute(
            "SELECT COUNT(*) FROM exam_questions WHERE exam_id = ? AND is_correct = 1", (exam_id,)
        ).fetchone()[0]
        score = round(100.0 * correct / exam["total"], 1) if exam["total"] else 0.0
        conn.execute(
            "UPDATE exams SET completed_at = ?, correct = ?, score = ? WHERE id = ?",
            (now(), correct, score, exam_id),
        )
        conn.commit()
    return get_exam(conn, exam_id, user_id)


def breakdown(items: list[dict]) -> list[dict]:
    """Score by domain, each with its topics, in first-seen order."""
    domains: dict[str, dict] = {}
    for item in items:
        d = domains.setdefault(item["domain"], {"name": item["domain"], "total": 0, "correct": 0, "topics": {}})
        t = d["topics"].setdefault(item["topic"], {"name": item["topic"], "total": 0, "correct": 0})
        for bucket in (d, t):
            bucket["total"] += 1
            bucket["correct"] += 1 if item["is_correct"] else 0
    result = []
    for d in domains.values():
        d["topics"] = sorted(d["topics"].values(), key=lambda t: t["correct"] / t["total"])
        result.append(d)
    return sorted(result, key=lambda d: d["correct"] / d["total"])


def history(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT e.*, (SELECT COUNT(*) FROM exam_questions eq WHERE eq.exam_id = e.id "
        "AND eq.selected_choice_id IS NOT NULL) AS answered "
        "FROM exams e WHERE e.user_id = ? ORDER BY e.started_at DESC, e.id DESC",
        (user_id,),
    ).fetchall()


def delete(conn: sqlite3.Connection, exam_id: int, user_id: int) -> None:
    get_exam(conn, exam_id, user_id)
    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    conn.commit()
