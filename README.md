# DDPrep: Datadog Fundamentals Exam Prep

A local web app for studying for the **Datadog Fundamentals** certification. It has two sections:

- **Practice exams**: 50 single-answer multiple-choice questions per exam. You submit each answer and immediately see whether it was right, with an explanation and a link to the Datadog docs. At the end you get your percentage score, a breakdown by domain and topic, and a full answer review. Choose between:
  - **Random**: 50 questions drawn uniformly from the whole bank.
  - **Targeted**: weighted toward the topics and questions you've struggled with.
- **Flash cards**: flip-and-rate decks (10–50 cards) for all domains, a single domain or a single topic.
  - **Random**: an even shuffle.
  - **Targeted**: mostly cards from the topics you missed in exams, plus cards you rated "Still learning".

Every exam answer and flash card rating is saved in SQLite, so the Progress page can show your score trend and per-topic mastery. Several people can share one install through **profiles** (header, top right). Each profile has its own history.

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py --open            # http://127.0.0.1:5000
```

Options: `--port 8080`, `--host 0.0.0.0`, `--db path/to/file.sqlite`, `--content path/to/content`, `--debug`.

On first start the app creates `instance/ddprep.sqlite` and loads the question bank into it. When the content files change, it re-syncs them on the next start.

## Content

The question bank and flash cards live in `content/*.json`. `content/taxonomy.json` lists the six exam-guide domains and their topics:

| Domain | Topics |
|---|---|
| Essential Concepts | Hardware Concepts, Linux Basics, Config File Modification, Scripting Basics, Metadata, Networking, Datadog Observability |
| Infrastructure Development | Agent Installation, API Key, Application Key, Running the Agent, Agent Hostname |
| Networking & Agent Configuration | Datadog Ports, Datadog IP Addresses, Auto-discovery |
| Data Collection | DogStatsD, Crawlers, Agent Integrations, API Endpoints, Tagging Best Practices, Metrics & Timeseries |
| Troubleshooting Datadog | Agent Commands, Agent Logs, Agent Config Files |
| Data Visualization & Utilization | Host Map, Dashboards, Using Metrics, Using Tags, Log & Event Search, Monitors and Alerts |

Questions were written from the Datadog docs linked in the official exam guide (and the pages those link to). They include reworded versions of the official sample questions. Each question cites its source URL.

Question format:

```json
{
  "id": "dc-q-001",
  "topic": "DogStatsD",
  "question": "…",
  "choices": ["…", "…", "…", "…"],
  "answer": 2,
  "explanation": "Why the answer is right and the distractors are wrong.",
  "reference": "https://docs.datadoghq.com/…",
  "difficulty": 2
}
```

Flash card format: `{"id", "topic", "front", "back", "reference"}`.

To add or edit content, change the JSON, then run the validator. It checks the schema, topics, unique ids, duplicate questions and answer-length bias:

```bash
python scripts/validate_content.py
```

Ids are stable keys. Editing an item updates it in place without touching recorded history. Removing an item deactivates it rather than deleting it.

## How targeted selection works

`ddprep/selection.py` and `ddprep/stats.py`:

1. **Topic accuracy.** For each topic, the app estimates accuracy from all of your submitted exam answers, using a Beta(1,1) prior (`(correct+1)/(answered+2)`). An unseen topic counts as 50%, and a couple of lucky answers don't swing it much.
2. **Topic weight.** `0.2 + 2 × (1 − accuracy)²`. With 10 answers each, a topic at 40% gets about 3.4× the weight of a topic at 90%. Weights are normalised so the bank's natural topic mix is the baseline.
3. **Question factor.** Last answer wrong ×3, never seen ×1.5, right once ×0.6, right two or more times in a row ×0.3.
4. **Sampling.** Weighted sampling without replacement. No single topic can take more than 25% of a targeted exam, so you still see breadth.

Targeted flash-card decks square the topic weight, so they lean harder on weak topics. The card factor is: rated "Still learning" ×2.5, unseen ×1.2, "Got it" ×0.5.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

```
app.py                  entry point
ddprep/
  __init__.py           app factory (creates DB, syncs content)
  db.py                 SQLite schema & connections
  seed.py               content/*.json → SQLite upsert
  stats.py              per-topic accuracy from recorded answers
  selection.py          random / weighted selection
  exams.py              exam lifecycle (create, answer, finish, review)
  flashcards.py         decks & ratings
  views.py              Flask routes + JSON endpoints
  templates/, static/   UI (vanilla JS, no build step)
content/                question bank, flash cards, taxonomy
scripts/validate_content.py
tests/
```

This is an unofficial study aid, not affiliated with Datadog. Always confirm details against [docs.datadoghq.com](https://docs.datadoghq.com/).
