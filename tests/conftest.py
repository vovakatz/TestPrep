from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ddprep import auth, create_app
from ddprep.db import connect

ROOT = Path(__file__).resolve().parent.parent


def build_content(target: Path, per_topic: int = 5, cards_per_topic: int = 3) -> list[str]:
    """Write a synthetic bank covering every taxonomy topic. Returns topic names."""
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "content" / "taxonomy.json", target / "taxonomy.json")
    taxonomy = json.loads((target / "taxonomy.json").read_text(encoding="utf-8"))
    topics = [t["name"] for d in taxonomy["domains"] for t in d["topics"]]
    questions, cards = [], []
    for ti, topic in enumerate(topics):
        for n in range(per_topic):
            questions.append(
                {
                    "id": f"t{ti}-q-{n}",
                    "topic": topic,
                    "question": f"Synthetic question {n} about {topic}?",
                    "choices": ["right answer", "wrong one", "wrong two", "wrong three"],
                    "answer": 0,
                    "explanation": "The first choice is right because this is a synthetic fixture used in tests.",
                    "reference": "https://docs.datadoghq.com/",
                    "difficulty": 1 + n % 3,
                }
            )
        for n in range(cards_per_topic):
            cards.append({"id": f"t{ti}-fc-{n}", "topic": topic, "front": f"{topic} card {n}", "back": "Back side text."})
    (target / "bank.json").write_text(json.dumps({"questions": questions, "flashcards": cards}), encoding="utf-8")
    return topics


@pytest.fixture
def content_dir(tmp_path: Path) -> Path:
    path = tmp_path / "content"
    build_content(path)
    return path


@pytest.fixture
def app(tmp_path: Path, content_dir: Path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.sqlite"),
            "CONTENT_DIR": str(content_dir),
            "SECRET_KEY": "test",
            "GOOGLE_CLIENT_ID": "google-client",
            "GOOGLE_CLIENT_SECRET": "google-secret",
            "MICROSOFT_CLIENT_ID": "microsoft-client",
            "MICROSOFT_CLIENT_SECRET": "microsoft-secret",
        }
    )


@pytest.fixture
def signed_in_client(app):
    """Return a factory: make_client(subject, email, name) -> test client signed in as that Google account."""

    def make_client(subject: str = "tester", email: str = "tester@example.com", name: str = "Tester"):
        conn = connect(app.config["DATABASE"])
        try:
            user_id = auth.sign_in(conn, "google", subject, email, name)
        finally:
            conn.close()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        return client

    return make_client


@pytest.fixture
def client(signed_in_client):
    return signed_in_client()


@pytest.fixture
def anon_client(app):
    return app.test_client()
