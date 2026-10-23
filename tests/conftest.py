from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from testprep import auth, create_app
from testprep.db import connect

ROOT = Path(__file__).resolve().parent.parent

CERT = "datadog-fundamentals"  # the real taxonomy, with a synthetic bank
OTHER = "other-cert"  # a second, small certification whose topic names overlap Datadog's
EMPTY = "empty-cert"  # a certification with no content yet
OTHER_TOPICS = ["Dashboards", "Linux Basics"]


def write_bank(cert_dir: Path, topics: list[str], prefix: str, per_topic: int, cards_per_topic: int) -> None:
    questions, cards = [], []
    for ti, topic in enumerate(topics):
        for n in range(per_topic):
            questions.append(
                {
                    "id": f"{prefix}t{ti}-q-{n}",
                    "topic": topic,
                    "question": f"Synthetic {prefix}question {n} about {topic}?",
                    "choices": ["right answer", "wrong one", "wrong two", "wrong three"],
                    "answer": 0,
                    "explanation": "The first choice is right because this is a synthetic fixture used in tests.",
                    "reference": "https://docs.datadoghq.com/",
                    "difficulty": 1 + n % 3,
                }
            )
        for n in range(cards_per_topic):
            cards.append(
                {"id": f"{prefix}t{ti}-fc-{n}", "topic": topic, "front": f"{prefix}{topic} card {n}", "back": "Back side."}
            )
    (cert_dir / "bank.json").write_text(json.dumps({"questions": questions, "flashcards": cards}), encoding="utf-8")


def build_content(target: Path, per_topic: int = 5, cards_per_topic: int = 3) -> list[str]:
    """Write synthetic banks for three certifications (see CERT, OTHER, EMPTY). Returns CERT's topic names."""
    cert_dir = target / CERT
    cert_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "content" / CERT / "taxonomy.json", cert_dir / "taxonomy.json")
    taxonomy = json.loads((cert_dir / "taxonomy.json").read_text(encoding="utf-8"))
    topics = [t["name"] for d in taxonomy["domains"] for t in d["topics"]]
    write_bank(cert_dir, topics, "", per_topic, cards_per_topic)

    other_dir = target / OTHER
    other_dir.mkdir()
    (other_dir / "taxonomy.json").write_text(json.dumps({
        "name": "Other Cert", "vendor": "Acme", "docs_url": "https://docs.example.com/",
        "domains": [{"name": "Essential Concepts", "slug": "essential", "topics": [{"name": t} for t in OTHER_TOPICS]}],
    }), encoding="utf-8")
    write_bank(other_dir, OTHER_TOPICS, "other-", per_topic, cards_per_topic)

    (target / EMPTY).mkdir()
    (target / EMPTY / "taxonomy.json").write_text(json.dumps({"name": "Empty Cert", "domains": []}), encoding="utf-8")
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
