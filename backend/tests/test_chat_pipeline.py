import pytest
from unittest.mock import AsyncMock

from app.core.models import ChatMessage, ChatRequest, Citation, Conversation
from app.core.tutor_models import EvidenceState, TeachingAction
from app.services.chat_service import STATE_UNCERTAINTY_MESSAGE, ChatService
from app.services.decision_controller import DecisionController
from app.services.evidence_assessor import EvidenceAssessor
from app.services.learner_state_stub import FixedLearnerStateEstimator
from app.services.prompt_builder import PromptBuilder


def cite(score, page=1, text="passage"):
    return Citation(document_id="doc1", document_name="notes.pdf", page_number=page, text=text, score=score)


@pytest.fixture
def mock_conversation_store():
    store = AsyncMock()
    store.create_conversation.return_value = Conversation(id="conv1", title="New Conv")
    store.get_conversation.return_value = None
    store.add_message = AsyncMock()
    return store


class MockLLMProvider:
    """Stub AI service: records every prompt, answers NO to conflict checks."""

    def __init__(self):
        self.calls = []

    async def generate_stream(self, messages, context):
        self.calls.append(messages)
        if "contradict" in messages[0].content:
            yield "NO"
        else:
            yield "Here is the answer."


class FixedEstimator:
    def __init__(self, state):
        self.state = state

    def estimate(self, conversation_id, topic=None):
        return self.state


def make_service(citations, store, llm, learner=None):
    retriever = AsyncMock()
    retriever.retrieve.return_value = citations
    return ChatService(
        retriever=retriever,
        prompt_builder=PromptBuilder(),
        llm=llm,
        conversation_store=store,
        evidence_assessor=EvidenceAssessor(llm=llm),
        learner_estimator=learner or FixedLearnerStateEstimator(),
        decision_controller=DecisionController(),
    )


async def run(service, message="What is a use case?"):
    events = [event async for event in service.chat_stream(ChatRequest(message=message))]
    text = "".join(chunk for chunk, _, _ in events)
    citations, meta = events[0][1], events[0][2]
    return text, citations, meta


@pytest.mark.asyncio
async def test_no_evidence_states_uncertainty_without_calling_llm(mock_conversation_store):
    llm = MockLLMProvider()
    text, citations, meta = await run(make_service([], mock_conversation_store, llm))

    assert text == STATE_UNCERTAINTY_MESSAGE
    assert citations == []
    assert meta["conversation_id"] == "conv1"
    assert meta["tutor_state"].evidence_state is EvidenceState.INSUFFICIENT
    assert meta["tutor_state"].teaching_action is TeachingAction.STATE_UNCERTAINTY
    assert llm.calls == []


@pytest.mark.asyncio
async def test_weak_evidence_is_insufficient(mock_conversation_store):
    llm = MockLLMProvider()
    text, citations, meta = await run(make_service([cite(0.45), cite(0.30)], mock_conversation_store, llm))

    assert text == STATE_UNCERTAINTY_MESSAGE
    assert meta["tutor_state"].evidence_score == 0.45
    assert llm.calls == []


@pytest.mark.asyncio
async def test_strong_evidence_uncertain_learner_gets_worked_example(mock_conversation_store):
    llm = MockLLMProvider()
    service = make_service([cite(0.82, page=3), cite(0.64, page=4), cite(0.20)], mock_conversation_store, llm)
    text, citations, meta = await run(service)

    state = meta["tutor_state"]
    assert text == "Here is the answer."
    assert state.learner_state == "uncertain"
    assert state.evidence_state is EvidenceState.STRONG
    assert state.teaching_action is TeachingAction.WORKED_EXAMPLE
    assert [c.score for c in citations] == [0.82, 0.64, 0.20]  # every passage reaches the LLM, best first

    answer_prompt = llm.calls[-1][0].content
    assert "Worked example" in answer_prompt
    assert "Check question:" in answer_prompt


def conversation_with(*messages):
    """messages alternate: user, assistant, user, assistant ..."""
    roles = ["user", "assistant"]
    return Conversation(id="conv1", title="Conv", messages=[
        ChatMessage(role=roles[i % 2], content=m) for i, m in enumerate(messages)
    ])


def topic_retriever(service, topic):
    """Fake search: a query that mentions the topic finds good passages (0.66 and 0.63),
    any other query finds only weak ones (0.30). Records every query."""
    service.queries = []

    async def retrieve(query, top_k, document_ids):
        service.queries.append(query)
        if topic in query.lower():
            return [cite(0.66, text="topic passage"), cite(0.63)]
        return [cite(0.30, text="weak passage")]

    service._retriever.retrieve.side_effect = retrieve
    return service


async def run_in(service, message, conversation_id="conv1"):
    request = ChatRequest(message=message, conversation_id=conversation_id)
    events = [event async for event in service.chat_stream(request)]
    return "".join(c for c, _, _ in events), events[0][1], events[0][2]


def make_topic_service(store, conv, topic="waterfall", llm=None):
    store.get_conversation.return_value = conv
    llm = llm or MockLLMProvider()
    return topic_retriever(make_service([], store, llm), topic), llm


WATERFALL_CHECK = (
    "What is the waterfall model?",
    "The waterfall model has fixed stages.\n**Check question:** When can we use it?",
)


@pytest.mark.asyncio
async def test_confused_learner_gets_simplify(mock_conversation_store):
    llm = MockLLMProvider()
    service = make_service([cite(0.60)], mock_conversation_store, llm, learner=FixedEstimator("confused"))
    _, _, meta = await run(service)

    assert meta["tutor_state"].evidence_state is EvidenceState.PARTIAL
    assert meta["tutor_state"].teaching_action is TeachingAction.SIMPLIFY


# ── replies that name no topic are searched with the conversation's topic ──

@pytest.mark.parametrize("reply", [
    "explain again",
    "No, we cannot use it as requirements are not clearly specified",
    "when we have to follow sequence",
    "because customer does not know about the whole project",
    "general definition",
    "ok",
])
@pytest.mark.asyncio
async def test_any_reply_keeps_the_topic(mock_conversation_store, reply):
    service, _ = make_topic_service(mock_conversation_store, conversation_with(*WATERFALL_CHECK))
    text, citations, meta = await run_in(service, reply)

    assert meta["tutor_state"].evidence_state is EvidenceState.STRONG or \
        meta["tutor_state"].evidence_state is EvidenceState.PARTIAL
    assert text == "Here is the answer."  # never "could not find"
    assert citations[0].text == "topic passage"


@pytest.mark.parametrize("question", ["What is the spiral model?", "explain the spiral model"])
@pytest.mark.asyncio
async def test_clear_new_question_is_searched_on_its_own(mock_conversation_store, question):
    service, llm = make_topic_service(mock_conversation_store, conversation_with(*WATERFALL_CHECK), topic="spiral")
    await run_in(service, question)

    assert service.queries == [question]
    assert "check question" not in llm.calls[-1][-1].content


@pytest.mark.asyncio
async def test_off_topic_question_is_not_mixed_with_the_old_topic(mock_conversation_store):
    service, llm = make_topic_service(mock_conversation_store, conversation_with(*WATERFALL_CHECK))
    text, _, meta = await run_in(service, "What is the capital of France?")

    assert service.queries == ["What is the capital of France?"]
    assert text == STATE_UNCERTAINTY_MESSAGE


@pytest.mark.asyncio
async def test_reply_that_names_its_own_topic_wins_on_its_own(mock_conversation_store):
    service, _ = make_topic_service(mock_conversation_store, conversation_with(*WATERFALL_CHECK), topic="spiral")
    _, citations, _ = await run_in(service, "the spiral model is better here")

    assert citations[0].text == "topic passage"


@pytest.mark.asyncio
async def test_topic_is_found_after_many_replies(mock_conversation_store):
    messages = list(WATERFALL_CHECK)
    for i in range(6):  # six answers push the first question out of the last 10 messages
        messages += [f"because of reason {i}", f"Good.\nCheck question: Why does point {i} matter?"]
    service, _ = make_topic_service(mock_conversation_store, conversation_with(*messages))
    text, citations, _ = await run_in(service, "because customer does not know the whole project")

    assert text == "Here is the answer."
    assert citations[0].text == "topic passage"


# ── how the tutor is asked to respond ──

@pytest.mark.asyncio
async def test_answer_to_check_question_is_marked_for_the_llm(mock_conversation_store):
    service, llm = make_topic_service(mock_conversation_store, conversation_with(*WATERFALL_CHECK))
    await run_in(service, "when we have to follow sequence")

    student_turn = llm.calls[-1][-1].content
    assert 'check question "When can we use it?"' in student_turn
    assert "My answer: when we have to follow sequence" in student_turn


@pytest.mark.asyncio
async def test_answer_after_a_could_not_find_reply_still_answers_the_check_question(mock_conversation_store):
    conv = conversation_with(*WATERFALL_CHECK, "no idea really", STATE_UNCERTAINTY_MESSAGE)
    service, llm = make_topic_service(mock_conversation_store, conv)
    text, _, _ = await run_in(service, "only when requirements are clear")

    assert text == "Here is the answer."
    assert 'check question "When can we use it?"' in llm.calls[-1][-1].content


class ConflictLLM(MockLLMProvider):
    """Says the passages contradict each other, so the evidence is Conflicting."""

    async def generate_stream(self, messages, context):
        self.calls.append(messages)
        yield "YES" if "contradict" in messages[0].content else "Here is the answer."


EVOLUTIONARY_CLARIFY = (
    "now explain what is evolutionary model?",
    "To clarify, are you asking for a general definition of the Evolutionary Process Model, "
    "or how it differs from the Waterfall Model?",
)


@pytest.mark.asyncio
async def test_reply_to_clarify_is_answered(mock_conversation_store):
    service, llm = make_topic_service(mock_conversation_store, conversation_with(*EVOLUTIONARY_CLARIFY),
                                      topic="evolutionary")
    text, citations, _ = await run_in(service, "general definition")

    assert text == "Here is the answer."
    assert citations[0].text == "topic passage"
    student_turn = llm.calls[-1][-1].content
    assert "My reply: general definition" in student_turn
    assert 'earlier question "now explain what is evolutionary model?"' in student_turn


@pytest.mark.asyncio
async def test_reply_to_clarify_does_not_clarify_again(mock_conversation_store):
    service, _ = make_topic_service(mock_conversation_store, conversation_with(*EVOLUTIONARY_CLARIFY),
                                    topic="evolutionary", llm=ConflictLLM())
    _, _, meta = await run_in(service, "general definition")

    assert meta["tutor_state"].evidence_state is EvidenceState.PARTIAL
    assert meta["tutor_state"].teaching_action is not TeachingAction.CLARIFY


@pytest.mark.asyncio
async def test_unanswerable_question_does_not_replace_the_topic(mock_conversation_store):
    service, _ = make_topic_service(mock_conversation_store, conversation_with(*EVOLUTIONARY_CLARIFY),
                                    topic="evolutionary")
    meta_question = "you did not ask a check question why?"
    text, _, _ = await run_in(service, meta_question)
    assert text == STATE_UNCERTAINTY_MESSAGE

    conv = conversation_with(*EVOLUTIONARY_CLARIFY, meta_question, STATE_UNCERTAINTY_MESSAGE)
    mock_conversation_store.get_conversation.return_value = conv
    text, citations, _ = await run_in(service, "i would like to know the differences")
    assert text == "Here is the answer."
    assert citations[0].text == "topic passage"


@pytest.mark.asyncio
async def test_after_restart_an_unanswered_question_is_not_the_topic(mock_conversation_store):
    conv = conversation_with(*EVOLUTIONARY_CLARIFY, "you did not ask a check question why?", STATE_UNCERTAINTY_MESSAGE)
    service, _ = make_topic_service(mock_conversation_store, conv, topic="evolutionary")
    text, citations, _ = await run_in(service, "i would like to know the differences")

    assert text == "Here is the answer."
    assert citations[0].text == "topic passage"
