"""Validate TestPrep content files.

Each certification is a folder, content/<slug>/, holding taxonomy.json (name, vendor, domains and topics)
and any number of question/flash card banks.

Usage:
    python scripts/validate_content.py                                      # validate every bank
    python scripts/validate_content.py content/datadog-fundamentals/x.json  # specific files (ids still checked globally)

Exits non-zero if any error is found. Warnings are printed but do not fail.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content"
TAXONOMY_FILE = "taxonomy.json"

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BANNED_CHOICE_RE = re.compile(r"\b(all|none|both) of the (above|below)\b", re.I)
POSITIONAL_RE = re.compile(r"\b(option|answer|choice) [a-d]\b|\b[a-d] and [a-d]\b", re.I)
# Top-level app routes a certification slug can't shadow.
RESERVED_SLUGS = {"api", "auth", "login", "logout", "profile", "static"}


def certification_dirs() -> list[Path]:
    return sorted(p.parent for p in CONTENT_DIR.glob(f"*/{TAXONOMY_FILE}"))


def validate_taxonomy(cert_dir: Path, errors: list[str]) -> dict[str, str]:
    """Check one certification's taxonomy.json. Returns its topic name -> domain name."""
    where = f"{cert_dir.name}/{TAXONOMY_FILE}"
    if not ID_RE.match(cert_dir.name) or cert_dir.name in RESERVED_SLUGS:
        errors.append(
            f"{cert_dir.name}: the folder name is the URL slug; use lowercase kebab-case other than "
            f"{sorted(RESERVED_SLUGS)}"
        )
    try:
        taxonomy = json.loads((cert_dir / TAXONOMY_FILE).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{where}: invalid JSON: {exc}")
        return {}
    if not str(taxonomy.get("name", "")).strip():
        errors.append(f"{where}: missing certification 'name'")
    if taxonomy.get("docs_url") and not str(taxonomy["docs_url"]).startswith("https://"):
        errors.append(f"{where}: docs_url must be an https URL")
    topics: dict[str, str] = {}
    for d in taxonomy.get("domains", []):
        for t in d.get("topics", []):
            if t["name"] in topics:
                errors.append(f"{where}: topic '{t['name']}' appears more than once")
            topics[t["name"]] = d["name"]
    return topics


def content_files() -> list[Path]:
    return [p for d in certification_dirs() for p in sorted(d.glob("*.json")) if p.name != TAXONOMY_FILE]


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def label(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def validate(paths: list[Path]) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    topics_by_cert = {d: validate_taxonomy(d, errors) for d in certification_dirs()}

    # Load every bank so id / duplicate checks are global even when validating one file.
    banks: dict[Path, dict] = {}
    for path in content_files() + [p for p in paths if p not in content_files()]:
        try:
            banks[path] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{label(path)}: invalid JSON: {exc}")

    seen_ids: dict[str, str] = {}
    seen_questions: dict[str, str] = {}
    seen_fronts: dict[str, str] = {}
    for path, bank in banks.items():
        for kind in ("questions", "flashcards"):
            for item in bank.get(kind, []):
                item_id = item.get("id", "")
                if item_id in seen_ids:
                    errors.append(f"{label(path)}: duplicate id '{item_id}' (also in {seen_ids[item_id]})")
                seen_ids.setdefault(item_id, label(path))
        for q in bank.get("questions", []):
            key = normalize(q.get("question", ""))
            if key in seen_questions and seen_questions[key] != q.get("id"):
                warnings.append(f"{label(path)}: question '{q.get('id')}' duplicates '{seen_questions[key]}'")
            seen_questions.setdefault(key, q.get("id"))
        for c in bank.get("flashcards", []):
            key = normalize(c.get("front", ""))
            if key in seen_fronts and seen_fronts[key] != c.get("id"):
                warnings.append(f"{label(path)}: flashcard '{c.get('id')}' duplicates '{seen_fronts[key]}'")
            seen_fronts.setdefault(key, c.get("id"))

    targets = paths or list(banks)
    for path in targets:
        bank = banks.get(path)
        if bank is None:
            continue
        name = label(path)
        topics = topics_by_cert.get(path.parent)
        if topics is None:
            errors.append(f"{name}: banks belong in a content/<slug>/ folder next to its {TAXONOMY_FILE}")
            continue
        if not isinstance(bank, dict) or set(bank) - {"questions", "flashcards"}:
            errors.append(f"{name}: top level must be an object with only 'questions' and 'flashcards'")
            continue

        longest_correct = 0
        answer_positions: Counter[int] = Counter()
        questions = bank.get("questions", [])
        for i, q in enumerate(questions):
            where = f"{name}: question #{i} ({q.get('id', '?')})"
            missing = {"id", "topic", "question", "choices", "answer", "explanation", "reference"} - set(q)
            if missing:
                errors.append(f"{where}: missing fields {sorted(missing)}")
                continue
            extra = set(q) - {"id", "topic", "question", "choices", "answer", "explanation", "reference", "difficulty"}
            if extra:
                errors.append(f"{where}: unknown fields {sorted(extra)}")
            if not ID_RE.match(q["id"]):
                errors.append(f"{where}: id must be lowercase kebab-case")
            if q["topic"] not in topics:
                errors.append(f"{where}: unknown topic '{q['topic']}'")
            if len(q["question"].strip()) < 15:
                errors.append(f"{where}: question text too short")
            choices = q["choices"]
            if not isinstance(choices, list) or not 3 <= len(choices) <= 4:
                errors.append(f"{where}: must have 3 or 4 choices")
                continue
            if any(not isinstance(c, str) or not c.strip() for c in choices):
                errors.append(f"{where}: choices must be non-empty strings")
                continue
            # Punctuation matters here (e.g. `env:prod` vs `-env:prod`), so compare near-verbatim.
            if len({" ".join(c.lower().split()) for c in choices}) != len(choices):
                errors.append(f"{where}: choices must be distinct")
            for c in choices:
                if BANNED_CHOICE_RE.search(c) or POSITIONAL_RE.search(c):
                    errors.append(f"{where}: choice '{c}' refers to other choices; choices are shuffled")
            if not isinstance(q["answer"], int) or not 0 <= q["answer"] < len(choices):
                errors.append(f"{where}: answer must be a valid 0-based index into choices")
                continue
            answer_positions[q["answer"]] += 1
            lengths = [len(c) for c in choices]
            if lengths[q["answer"]] == max(lengths) and lengths.count(max(lengths)) == 1:
                longest_correct += 1
            if len(q["explanation"].strip()) < 60:
                errors.append(f"{where}: explanation should be at least 60 characters")
            if not str(q["reference"]).startswith("https://"):
                errors.append(f"{where}: reference must be an https URL")
            if "difficulty" in q and q["difficulty"] not in (1, 2, 3):
                errors.append(f"{where}: difficulty must be 1, 2 or 3")

        if questions:
            ratio = longest_correct / len(questions)
            if ratio > 0.45:
                warnings.append(
                    f"{name}: correct answer is the uniquely longest choice in {ratio:.0%} of questions "
                    "(aim for <= 45% so length does not give the answer away)"
                )

        for i, c in enumerate(bank.get("flashcards", [])):
            where = f"{name}: flashcard #{i} ({c.get('id', '?')})"
            missing = {"id", "topic", "front", "back"} - set(c)
            if missing:
                errors.append(f"{where}: missing fields {sorted(missing)}")
                continue
            extra = set(c) - {"id", "topic", "front", "back", "reference"}
            if extra:
                errors.append(f"{where}: unknown fields {sorted(extra)}")
            if not ID_RE.match(c["id"]):
                errors.append(f"{where}: id must be lowercase kebab-case")
            if c["topic"] not in topics:
                errors.append(f"{where}: unknown topic '{c['topic']}'")
            if len(c["front"].strip()) < 5 or len(c["back"].strip()) < 5:
                errors.append(f"{where}: front and back must be non-trivial")
            if "reference" in c and not str(c["reference"]).startswith("https://"):
                errors.append(f"{where}: reference must be an https URL")

        per_topic = Counter(q["topic"] for q in questions if "topic" in q)
        cards_per_topic = Counter(c["topic"] for c in bank.get("flashcards", []) if "topic" in c)
        print(f"{name}: {len(questions)} questions, {len(bank.get('flashcards', []))} flashcards")
        for topic in sorted(set(per_topic) | set(cards_per_topic)):
            print(f"    {topic}: {per_topic[topic]} q / {cards_per_topic[topic]} cards")

    if not paths:
        for cert_dir, topics in topics_by_cert.items():
            if not any(p.parent == cert_dir for p in banks):
                print(f"{cert_dir.name}: no questions or flashcards yet ({len(topics)} topics)")

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    args = [Path(a).resolve() for a in sys.argv[1:]]
    sys.exit(validate(args))
