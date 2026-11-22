"""
Evaluation Mode (SRS 3.2.9) — run test questions through the full tutor and its
simpler versions, and compare the results.

Shared by the evaluator screen (``api/routes/evaluation.py``) and the command-line
script ``scripts/evaluate.py``, so both count results the same way.

Versions (SRS names):
    full         Full tutor: evidence (ECA) + learner state (LSE) -> action (DSC)
    baseline_a   Baseline A: normal RAG (retrieve and answer, no state awareness)
    baseline_b   Baseline B: learner state only (evidence is ignored)
    baseline_c   Baseline C: evidence confidence only (learner is always Uncertain)
    plain_llm    Optional extra: the LLM alone, no retrieval (not run by default)

The learner state of a question comes from the "learner_state" column of the CSV,
or is Uncertain when empty. No student data is used.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.models import ChatMessage
from app.core.tutor_models import EvidenceState
from app.services.chat_service import STATE_UNCERTAINTY_MESSAGE
from app.services.evidence_assessor import EvidenceAssessor

logger = logging.getLogger(__name__)

DEFAULT_VERSIONS = ["full", "baseline_a", "baseline_b", "baseline_c"]
ALL_VERSIONS = DEFAULT_VERSIONS + ["plain_llm"]
VERSION_LABELS = {
    "full": "Full tutor",
    "baseline_a": "Baseline A: normal RAG",
    "baseline_b": "Baseline B: learner state only",
    "baseline_c": "Baseline C: evidence confidence only",
    "plain_llm": "Plain LLM (no retrieval)",
}

LEARNER_STATES = ("understanding", "uncertain", "confused", "repeated_failure")
_STATE_ALIASES = {
    "repeatedly_failing": "repeated_failure",
    "repeated_failing": "repeated_failure",
    "repeatedly_failure": "repeated_failure",
    "understand": "understanding",
    "uncertain": "uncertain",
    "confuse": "confused",
}

RESULT_FIELDS = [
    "id", "version", "question", "answerable", "reference_page", "learner_state",
    "evidence_state", "evidence_score", "teaching_action", "outcome",
    "source_pages", "source_match", "response_time_s", "answer", "error",
    "manual_label",
]

# Used by the baselines that have no decision table.
RAG_PROMPT = (
    "Answer the question using only the passages below. "
    "Keep the answer under 150 words.\n\n"
    "Passages:\n{passages}\n\n"
    "Question: {question}"
)
PLAIN_PROMPT = "Answer the question. Keep the answer under 150 words.\n\nQuestion: {question}"

# A generated answer that says the material does not contain the answer counts as a refusal.
# This is a rough text check; the answers are saved so they can be checked by hand.
REFUSAL_RE = re.compile(
    r"(could not find|couldn't find|can't find|cannot find|"
    r"not (?:found|mentioned|covered|provided|included|available|stated|specified|discussed)"
    r" (?:in|by|within) (?:the |your )?(?:provided |given |study )?(?:passages?|material|context|documents?|text|lectures?)|"
    r"(?:does not|doesn't|do not|don't) (?:contain|mention|provide|cover|include|specify|discuss|say|have|list|state|explain|describe|define)|"
    r"no (?:information|mention|reference|details)|not enough information|"
    r"insufficient information|unable to (?:find|answer|provide)|"
    r"(?:cannot|can't) (?:answer|provide)|will not guess|"
    r"not in the (?:provided |given |study )?(?:passages|material|context|document|text|lecture))",
    re.IGNORECASE,
)


class QuestionFileError(ValueError):
    """The test question CSV is missing a column or has an invalid row."""


@dataclass
class Question:
    id: str
    question: str
    answerable: bool
    reference_page: int | None
    learner_state: str


@dataclass
class Pipeline:
    """The backend pieces the evaluation needs (built from the real app, or faked in tests)."""
    retriever: Any
    evidence: Any
    decision: Any
    prompt_builder: Any
    llm: Any
    top_k: int = 5
    document_ids: list[str] | None = None
    retry_waits: tuple[float, ...] = (15.0, 30.0, 60.0)


class RateLimitedLLM:
    """Keeps at least ``min_interval`` seconds between the starts of any two AI calls,
    including the evidence conflict checks, so a run stays within the free plan limits."""

    def __init__(self, llm: Any, min_interval: float) -> None:
        self._llm = llm
        self._interval = max(min_interval, 0.0)
        self._last = float("-inf")
        self._lock = asyncio.Lock()

    async def generate_stream(self, messages: list[Any], context: str = ""):
        async with self._lock:
            wait = self._last + self._interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        async for chunk in self._llm.generate_stream(messages, context=context):
            yield chunk


def pipeline_from_chat_service(
    chat: Any, document_ids: list[str] | None, min_interval: float, top_k: int = 5
) -> Pipeline:
    """Build the evaluation pipeline from the running app's chat service."""
    llm = RateLimitedLLM(chat._llm, min_interval)
    evidence = EvidenceAssessor(
        llm=llm,
        partial_limit=chat._evidence.partial_limit,
        strong_limit=chat._evidence.strong_limit,
    )
    return Pipeline(
        retriever=chat._retriever,
        evidence=evidence,
        decision=chat._decision,
        prompt_builder=chat._prompt_builder,
        llm=llm,
        top_k=top_k,
        document_ids=document_ids,
    )


# ──────────────────────────── questions ────────────────────────────

def normalise_learner_state(value: str | None) -> str:
    text = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not text:
        return "uncertain"
    text = _STATE_ALIASES.get(text, text)
    if text not in LEARNER_STATES:
        raise ValueError(f"learner_state '{value}' is not one of: {', '.join(LEARNER_STATES)}")
    return text


def parse_questions(text: str) -> list[Question]:
    """Read the test question CSV (columns id, question, answerable, and optionally
    reference_page and learner_state). A file with missing columns is rejected."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = {"id", "question", "answerable"} - set(reader.fieldnames or [])
    if missing:
        raise QuestionFileError(
            f"The question file is missing the column(s): {', '.join(sorted(missing))}"
        )
    questions: list[Question] = []
    for line_no, row in enumerate(reader, start=2):
        qid = (row.get("id") or "").strip()
        question = (row.get("question") or "").strip()
        if not qid or not question:
            raise QuestionFileError(f"Line {line_no}: id and question must not be empty.")
        answerable = (row.get("answerable") or "").strip().lower() in {"yes", "y", "true", "1"}
        ref = (row.get("reference_page") or "").strip()
        try:
            learner = normalise_learner_state(row.get("learner_state"))
        except ValueError as exc:
            raise QuestionFileError(f"Line {line_no}: {exc}") from None
        questions.append(Question(qid, question, answerable, int(ref) if ref.isdigit() else None, learner))
    if not questions:
        raise QuestionFileError("The question file has no questions.")
    return questions


def load_questions(path: Path) -> list[Question]:
    if not path.exists():
        raise QuestionFileError(f"Question file not found: {path}")
    return parse_questions(path.read_text(encoding="utf-8-sig"))


# ──────────────────────────── answering one question ────────────────────────────

def is_llm_error(text: str) -> bool:
    return "[LLM Error:" in text or text.startswith("Error: OPENAI_API_KEY")


def classify_outcome(action_value: str, answer: str) -> str:
    """refused | clarified | answered"""
    if action_value == "state_uncertainty":
        return "refused"
    if action_value == "clarify":
        return "clarified"
    return "refused" if REFUSAL_RE.search(answer or "") else "answered"


def format_passages(citations: list[Any]) -> str:
    return "\n\n".join(
        f"[{i}] (page {c.page_number}) {c.text}" for i, c in enumerate(citations, start=1)
    )


async def collect(llm: Any, messages: list[Any]) -> str:
    text = ""
    async for chunk in llm.generate_stream(messages, context=""):
        text += chunk
    return text


async def call_llm(p: Pipeline, messages: list[Any]) -> tuple[str, str]:
    """Returns (answer, error). Retries a few times (e.g. after a 503 or rate limit)."""
    attempt = 0
    while True:
        text = (await collect(p.llm, messages)).strip()
        if not is_llm_error(text):
            return text, ""
        if attempt >= len(p.retry_waits):
            return "", text
        await asyncio.sleep(p.retry_waits[attempt])
        attempt += 1


def _value(x: Any) -> str:
    return str(getattr(x, "value", x)) if x is not None else ""


async def answer_question(p: Pipeline, version: str, q: Question) -> dict[str, Any]:
    """Run one question through one version. Returns one result row."""
    started = time.perf_counter()
    conversation_id = f"eval-{version}-{q.id}"  # fresh, so the "Clarify twice" rule never carries over
    evidence_state = None
    evidence_score: Any = ""
    action = None
    cited: list[Any] = []
    answer = ""
    error = ""

    if version == "plain_llm":
        messages = [ChatMessage(role="user", content=PLAIN_PROMPT.format(question=q.question))]
        answer, error = await call_llm(p, messages)
    else:
        citations = await p.retriever.retrieve(
            query=q.question, top_k=p.top_k, document_ids=p.document_ids
        )
        ranked = sorted(citations, key=lambda c: c.score or 0.0, reverse=True)
        best = ranked[0].score if ranked else 0.0

        if version in ("full", "baseline_c"):
            evidence_state, best = await p.evidence.assess(q.question, citations)
        evidence_score = round(float(best or 0.0), 2)

        if version == "full":
            action = p.decision.decide(q.learner_state, evidence_state, conversation_id=conversation_id)
        elif version == "baseline_b":  # learner only: evidence is ignored (treated as Strong)
            action = p.decision.decide(q.learner_state, EvidenceState.STRONG, conversation_id=conversation_id)
        elif version == "baseline_c":  # evidence only: learner is always Uncertain
            action = p.decision.decide("uncertain", evidence_state, conversation_id=conversation_id)

        if _value(action) == "state_uncertainty":
            answer = STATE_UNCERTAINTY_MESSAGE  # fixed message, no LLM call (SRS 3.2.7)
        else:
            cited = ranked
            if action is None:  # baseline A: normal RAG
                messages = [ChatMessage(
                    role="user",
                    content=RAG_PROMPT.format(passages=format_passages(ranked), question=q.question),
                )]
            else:
                messages = p.prompt_builder.build_messages(
                    history=[], query=q.question, citations=ranked, action=action
                )
            answer, error = await call_llm(p, messages)

    elapsed = round(time.perf_counter() - started, 2)
    action_value = _value(action)
    outcome = "error" if error else classify_outcome(action_value, answer)

    pages = sorted({c.page_number for c in cited})  # kept for every row, so rows can be rescored
    if q.reference_page is not None and outcome == "answered":
        source_match = "yes" if q.reference_page in pages else "no"
    else:
        source_match = ""

    return {
        "id": q.id,
        "version": version,
        "question": q.question,
        "answerable": "yes" if q.answerable else "no",
        "reference_page": q.reference_page if q.reference_page is not None else "",
        "learner_state": q.learner_state,
        "evidence_state": _value(evidence_state),
        "evidence_score": evidence_score,
        "teaching_action": action_value,
        "outcome": outcome,
        "source_pages": " ".join(str(x) for x in pages),
        "source_match": source_match,
        "response_time_s": elapsed,
        "answer": answer.replace("\r", " ").strip(),
        "error": error,
        "manual_label": "",
    }


# ──────────────────────────── results file, resume, summary ────────────────────────────

def read_results(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def latest_by_key(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    """The last row of each (id, version) wins, so a retried question replaces its failed row."""
    return {(r["id"], r["version"]): r for r in rows}


def append_row(path: Path, row: dict[str, Any]) -> None:
    new_file = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        f.flush()


def _pct(num: int, den: int) -> str:
    return f"{100.0 * num / den:.0f}%" if den else "-"


def summarise(rows: list[dict[str, str]], question_ids: set[str], versions: list[str]) -> list[dict[str, Any]]:
    latest = latest_by_key(rows)
    summary = []
    for v in versions:
        mine = [r for (qid, ver), r in latest.items() if ver == v and qid in question_ids]
        if not mine:
            continue
        ok = [r for r in mine if r["outcome"] != "error"]
        for r in ok:
            # Re-check each saved answer with the current refusal rules
            r["outcome"] = classify_outcome(r["teaching_action"], r["answer"])
        ans_q = [r for r in ok if r["answerable"] == "yes"]
        unans_q = [r for r in ok if r["answerable"] == "no"]
        unsupported = sum(r["outcome"] == "answered" for r in unans_q)
        correct_ref = sum(r["outcome"] == "refused" for r in unans_q)
        wrong_ref = sum(r["outcome"] == "refused" for r in ans_q)
        answered_ans = [r for r in ans_q if r["outcome"] == "answered"]
        with_ref = [r for r in answered_ans if r["source_match"] in ("yes", "no")]
        src_ok = sum(r["source_match"] == "yes" for r in with_ref)
        times = [float(r["response_time_s"]) for r in ok if r["response_time_s"]]
        summary.append({
            "version": VERSION_LABELS.get(v, v),
            "questions": len(mine),
            "answerable": len(ans_q),
            "unanswerable": len(unans_q),
            "unsupported_answers": unsupported,
            "unsupported_rate": _pct(unsupported, len(unans_q)),
            "correct_refusals": correct_ref,
            "correct_refusal_rate": _pct(correct_ref, len(unans_q)),
            "wrong_refusals": wrong_ref,
            "wrong_refusal_rate": _pct(wrong_ref, len(ans_q)),
            "clarifying_questions": sum(r["outcome"] == "clarified" for r in ok),
            "source_page_matches": f"{src_ok}/{len(with_ref)}",
            "source_match_rate": _pct(src_ok, len(with_ref)),
            "avg_response_time_s": round(sum(times) / len(times), 2) if times else "",
            "errors": len(mine) - len(ok),
        })
    return summary


def write_summary(path: Path, summary: list[dict[str, Any]]) -> None:
    if not summary:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        writer.writeheader()
        writer.writerows(summary)


async def run(
    p: Pipeline,
    questions: list[Question],
    versions: list[str],
    out_path: Path,
    should_stop: Callable[[], bool] = lambda: False,
    on_progress: Callable[[int, int, str], None] = lambda done, total, line: None,
) -> None:
    """Run every question through every version, appending rows to ``out_path``.
    Rows already in the file without an error are skipped, so a stopped run continues."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = {key for key, row in latest_by_key(read_results(out_path)).items() if not row["error"]}
    total = len(questions) * len(versions)
    count = 0
    for q in questions:
        for v in versions:
            if should_stop():
                return
            count += 1
            if (q.id, v) in done:
                on_progress(count, total, f"{q.id} {v}: already done, skipped")
                continue
            row = await answer_question(p, v, q)
            append_row(out_path, row)
            line = f"{q.id} {v}: {row['outcome']} ({row['response_time_s']}s)"
            if row["error"]:
                line += f"  ERROR: {row['error'][:80]}"
            on_progress(count, total, line)


# ──────────────────────────── evaluator screen ────────────────────────────

class EvaluationBusyError(RuntimeError):
    """An evaluation run is already in progress."""


class EvaluationManager:
    """Keeps the evaluator's question file and results in ``work_dir`` and runs one
    evaluation at a time in the background, so the screen can poll its progress."""

    def __init__(
        self,
        work_dir: Path,
        get_chat_service: Callable[[], Any],
        get_metadata_store: Callable[[], Any],
        top_k: int = 5,
        pipeline_factory: Callable[..., Pipeline] = pipeline_from_chat_service,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.questions_path = self.work_dir / "test_questions.csv"
        self.results_path = self.work_dir / "results.csv"
        self.summary_path = self.work_dir / "results_summary.csv"
        self._get_chat = get_chat_service
        self._get_metadata = get_metadata_store
        self._top_k = top_k
        self._pipeline_factory = pipeline_factory
        self._task: asyncio.Task | None = None
        self._stop = False
        self._versions: list[str] = list(DEFAULT_VERSIONS)
        self._state: dict[str, Any] = {
            "status": "idle", "done": 0, "total": 0, "last": "", "error": "",
            "started_at": None, "finished_at": None, "documents": [],
        }

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── question file ──

    def save_questions(self, text: str) -> dict[str, Any]:
        if self.running:
            raise EvaluationBusyError("Stop the running evaluation before uploading new questions")
        questions = parse_questions(text)
        self.questions_path.write_text(text.lstrip("﻿"), encoding="utf-8")
        return self._question_info(questions)

    def question_info(self) -> dict[str, Any] | None:
        if not self.questions_path.exists():
            return None
        return self._question_info(load_questions(self.questions_path))

    @staticmethod
    def _question_info(questions: list[Question]) -> dict[str, Any]:
        answerable = sum(q.answerable for q in questions)
        return {
            "questions": len(questions),
            "answerable": answerable,
            "unanswerable": len(questions) - answerable,
        }

    # ── running ──

    async def start(
        self,
        versions: list[str],
        delay: float,
        document_ids: list[str] | None = None,
        fresh: bool = False,
        owner_id: int | None = None,
    ) -> dict[str, Any]:
        """Start a background run over ``owner_id``'s completed documents (all documents if None)."""
        if self.running:
            raise EvaluationBusyError("An evaluation is already running")
        bad = [v for v in versions if v not in ALL_VERSIONS]
        if bad or not versions:
            raise ValueError(f"Choose versions from: {', '.join(ALL_VERSIONS)}")
        if not self.questions_path.exists():
            raise ValueError("Upload the test question file first")
        questions = load_questions(self.questions_path)

        docs, _ = await self._get_metadata().list_documents(limit=500, offset=0, user_id=owner_id)
        ready = [d for d in docs if _value(d.status).lower() in ("completed", "ready")]
        if document_ids:
            ready = [d for d in ready if d.id in document_ids]
        if not ready and versions != ["plain_llm"]:
            raise ValueError("No completed document to test with. Upload a PDF first")

        if fresh:
            self.results_path.unlink(missing_ok=True)
            self.summary_path.unlink(missing_ok=True)

        pipeline = self._pipeline_factory(
            self._get_chat(), [d.id for d in ready], delay, top_k=self._top_k
        )
        self._versions = list(versions)
        self._stop = False
        self._state.update(
            status="running", done=0, total=len(questions) * len(versions), last="", error="",
            started_at=_now(), finished_at=None, documents=[d.original_name for d in ready],
        )
        self._task = asyncio.create_task(self._run(pipeline, questions, list(versions)))
        return self.status()

    async def _run(self, pipeline: Pipeline, questions: list[Question], versions: list[str]) -> None:
        def progress(done: int, total: int, line: str) -> None:
            self._state.update(done=done, total=total, last=line)

        try:
            await run(pipeline, questions, versions, self.results_path, lambda: self._stop, progress)
            self._state["status"] = "stopped" if self._stop else "finished"
        except Exception as exc:  # shown on the screen; the run can be continued
            logger.exception("Evaluation run failed")
            self._state.update(status="failed", error=str(exc))
        finally:
            self._state["finished_at"] = _now()
            write_summary(self.summary_path, self.summary(versions))

    def stop(self) -> dict[str, Any]:
        """Stops after the question in progress; running again continues where it stopped."""
        if self.running:
            self._stop = True
            self._state["status"] = "stopping"
        return self.status()

    def status(self) -> dict[str, Any]:
        return {**self._state, "versions": self._versions}

    def summary(self, versions: list[str] | None = None) -> list[dict[str, Any]]:
        if not self.questions_path.exists():
            return []
        ids = {q.id for q in load_questions(self.questions_path)}
        return summarise(read_results(self.results_path), ids, versions or self._versions)

    def export_summary(self) -> Path:
        """Write the current side-by-side summary to its CSV file and return the path."""
        write_summary(self.summary_path, self.summary())
        return self.summary_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
