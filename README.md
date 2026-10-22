# TestPrep: Datadog Fundamentals Exam Prep

A local web app for studying for the **Datadog Fundamentals** certification. It has two sections:

- **Practice exams**: 50 single-answer multiple-choice questions per exam. You submit each answer and immediately see whether it was right, with an explanation and a link to the Datadog docs. At the end you get your percentage score, a breakdown by domain and topic, and a full answer review. Choose between:
  - **Random**: 50 questions drawn uniformly from the whole bank.
  - **Targeted**: weighted toward the topics and questions you've struggled with.
- **Flash cards**: flip-and-rate decks (10–50 cards) for all domains, a single domain or a single topic.
  - **Random**: an even shuffle.
  - **Targeted**: mostly cards from the topics you missed in exams, plus cards you rated "Still learning".

Every exam answer and flash card rating is saved in SQLite, so the Progress page can show your score trend and per-topic mastery. Everyone **signs in with Google or Microsoft** before they can see anything, and each account has its own profile and history.

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env, then add your sign-in client ids (see Sign-in)
python app.py --open            # http://localhost:5000
```

Options: `--port 8080`, `--host 0.0.0.0`, `--db path/to/file.sqlite`, `--content path/to/content`, `--debug`.

Browse to `http://localhost:5000`, not `http://127.0.0.1:5000`: the sign-in redirect URIs below are registered for `localhost`.

On first start the app creates `instance/testprep.sqlite` and loads the question bank into it. When the content files change, it re-syncs them on the next start.

## Sign-in

Every page needs a signed-in user. Users sign in with Google or Microsoft (OpenID Connect via [Authlib](https://docs.authlib.org/)). The first sign-in creates a profile for that account. Signing in later with the same account opens the same profile. Each provider shows up on the sign-in page once its client id and secret are set.

Settings come from `TESTPREP_*` environment variables. `python app.py` and `docker compose up` both read them from `.env` (copy `.env.example`). Any `TESTPREP_<KEY>` sets Flask config `<KEY>`.

| Variable | |
|---|---|
| `TESTPREP_GOOGLE_CLIENT_ID`, `TESTPREP_GOOGLE_CLIENT_SECRET` | Enables **Continue with Google** |
| `TESTPREP_MICROSOFT_CLIENT_ID`, `TESTPREP_MICROSOFT_CLIENT_SECRET` | Enables **Continue with Microsoft** |
| `TESTPREP_MICROSOFT_TENANT` | `common` (default: work, school and personal accounts), `organizations`, `consumers` or a tenant id |
| `TESTPREP_ALLOWED_EMAILS` | Optional. Comma-separated addresses and/or domains (`you@example.com,example.org`). Anyone else is turned away. Empty means any Google or Microsoft account can sign in |
| `TESTPREP_BEHIND_PROXY` | `true` when an HTTPS reverse proxy sits in front, so callback URLs use the public `https://` address |
| `TESTPREP_SESSION_COOKIE_SECURE` | `true` to send the session cookie over HTTPS only (set it when serving over HTTPS) |

The redirect URI is `<address>/auth/<provider>/callback`. Register one for every address you use: `http://localhost:5000` for `python app.py`, `http://localhost:5050` for `docker compose`, and `https://<your-host>` in production.

**Google.** In the [Google Cloud console](https://console.cloud.google.com/apis/credentials), set up the OAuth consent screen. Then go to *Create credentials → OAuth client ID → Web application* and add the redirect URIs, e.g. `http://localhost:5000/auth/google/callback`. Copy the client ID and secret into `.env`. While the consent screen is in *Testing*, only the test users you list can sign in.

**Microsoft.** In the [Microsoft Entra admin center](https://entra.microsoft.com), go to *App registrations → New registration*. Pick the supported account types that match `TESTPREP_MICROSOFT_TENANT`: *Accounts in any organizational directory and personal Microsoft accounts* for `common`. Add a *Web* redirect URI, e.g. `http://localhost:5000/auth/microsoft/callback`. Then go to *Certificates & secrets → New client secret*. Copy the *Application (client) ID* and the secret's **Value** into `.env`. Client secrets expire, so note the date.

`TESTPREP_ALLOWED_EMAILS` checks Google's verified email. For Microsoft it checks the sign-in name (`preferred_username`), because Microsoft doesn't verify the optional `email` claim.

### Profiles from before sign-in

Profiles created before sign-in existed have no account. To keep one's history, sign in once with the account, then link it. That account's new profile, and anything recorded in it, merges into the old one:

```bash
python -m flask --app testprep profiles list
python -m flask --app testprep profiles link "Vova" you@example.com
docker compose exec testprep python -m flask --app testprep profiles link "Vova" you@example.com   # in Docker
```

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

`testprep/selection.py` and `testprep/stats.py`:

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
testprep/
  __init__.py           app factory (config, creates DB, syncs content)
  auth.py               Google / Microsoft sign-in and the login gate
  cli.py                `python -m flask --app testprep profiles ...` admin commands
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
