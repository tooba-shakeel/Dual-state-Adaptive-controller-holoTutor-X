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

# The tutor ends some answers with "Check question: ..." (SRS 3.2.4). The student's next
# message is read as the answer to it, unless it is a clear new question: it ends with
# "?" or starts with one of these words. A clear new question is also searched on its
# own. Words such as "when" or "because" are left out on purpose: answers to
# "When is ...?" or "Why ...?" often start with them.
CHECK_QUESTION_MARK = "Check question:"
QUESTION_STARTS = {"what", "explain", "define", "describe", "tell", "give", "list", "compare", "show"}


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
        self._topics: dict[str, str] = {}  # conversation id -> last question that was searched on its own

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

        # 2. Retrieve context.
        # A reply such as "general definition", "because the customer is unsure" or
        # "explain again" names no topic on its own. Unless the message is a clear new
        # question, it is searched both alone and together with the conversation's topic,
        # and the search with the better similarity score is used.
        all_messages = conv.messages if conv else []
        check_question = self._open_check_question(request.message, all_messages)
        clarify_question = (
            None if check_question is not None
            else self._open_clarify_question(request.message, all_messages)
        )
        earlier_question = self._topics.get(conv.id) or self._previous_question(all_messages)

        candidates = [request.message]
        standalone = self._looks_like_new_question(request.message) and not self._is_follow_up(request.message)
        if earlier_question and not standalone and earlier_question != request.message:
            candidates += [f"{earlier_question} {request.message}", earlier_question]

        document_ids = request.document_ids
        if allowed_document_ids is not None:
            document_ids = [d for d in (document_ids or allowed_document_ids) if d in allowed_document_ids]
        search_query, citations, best = request.message, [], 0.0
        if allowed_document_ids is None or document_ids:  # else: no ready document, nothing to search
            best = -1.0
            for query in candidates:
                found = await self._retriever.retrieve(query=query, top_k=self._top_k, document_ids=document_ids)
                score = max((c.score or 0.0 for c in found), default=0.0)
                if score > best:  # ties keep the earlier candidate: the message alone first
                    search_query, citations, best = query, found, score
        # The topic stays with the conversation until a message wins on its own AND finds
        # material: a question the PDFs cannot answer ("why no check question?") is not a topic.
        if search_query == request.message and best >= self._evidence.partial_limit:
            self._topics[conv.id] = request.message
        logger.info("Search query: %r (from %d candidates)", search_query, len(candidates))

        # 3. Dual-State decision: evidence (ECA) + learner (LSE) -> action (DSC)
        evidence_state, evidence_score = await self._evidence.assess(search_query, citations)
        if clarify_question is not None and evidence_state is EvidenceState.CONFLICTING:
            # The student has already said what they want; asking again would loop
            evidence_state = EvidenceState.PARTIAL
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
        query = request.message
        if check_question is not None:
            query = (
                f'I am answering your check question "{check_question}".\n'
                f"My answer: {request.message}\n"
                "Tell me whether my answer is correct, partly correct or wrong, "
                "explain why from the passages, and give the correct answer if I missed something."
            )
        elif clarify_question is not None:
            query = (
                f'You asked me: "{clarify_question}"\n'
                f"My reply: {request.message}\n"
                f'Now answer my earlier question "{earlier_question or ""}" in the way I asked for.'
            )
        messages = self._prompt_builder.build_messages(
            history=history,
            query=query,
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
    def _previous_question(cls, history: list[ChatMessage]) -> str | None:
        """The latest earlier user message that is a clear question of its own.

        Used when the conversation's topic is not in memory (e.g. after a restart).
        """
        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            if msg.role != "user" or not cls._looks_like_new_question(msg.content) \
                    or cls._is_follow_up(msg.content):
                continue
            reply = history[i + 1] if i + 1 < len(history) else None
            if reply is not None and reply.content.strip() == STATE_UNCERTAINTY_MESSAGE:
                continue  # nothing was found for it, so it is not the topic
            return msg.content
        return None

    @staticmethod
    def _last_tutor_reply(history: list[ChatMessage]) -> ChatMessage | None:
        """The tutor's last real reply (a "could not find" reply is skipped)."""
        return next(
            (m for m in reversed(history)
             if m.role == "assistant" and m.content.strip() != STATE_UNCERTAINTY_MESSAGE),
            None,
        )

    @staticmethod
    def _looks_like_new_question(message: str) -> bool:
        text = message.strip()
        words = re.findall(r"[a-z']+", text.lower())
        return not words or text.endswith("?") or words[0] in QUESTION_STARTS

    @classmethod
    def _open_clarify_question(cls, message: str, history: list[ChatMessage]) -> str | None:
        """The Clarify question the student is replying to, or None.

        Clarify answers with one question and no check question, so a tutor reply that
        ends with "?" and has no "Check question:" is a Clarify question.
        """
        last_reply = cls._last_tutor_reply(history)
        if last_reply is None or CHECK_QUESTION_MARK in last_reply.content:
            return None
        if not last_reply.content.strip().endswith("?") or cls._looks_like_new_question(message):
            return None
        return last_reply.content.strip()

    @classmethod
    def _open_check_question(cls, message: str, history: list[ChatMessage]) -> str | None:
        """The check question the student is answering, or None.

        It is open when the tutor's last reply ends with "Check question: ..." and the
        new message does not look like a new question. A "could not find" reply is
        skipped, so the student can still answer after it.
        """
        last_reply = cls._last_tutor_reply(history)
        if last_reply is None or CHECK_QUESTION_MARK not in last_reply.content:
            return None
        if cls._looks_like_new_question(message):
            return None
        question = last_reply.content.rsplit(CHECK_QUESTION_MARK, 1)[1].strip()
        return question.strip("*_ ").strip() or None

    @staticmethod
    def _topic_of(citations: list[Citation]) -> str | None:
        """Topic = document + 5-page block of the best matching passage (SRS 3.2.4)."""
        if not citations:
            return None
        best = max(citations, key=lambda c: c.score or 0.0)
        block = max(best.page_number - 1, 0) // TOPIC_BLOCK_PAGES
        return f"{best.document_id}:{block}"
