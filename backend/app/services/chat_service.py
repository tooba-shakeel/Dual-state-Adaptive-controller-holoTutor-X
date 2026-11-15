"""
Chat Service — Orchestrates Retrieval, the Dual-State Controller, Prompt Building, and LLM Generation.

Pipeline per question:
retrieve -> rate evidence (ECA) -> estimate learner state (LSE)
-> choose teaching action (DSC) -> prompt with that action -> LLM.
"""

from __future__ import annotations

import inspect
import logging
import re
import typing

from app.core.interfaces import ConversationStore, LLMProvider
from app.core.models import ChatMessage, ChatRequest, Citation
from app.core.tutor_models import EvidenceState, TeachingAction, TutorState
from app.services.decision_controller import DecisionController
from app.services.evidence_assessor import EvidenceAssessor
from app.services.prompt_builder import PromptBuilder
from app.services.retriever_service import RetrieverService

logger = logging.getLogger(__name__)

# Shown for State uncertainty; the LLM is not called (SRS 3.2.7).
STATE_UNCERTAINTY_MESSAGE = (
    "I could not find a reliable answer to this in your study material, so I will not guess. "
    "Try asking in a different way, or upload material that covers this topic."
)

# Pages of a document that form one topic (SRS Appendix A.1, TOPIC BLOCK).
TOPIC_BLOCK_PAGES = 5

# A short message with one of these words ("explain again", "give an example of it")
# refers back to the previous question, so retrieval searches with that question.
FOLLOW_UP_WORDS = {
    "again", "this", "that", "it", "its", "they", "them", "these", "those",
    "more", "above", "same", "previous", "earlier",
}
FOLLOW_UP_MAX_WORDS = 8


class LearnerStateEstimator(typing.Protocol):
    """What ChatService needs from the LSE module (sync or async methods both work)."""

    def estimate(self, conversation_id: str, topic: str | None = None) -> typing.Any: ...


class ChatService:
    """Orchestrates the RAG pipeline for chat interactions."""

    def __init__(
        self,
        retriever: RetrieverService,
        prompt_builder: PromptBuilder,
        llm: LLMProvider,
        conversation_store: ConversationStore,
        evidence_assessor: EvidenceAssessor,
        learner_estimator: LearnerStateEstimator,
        decision_controller: DecisionController,
        top_k: int = 5,
        interaction_log: typing.Any = None,
    ) -> None:
        self._retriever = retriever
        self._prompt_builder = prompt_builder
        self._llm = llm
        self._conversation_store = conversation_store
        self._evidence = evidence_assessor
        self._learner = learner_estimator
        self._decision = decision_controller
        self._top_k = top_k
        self._log = interaction_log  # LOG module; None = do not log (tests, scripts)

    async def chat_stream(
        self,
        request: ChatRequest,
        user_id: int | None = None,
        allowed_document_ids: list[str] | None = None,
    ) -> typing.AsyncGenerator[tuple[str, list[Citation], dict[str, typing.Any] | None], None]:
        """
        Executes the pipeline and yields ``(text, citations, meta)``.
        The very first yield carries the citations and ``meta`` = conversation id + Tutor State,
        so the frontend can show the sources and the Tutor State panel.
        Subsequent yields only contain text chunks.

        ``user_id`` owns any new conversation. ``allowed_document_ids`` limits the search
        to the student's own documents (None = no limit, used by tests and scripts).
        """
        # 1. Retrieve or create conversation
        conv = None
        if request.conversation_id:
            conv = await self._conversation_store.get_conversation(request.conversation_id)
            if conv is not None and user_id is not None and conv.user_id != user_id:
                conv = None  # never continue another user's conversation
        if conv is None:
            conv = await self._conversation_store.create_conversation(user_id=user_id)

        history = conv.messages[-10:] if conv else [] # Keep last 10 messages for context

        # 2. Retrieve context (a follow-up is searched with the question it refers to)
        search_query = self._search_query(request.message, history)
        document_ids = request.document_ids
        if allowed_document_ids is not None:
            document_ids = [d for d in (document_ids or allowed_document_ids) if d in allowed_document_ids]
        if allowed_document_ids is not None and not document_ids:
            citations = []  # the student has no ready document: nothing to search
        else:
            citations = await self._retriever.retrieve(
                query=search_query,
                top_k=self._top_k,
                document_ids=document_ids,
            )

        # 3. Dual-State decision: evidence (ECA) + learner (LSE) -> action (DSC)
        evidence_state, evidence_score = await self._evidence.assess(search_query, citations)
        topic = self._topic_of(citations)
        learner_state = await self._estimate_learner(conv.id, topic)
        action = self._decision.decide(learner_state, evidence_state, conversation_id=conv.id)

        tutor_state = TutorState(
            learner_state=learner_state,
            evidence_state=evidence_state,
            evidence_score=round(evidence_score, 2),
            teaching_action=action,
            table_version=self._decision.version,
        )
        meta = {"conversation_id": conv.id, "topic": topic, "tutor_state": tutor_state}
        logger.info(
            "Tutor state: learner=%s evidence=%s (%.2f) action=%s",
            learner_state, evidence_state.value, evidence_score, action.value,
        )

        # The limits only rate the evidence; the LLM gets every retrieved passage (SRS 3.2.7),
        # best first, so a relevant page just under the limit is not lost.
        usable = [] if evidence_state is EvidenceState.INSUFFICIENT else sorted(
            citations, key=lambda c: c.score or 0.0, reverse=True
        )

        # Save user message
        user_msg = ChatMessage(role="user", content=request.message)
        await self._conversation_store.add_message(conv.id, user_msg)

        # 4a. State uncertainty: fixed message, no LLM call
        if action is TeachingAction.STATE_UNCERTAINTY:
            yield (STATE_UNCERTAINTY_MESSAGE, usable, meta)
            await self._conversation_store.add_message(
                conv.id, ChatMessage(role="assistant", content=STATE_UNCERTAINTY_MESSAGE)
            )
            await self._record(user_id, conv.id, request.message, topic, tutor_state, "")
            return

        # 4b. Build prompt with the chosen teaching action
        messages = self._prompt_builder.build_messages(
            history=history,
            query=request.message,
            citations=usable,
            action=action,
        )

        # 5. Stream response
        full_response = ""
        first_chunk = True

        async for chunk in self._llm.generate_stream(messages, context=""):
            full_response += chunk
            if first_chunk:
                yield (chunk, usable, meta)
                first_chunk = False
            else:
                yield (chunk, [], None)

        if first_chunk:
            # If no chunks were yielded at all (e.g., error)
            yield ("", usable, meta)

        # Save assistant message
        assistant_msg = ChatMessage(role="assistant", content=full_response)
        await self._conversation_store.add_message(conv.id, assistant_msg)
        await self._record(user_id, conv.id, request.message, topic, tutor_state, full_response)

    async def _record(
        self,
        user_id: int | None,
        conversation_id: str,
        question: str,
        topic: str | None,
        tutor_state: TutorState,
        answer: str,
    ) -> None:
        """Write one interaction-log row. A logging failure never breaks the student's answer."""
        if self._log is None:
            return
        try:
            await self._log.add(
                user_id=user_id,
                conversation_id=conversation_id,
                question=question,
                topic=topic,
                learner_state=tutor_state.learner_state,
                evidence_state=tutor_state.evidence_state.value,
                evidence_score=tutor_state.evidence_score,
                teaching_action=tutor_state.teaching_action.value,
                table_version=tutor_state.table_version,
                # Unanswered = the tutor declined (State uncertainty) or the LLM returned nothing
                answered=tutor_state.teaching_action is not TeachingAction.STATE_UNCERTAINTY
                and bool(answer.strip()),
                answer_chars=len(answer),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not write the interaction log")

    async def _estimate_learner(self, conversation_id: str, topic: str | None) -> str:
        """Ask the LSE module for the learner state, accepting sync or async estimators."""
        result = self._learner.estimate(conversation_id, topic)
        if inspect.isawaitable(result):
            result = await result
        return str(getattr(result, "value", result))

    @staticmethod
    def _is_follow_up(message: str) -> bool:
        words = re.findall(r"[a-z']+", message.lower())
        return 0 < len(words) <= FOLLOW_UP_MAX_WORDS and any(w in FOLLOW_UP_WORDS for w in words)

    @classmethod
    def _search_query(cls, message: str, history: list[ChatMessage]) -> str:
        """A follow-up is searched with the latest earlier question that is not itself a follow-up.

        Words such as "explain again" carry no topic and only lower the similarity
        scores; the LLM still sees the whole conversation through the history.
        """
        if not cls._is_follow_up(message):
            return message
        for msg in reversed(history):
            if msg.role == "user" and not cls._is_follow_up(msg.content):
                return msg.content
        return message

    @staticmethod
    def _topic_of(citations: list[Citation]) -> str | None:
        """Topic = document + 5-page block of the best matching passage (SRS 3.2.4)."""
        if not citations:
            return None
        best = max(citations, key=lambda c: c.score or 0.0)
        block = max(best.page_number - 1, 0) // TOPIC_BLOCK_PAGES
        return f"{best.document_id}:{block}"
