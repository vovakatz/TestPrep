import random
from collections import Counter

from testprep import selection
from testprep.stats import TopicStat, topic_weight


def make_items(topics: int, per_topic: int) -> list[selection.Item]:
    return [selection.Item(t * 100 + n, t) for t in range(topics) for n in range(per_topic)]


def test_weighted_sample_returns_k_unique_items():
    items = make_items(5, 20)
    picked = selection.weighted_sample(items, [1.0] * len(items), 30, random.Random(1))
    assert len(picked) == 30
    assert len({i.id for i in picked}) == 30


def test_weighted_sample_respects_topic_cap_and_fills_when_short():
    items = make_items(3, 20)
    weights = [100.0 if i.topic_id == 0 else 0.01 for i in items]
    picked = selection.weighted_sample(items, weights, 10, random.Random(2), max_per_topic=4)
    counts = Counter(i.topic_id for i in picked)
    assert counts[0] == 4 and counts[1] + counts[2] == 6

    # Every topic capped before k is reached: the remainder comes from the heaviest overflow.
    two = make_items(2, 20)
    heavy_first = [100.0 if i.topic_id == 0 else 0.01 for i in two]
    counts = Counter(i.topic_id for i in selection.weighted_sample(two, heavy_first, 10, random.Random(2), 4))
    assert counts == Counter({0: 6, 1: 4})

    # Cap can't be honoured with only one topic: still returns k items.
    single = make_items(1, 20)
    assert len(selection.weighted_sample(single, [1.0] * 20, 10, random.Random(3), max_per_topic=2)) == 10


def test_weighted_sample_prefers_heavier_items():
    items = make_items(2, 50)
    weights = [5.0 if i.topic_id == 0 else 1.0 for i in items]
    rng = random.Random(4)
    counts = Counter()
    for _ in range(200):
        counts.update(i.topic_id for i in selection.weighted_sample(items, weights, 10, rng))
    assert counts[0] > counts[1] * 2


def test_topic_weight_orders_weak_above_unseen_above_strong():
    weak = TopicStat(1, "a", 1, "d", attempts=10, correct=4)
    unseen = TopicStat(2, "b", 1, "d")
    strong = TopicStat(3, "c", 1, "d", attempts=10, correct=9)
    assert topic_weight(weak) > topic_weight(unseen) > topic_weight(strong)
    assert topic_weight(weak) / topic_weight(strong) > 3


def test_question_factor():
    assert selection.question_factor(None) == 1.5
    assert selection.question_factor([True, False]) == 3.0
    assert selection.question_factor([False, True]) == 0.6
    assert selection.question_factor([False, True, True]) == 0.3


def test_targeted_questions_lean_toward_weak_topic():
    items = make_items(6, 30)
    topics = {t: TopicStat(t, f"t{t}", 1, "d", attempts=20, correct=19) for t in range(6)}
    topics[0] = TopicStat(0, "t0", 1, "d", attempts=20, correct=6)
    rng = random.Random(5)
    counts = Counter()
    for _ in range(100):
        counts.update(i.topic_id for i in selection.pick_targeted_questions(items, 50, topics, {}, rng))
    others = sum(counts[t] for t in range(1, 6)) / 5
    assert counts[0] > others * 1.5
    # Cap: never more than 25% of a 50-question exam from one topic.
    single = selection.pick_targeted_questions(items, 50, topics, {}, random.Random(6))
    assert max(Counter(i.topic_id for i in single).values()) <= 13


def test_targeted_flashcards_prefer_learning_cards():
    items = make_items(1, 40)
    topics = {0: TopicStat(0, "t0", 1, "d")}
    ratings = {i.id: ("learning" if i.id < 10 else "known") for i in items}
    rng = random.Random(7)
    hits = sum(
        sum(1 for i in selection.pick_targeted_flashcards(items, 10, topics, ratings, rng) if i.id < 10)
        for _ in range(100)
    )
    assert hits / 1000 > 0.5  # 25% of the pool, well over half of the picks
