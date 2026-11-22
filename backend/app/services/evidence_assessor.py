"""
Evidence Confidence Assessment (ECA) — rates how well the retrieved passages
support an answer before the answer is written (SRS 3.2.5).

Rules, checked in this order:
1. Insufficient: no passage, or the best score is below PARTIAL_LIMIT.
2. Conflicting: two or more passages score above PARTIAL_LIMIT and the LLM
   says they contradict each other.
3. Strong: the best score is STRONG_LIMIT or more and at least two passages
   score above PARTIAL_LIMIT.
4. Partial: every other case.
"""

from __future__ import annotations

import logging

from app.core.interfaces import LLMProvider
from app.core.models import ChatMessage, Citation
from app.core.tutor_models import EvidenceState

logger = logging.getLogger(__name__)


class EvidenceAssessor:
    """Rates retrieved evidence as Strong, Partial, Conflicting or Insufficient."""

    CONFLICT_PROMPT = """You check study material for contradictions.
Below are passages retrieved for a student's question.
Do any two of these passages contradict each other about the question?
Reply with exactly one word: YES or NO."""

    def __init__(
        self,
        llm: LLMProvider | None,
        partial_limit: float = 0.50,
        strong_limit: float = 0.75,
    ) -> None:
        self._llm = llm
        self.set_limits(partial_limit, strong_limit)

    @staticmethod
    def check_limits(partial_limit: float, strong_limit: float) -> None:
        if not 0.0 <= partial_limit <= strong_limit <= 1.0:
            raise ValueError("Limits must satisfy 0 <= partial_limit <= strong_limit <= 1")

    def set_limits(self, partial_limit: float, strong_limit: float) -> None:
        """Change the limits while the app runs (Tutor Settings screen)."""
        self.check_limits(partial_limit, strong_limit)
        self._partial_limit = float(partial_limit)
        self._strong_limit = float(strong_limit)

    @property
    def partial_limit(self) -> float:
        return self._partial_limit

    @property
    def strong_limit(self) -> float:
        return self._strong_limit

    async def assess(
        self, question: str, citations: list[Citation]
    ) -> tuple[EvidenceState, float]:
        """Return the evidence state and the best similarity score (0 to 1)."""
        best = max((self._score(c) for c in citations), default=0.0)

        if not citations or best < self._partial_limit:
            return EvidenceState.INSUFFICIENT, best

        relevant = [c for c in citations if self._score(c) > self._partial_limit]

        if len(relevant) >= 2 and await self._is_conflicting(question, relevant):
            return EvidenceState.CONFLICTING, best

        if best >= self._strong_limit and len(relevant) >= 2:
            return EvidenceState.STRONG, best

        return EvidenceState.PARTIAL, best

    @staticmethod
    def _score(citation: Citation) -> float:
        """Similarity score clamped to 0..1 (cosine similarity can dip below 0)."""
        return min(max(citation.score or 0.0, 0.0), 1.0)

    async def _is_conflicting(self, question: str, passages: list[Citation]) -> bool:
        """Ask the LLM whether the passages contradict each other."""
        if self._llm is None:
            return False

        blocks = [
            f"[Passage {i}: {c.document_name}, Page {c.page_number}]\n{c.text}"
            for i, c in enumerate(passages, 1)
        ]
        messages = [
            ChatMessage(role="system", content=self.CONFLICT_PROMPT),
            ChatMessage(
                role="user",
                content=f"Question: {question}\n\n" + "\n\n".join(blocks),
            ),
        ]

        reply = ""
        try:
            async for part in self._llm.generate_stream(messages, context=""):
                reply += part
        except Exception:
            logger.exception("Conflict check failed; treating evidence as not conflicting")
            return False

        return reply.strip().upper().startswith("YES")
