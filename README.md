# HoloTutor-X

**A dual-state adaptive tutor for your own study material.**

HoloTutor-X answers questions from the PDFs a student uploads. Before it answers, it looks at two things:

1. **Learner state:** how well the student understands, taken from their *Got it / Not sure / Confused* ratings.
2. **Evidence confidence:** how strongly the uploaded material supports an answer, taken from the retrieval scores.

The pair of states picks the **teaching action**: advance, simplify, give a worked example, ask the student to clarify, or say that the material does not cover the question. The tutor never answers from outside the uploaded material.

> Built on [shubhProcoder/RAG](https://github.com/shubhProcoder/RAG) (MIT licence), a PDF question-answering app. HoloTutor-X adds the dual-state controller, user accounts, an administrator area and an evaluation screen.

---

## How a question is answered

```
Student question
      │
      ▼
Retriever ── searches the student's PDFs (ChromaDB, all-MiniLM-L6-v2)
      │
      ├──► Evidence Confidence Assessor (ECA) ──► Strong / Partial / Conflicting / Insufficient
      │
      ├──► Learner State Estimator (LSE) ───────► Understanding / Uncertain / Confused / Repeated failure
      │
      ▼
Dual-State Controller (DSC) ── 4 × 4 decision table ──► teaching action
      │
      ▼
Prompt builder + LLM ── answer with page citations and a check question
      │
      ▼
Interaction log (for the administrator and the evaluation)
```

### Evidence confidence (ECA)

| State | Rule (default limits, changeable by the administrator) |
|---|---|
| **Insufficient** | No passage found, or the best score is below the partial limit (0.50) |
| **Conflicting** | Two or more passages are above the partial limit and the LLM finds that they contradict each other |
| **Strong** | The best score reaches the strong limit, and at least two passages are above the partial limit |
| **Partial** | Anything else |

### Learner state (LSE)

Each rating is a signal: *Got it* = +1, *Not sure* = 0, *Confused* = −1.

| State | Rule |
|---|---|
| **Repeated failure** | The last three signals are all −1 |
| **Confused** | The latest signal is −1 |
| **Understanding** | The latest signal is +1 and there is no −1 |
| **Uncertain** | Anything else, including a new topic |

### Decision table (DSC), default

| Learner \ Evidence | Strong | Partial | Conflicting | Insufficient |
|---|---|---|---|---|
| **Understanding** | Advance | Clarify | Clarify | State uncertainty |
| **Uncertain** | Worked example | Simplify | Clarify | State uncertainty |
| **Confused** | Simplify | Simplify | Clarify | State uncertainty |
| **Repeated failure** | Worked example | Worked example | State uncertainty | State uncertainty |

The Insufficient column is always *State uncertainty*: the tutor does not guess. Clarify is never chosen twice in a row in one conversation.

### Conversation rules

- Answers end with a **check question**. When the student answers it, the tutor says whether the answer is correct, partly correct or wrong, and explains why from the material.
- Replies that name no topic (*"explain again"*, *"general definition"*, *"because the customer is unsure"*) are searched together with the conversation's topic, and the search with the better score is used.
- A message that ends with `?` or starts with *what, explain, define, describe, tell, give, list, compare* or *show* is treated as a new question.

---

## Features

**Student**
- Sign up (with consent to store questions and ratings), log in, log out
- Upload PDFs and chat with them; answers stream in with sources and page numbers
- *Tutor State* panel under every answer, plus *Got it / Not sure / Confused* buttons
- Conversation list with titles, retry for a failed answer, export of a chat as TXT

**Administrator**
- Users: list accounts, enable or disable them, create evaluator accounts
- Tutor Settings: change the evidence limits and any cell of the decision table, with versions and reset
- Logs: every interaction with its states and action, and a list of unanswered questions

**Evaluator**
- Evaluation screen: run a question set (`scripts/test_questions.csv`) against the tutor and download the results

**Security**
- Passwords of 8 to 64 characters, stored as hashes
- Account lock after repeated failed logins, session timeout after inactivity
- Each student sees only their own documents and conversations

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS and plain JavaScript (single page) |
| API | FastAPI + Uvicorn, server-sent events for streaming |
| PDF text | PyMuPDF, pdfplumber (optional OCR with Tesseract) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` |
| Vector store | ChromaDB |
| Data | SQLite: users, documents, conversations, interaction log |
| LLM | Any OpenAI-compatible endpoint (we use Google Gemini) |
| Tests | pytest (backend), Selenium (browser) |

---

## Running it on Windows

**You need:** Python 3.11 and an API key for an OpenAI-compatible LLM (for example a Gemini key from Google AI Studio).

### 1. Get the code

```bash
git clone https://github.com/tooba-shakeel/Dual-state-Adaptive-controller-holoTutor-X.git
```

```bash
cd Dual-state-Adaptive-controller-holoTutor-X
```

### 2. Create the Python environment (first time only)

```bash
python -m venv .venv
```

```bash
.venv\Scripts\pip install -r backend\requirements.txt
```

### 3. Create `.env` (first time only)

```bash
copy .env.example .env
```

Open `.env` and fill in at least:

| Setting | What to put |
|---|---|
| `OPENAI_API_KEY` | Your LLM API key |
| `OPENAI_API_BASE` | e.g. `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `LLM_MODEL` | e.g. `gemini-2.5-flash` |
| `ADMIN_NAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | The administrator account, created on the first start |

`.env` is in `.gitignore`. **Never commit it or share your key.**

### 4. Start

Double-click **`start_holotutor.bat`**. The browser opens at <http://localhost:8000> after about 20 seconds. Close the black window (or press Ctrl + C in it) to stop the server.

### 5. First use

1. Log in as the administrator, or sign up as a student.
2. Upload a PDF on the **Documents** page and wait until it shows as ready.
3. Ask a question on the **Chat** page.

Uploaded PDFs, the database and the Tutor Settings are kept in `data/`, which is not in Git. A fresh copy starts empty, with the default limits.

---

## Tests

Backend:

```bash
cd backend
```

```bash
..\.venv\Scripts\python -m pytest -q
```

Browser tests (the server must be running): see `tests/selenium/`.

Evaluation from the command line: `scripts/evaluate.py` with `scripts/test_questions.csv`.

---

## Project layout

```
backend/
  app/
    api/routes/      auth, chat, documents, feedback, logs, tutor_settings, evaluation, export, health
    services/        evidence_assessor (ECA), learner_state (LSE), decision_controller (DSC),
                     prompt_builder, chat_service, retriever_service, auth_service, ...
    storage/         SQLite and ChromaDB stores
  tests/             pytest tests
frontend/            index.html, css/, js/ (chat, documents, auth, admin, settings, logs, evaluation)
scripts/             evaluate.py, test_questions.csv
tests/selenium/      browser tests
start_holotutor.bat  starts the server on Windows
```

---

## Branches

- `main`: the stable version. Clone and test this one.
- `feature/integration`: where finished features are combined before they go to `main`.
- `feature/...`: one branch per feature, merged into `feature/integration` with a pull request.

When opening a pull request, check that the **base repository** is `tooba-shakeel/Dual-state-Adaptive-controller-holoTutor-X`, not the original `shubhProcoder/RAG`.

---

## Team

- Tooba M. Shakeel
- Ayesha Aziz
- Sameen
- Maryam

## Licence and credit

Based on [shubhProcoder/RAG](https://github.com/shubhProcoder/RAG), released under the MIT licence.
