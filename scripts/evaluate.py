#!/usr/bin/env python
"""
Evaluation Mode (SRS 3.2.9): run test questions through the full tutor and
its simpler versions, and compare the results.

The evaluation logic lives in backend/app/services/evaluation.py and is shared
with the evaluator screen; this script runs it from the command line.

Versions (SRS names):
    full         Full tutor: evidence (ECA) + learner state (LSE) -> action (DSC)
    baseline_a   Baseline A: normal RAG (retrieve and answer, no state awareness)
    baseline_b   Baseline B: learner state only (evidence is ignored)
    baseline_c   Baseline C: evidence confidence only (learner is always Uncertain)
    plain_llm    Optional extra: the LLM alone, no retrieval (not run by default)

Run it from the project folder, with the web server STOPPED:

    cd backend
    python ..\\scripts\\evaluate.py

Quick test with 3 questions:   python ..\\scripts\\evaluate.py --limit 3
Only some versions:            python ..\\scripts\\evaluate.py --versions full,baseline_a
A stopped run is continued by running the same command again.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation import (  # noqa: E402
    ALL_VERSIONS,
    DEFAULT_VERSIONS,
    QuestionFileError,
    load_questions,
    pipeline_from_chat_service,
    read_results,
    run,
    summarise,
    write_summary,
)


def print_summary(summary: list[dict[str, Any]]) -> None:
    if not summary:
        print("No results yet.")
        return
    print("\n=== Results (side by side) ===")
    cols = [
        ("version", "Version"),
        ("unsupported_answers", "Unsupported"),
        ("correct_refusals", "Correct refusals"),
        ("wrong_refusals", "Wrong refusals"),
        ("clarifying_questions", "Clarify"),
        ("source_page_matches", "Source page"),
        ("avg_response_time_s", "Avg time (s)"),
        ("errors", "Errors"),
    ]
    widths = [max(len(h), *(len(str(r[k])) for r in summary)) for k, h in cols]
    print("  ".join(h.ljust(w) for (k, h), w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in summary:
        print("  ".join(str(r[k]).ljust(w) for (k, h), w in zip(cols, widths)))
    print(
        "\nUnsupported = answered a question the material cannot answer (lower is better).\n"
        "Correct refusals = refused an unanswerable question (higher is better).\n"
        "Wrong refusals = refused a question the material can answer (lower is better).\n"
        "Source page = answered questions whose reference page was among the sources."
    )


async def build_pipeline(document_name: str | None, delay: float):
    os.chdir(BACKEND_DIR)  # so that backend/.env and backend/data are found
    logging.basicConfig(level=logging.WARNING)

    from app import dependencies
    from app.config import settings

    print("Starting the backend components (loading the embedding model can take a minute)...")
    await dependencies.startup()

    metadata = dependencies.get_metadata_store()
    docs, _ = await metadata.list_documents(limit=200, offset=0)
    ready = [d for d in docs if str(getattr(d.status, "value", d.status)).lower() in ("completed", "ready")]
    if document_name:
        ready = [d for d in ready if document_name.lower() in d.original_name.lower()]
    if not ready:
        raise SystemExit(
            "No completed document found. Upload your PDF in the app first "
            "(Documents tab, status 'completed')."
        )
    print("Documents used: " + ", ".join(d.original_name for d in ready))

    return pipeline_from_chat_service(
        dependencies.get_chat_service(),
        [d.id for d in ready],
        min_interval=delay,
        top_k=settings.TOP_K_RETRIEVAL,
    )


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="HoloTutor-X evaluation mode (SRS 3.2.9)")
    ap.add_argument("--questions", type=Path, default=SCRIPT_DIR / "test_questions.csv",
                    help="CSV with columns id, question, answerable, reference_page, learner_state")
    ap.add_argument("--versions", default=",".join(DEFAULT_VERSIONS),
                    help="comma list from: " + ", ".join(ALL_VERSIONS))
    ap.add_argument("--document", default=None,
                    help="use only documents whose name contains this text (default: all completed)")
    ap.add_argument("--out", type=Path, default=SCRIPT_DIR / "results" / "results.csv")
    ap.add_argument("--delay", type=float, default=5.0,
                    help="minimum seconds between any two AI calls (free plan limits)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N questions (quick test)")
    args = ap.parse_args()
    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    bad = [v for v in versions if v not in ALL_VERSIONS]
    if bad:
        ap.error(f"unknown version(s): {', '.join(bad)}. Choose from: {', '.join(ALL_VERSIONS)}")
    args.versions = versions
    return args


def main() -> None:
    args = parse_args()
    try:
        questions = load_questions(args.questions.resolve())
    except QuestionFileError as exc:
        raise SystemExit(str(exc))
    if args.limit:
        questions = questions[: args.limit]
    out_path = args.out.resolve()

    async def go() -> None:
        pipeline = await build_pipeline(args.document, args.delay)
        await run(pipeline, questions, args.versions, out_path,
                  on_progress=lambda done, total, line: print(f"[{done}/{total}] {line}"))

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to continue where it stopped.")

    summary = summarise(read_results(out_path), {q.id for q in questions}, args.versions)
    summary_path = out_path.with_name("results_summary.csv")
    write_summary(summary_path, summary)
    print_summary(summary)
    print(f"\nPer-question results: {out_path}\nSummary:              {summary_path}")


if __name__ == "__main__":
    main()
