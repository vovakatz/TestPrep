"""Pick exam questions and flash cards, either uniformly or weighted toward weak areas."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

from .stats import TopicStat, topic_weight

# A targeted exam never draws more than this share of its questions from one topic.
MAX_TOPIC_SHARE = 0.25


@dataclass(frozen=True)
class Item:
    id: int
    topic_id: int


def weighted_sample(
    items: Sequence[Item],
    weights: Sequence[float],
    k: int,
    rng: random.Random,
    max_per_topic: int | None = None,
) -> list[Item]:
    """Weighted sampling without replacement (Efraimidis-Spirakis), with an optional per-topic cap.

    If the cap leaves the sample short (tiny banks), the remaining slots are filled
    from the capped-out items in key order so the caller always gets min(k, len(items)).
    """
    keyed = sorted(
        zip(items, weights),
        key=lambda pair: math.log(1.0 - rng.random()) / max(pair[1], 1e-9),
        reverse=True,
    )
    chosen: list[Item] = []
    overflow: list[Item] = []
    per_topic: dict[int, int] = {}
    for item, _ in keyed:
        if len(chosen) == k:
            break
        if max_per_topic is not None and per_topic.get(item.topic_id, 0) >= max_per_topic:
            overflow.append(item)
            continue
        per_topic[item.topic_id] = per_topic.get(item.topic_id, 0) + 1
        chosen.append(item)
    chosen.extend(overflow[: k - len(chosen)])
    return chosen


def _topic_multipliers(items: Sequence[Item], topics: dict[int, TopicStat]) -> dict[int, float]:
    """Topic weight normalised so the average topic present in the pool has multiplier 1.

    Keeps the bank's natural topic proportions as the baseline and shifts toward weak topics.
    """
    present = {item.topic_id for item in items}
    raw = {tid: topic_weight(topics[tid]) if tid in topics else 0.7 for tid in present}
    mean = sum(raw.values()) / len(raw) if raw else 1.0
    return {tid: w / mean for tid, w in raw.items()}


def question_factor(history: list[bool] | None) -> float:
    """Per-question adjustment from the user's own answer history."""
    if not history:
        return 1.5  # never seen: worth exploring
    if not history[-1]:
        return 3.0  # most recently missed
    streak = 0
    for result in reversed(history):
        if not result:
            break
        streak += 1
    return 0.6 if streak == 1 else 0.3


def flashcard_factor(last_rating: str | None) -> float:
    if last_rating is None:
        return 1.2
    return 2.5 if last_rating == "learning" else 0.5


def pick_random(items: Sequence[Item], k: int, rng: random.Random) -> list[Item]:
    return rng.sample(list(items), min(k, len(items)))


def pick_targeted_questions(
    items: Sequence[Item],
    k: int,
    topics: dict[int, TopicStat],
    history: dict[int, list[bool]],
    rng: random.Random,
) -> list[Item]:
    multipliers = _topic_multipliers(items, topics)
    weights = [multipliers[i.topic_id] * question_factor(history.get(i.id)) for i in items]
    cap = max(1, math.ceil(k * MAX_TOPIC_SHARE))
    return weighted_sample(items, weights, min(k, len(items)), rng, max_per_topic=cap)


def pick_targeted_flashcards(
    items: Sequence[Item],
    k: int,
    topics: dict[int, TopicStat],
    ratings: dict[int, str],
    rng: random.Random,
) -> list[Item]:
    multipliers = _topic_multipliers(items, topics)
    # Square the topic multiplier: flash cards are "mostly" about weak exam topics.
    weights = [multipliers[i.topic_id] ** 2 * flashcard_factor(ratings.get(i.id)) for i in items]
    return weighted_sample(items, weights, min(k, len(items)), rng)


def focus_topics(topics: dict[int, TopicStat], limit: int = 5) -> list[TopicStat]:
    """Topics a targeted session will emphasise, weakest first (only topics with answers)."""
    seen = [t for t in topics.values() if t.attempts]
    return sorted(seen, key=lambda t: (t.estimated_accuracy, -t.attempts))[:limit]
