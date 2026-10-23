"""Per-user performance statistics derived from recorded exam answers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Beta(1, 1) prior: an unseen topic is treated as 50% known, and a single
# answer only moves the estimate part of the way.
PRIOR_CORRECT = 1
PRIOR_ATTEMPTS = 2

# Mastery bands used in the UI.
STRONG_AT = 0.8
FAIR_AT = 0.6


@dataclass
class TopicStat:
    topic_id: int
    topic: str
    domain_id: int
    domain: str
    attempts: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.attempts if self.attempts else None

    @property
    def estimated_accuracy(self) -> float:
        return (self.correct + PRIOR_CORRECT) / (self.attempts + PRIOR_ATTEMPTS)

    @property
    def weakness(self) -> float:
        return 1.0 - self.estimated_accuracy

    @property
    def band(self) -> str:
        if not self.attempts:
            return "new"
        acc = self.accuracy or 0.0
        if acc >= STRONG_AT:
            return "strong"
        if acc >= FAIR_AT:
            return "fair"
        return "weak"


def topic_weight(stat: TopicStat) -> float:
    """Selection weight for a topic: weak topics count several times more than strong ones.

    40% accuracy over 10 answers -> ~0.88, 90% over 10 -> ~0.26, unseen -> 0.70.
    """
    return 0.2 + 2.0 * stat.weakness**2


def topic_stats(conn: sqlite3.Connection, user_id: int, cert_id: int) -> dict[int, TopicStat]:
    """Every topic of one certification, with the user's exam answers in it."""
    stats = {
        row["id"]: TopicStat(row["id"], row["name"], row["domain_id"], row["domain"])
        for row in conn.execute(
            "SELECT t.id, t.name, t.domain_id, d.name AS domain FROM topics t "
            "JOIN domains d ON d.id = t.domain_id WHERE t.certification_id = ? ORDER BY d.sort_order, t.sort_order",
            (cert_id,),
        )
    }
    rows = conn.execute(
        "SELECT q.topic_id, COUNT(*) AS attempts, SUM(eq.is_correct) AS correct "
        "FROM exam_questions eq "
        "JOIN exams e ON e.id = eq.exam_id "
        "JOIN questions q ON q.id = eq.question_id "
        "WHERE e.user_id = ? AND e.certification_id = ? AND eq.selected_choice_id IS NOT NULL "
        "GROUP BY q.topic_id",
        (user_id, cert_id),
    )
    for row in rows:
        stat = stats.get(row["topic_id"])
        if stat:
            stat.attempts = row["attempts"]
            stat.correct = row["correct"] or 0
    return stats


def domain_stats(topics: dict[int, TopicStat]) -> list[dict]:
    domains: dict[int, dict] = {}
    for stat in topics.values():
        d = domains.setdefault(
            stat.domain_id, {"id": stat.domain_id, "name": stat.domain, "attempts": 0, "correct": 0, "topics": []}
        )
        d["attempts"] += stat.attempts
        d["correct"] += stat.correct
        d["topics"].append(stat)
    for d in domains.values():
        d["accuracy"] = d["correct"] / d["attempts"] if d["attempts"] else None
    return list(domains.values())


def question_history(conn: sqlite3.Connection, user_id: int) -> dict[int, list[bool]]:
    """Chronological list of right/wrong results for every question the user answered."""
    history: dict[int, list[bool]] = {}
    rows = conn.execute(
        "SELECT eq.question_id, eq.is_correct FROM exam_questions eq "
        "JOIN exams e ON e.id = eq.exam_id "
        "WHERE e.user_id = ? AND eq.selected_choice_id IS NOT NULL "
        "ORDER BY eq.answered_at, eq.id",
        (user_id,),
    )
    for row in rows:
        history.setdefault(row["question_id"], []).append(bool(row["is_correct"]))
    return history


def flashcard_history(conn: sqlite3.Connection, user_id: int) -> dict[int, str]:
    """Most recent self-rating ('known' / 'learning') for each flash card the user reviewed."""
    rows = conn.execute(
        "SELECT flashcard_id, rating FROM flashcard_reviews WHERE user_id = ? ORDER BY reviewed_at, id",
        (user_id,),
    )
    return {row["flashcard_id"]: row["rating"] for row in rows}


def answered_count(conn: sqlite3.Connection, user_id: int, cert_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM exam_questions eq JOIN exams e ON e.id = eq.exam_id "
        "WHERE e.user_id = ? AND e.certification_id = ? AND eq.selected_choice_id IS NOT NULL",
        (user_id, cert_id),
    ).fetchone()[0]
