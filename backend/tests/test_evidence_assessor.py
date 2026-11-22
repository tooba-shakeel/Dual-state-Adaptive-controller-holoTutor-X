import pytest

from app.core.models import Citation
from app.core.tutor_models import EvidenceState
from app.services.evidence_assessor import EvidenceAssessor


def cite(score, text="passage"):
    return Citation(document_id="doc1", document_name="notes.pdf", page_number=1, text=text, score=score)


class StubLLM:
    """Answers the conflict check with a fixed reply."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def generate_stream(self, messages, context):
        self.calls += 1
        yield self.reply


@pytest.mark.asyncio
async def test_no_passages_is_insufficient():
    state, score = await EvidenceAssessor(llm=None).assess("q", [])
    assert state is EvidenceState.INSUFFICIENT
    assert score == 0.0


@pytest.mark.asyncio
async def test_best_below_partial_limit_is_insufficient():
    state, score = await EvidenceAssessor(llm=None).assess("q", [cite(0.49), cite(0.10)])
    assert state is EvidenceState.INSUFFICIENT
    assert score == 0.49


@pytest.mark.asyncio
async def test_strong_needs_high_best_and_two_relevant_passages():
    state, score = await EvidenceAssessor(llm=StubLLM("NO")).assess("q", [cite(0.80), cite(0.55)])
    assert state is EvidenceState.STRONG
    assert score == 0.80


@pytest.mark.asyncio
async def test_high_best_with_one_relevant_passage_is_partial():
    llm = StubLLM("YES")
    state, _ = await EvidenceAssessor(llm=llm).assess("q", [cite(0.90), cite(0.40)])
    assert state is EvidenceState.PARTIAL
    assert llm.calls == 0  # one relevant passage cannot conflict


@pytest.mark.asyncio
async def test_middle_scores_are_partial():
    state, _ = await EvidenceAssessor(llm=StubLLM("NO")).assess("q", [cite(0.60), cite(0.52)])
    assert state is EvidenceState.PARTIAL


@pytest.mark.asyncio
async def test_conflict_is_checked_before_strong():
    state, _ = await EvidenceAssessor(llm=StubLLM("YES")).assess("q", [cite(0.85), cite(0.70)])
    assert state is EvidenceState.CONFLICTING


@pytest.mark.asyncio
async def test_llm_error_means_not_conflicting():
    class BrokenLLM:
        async def generate_stream(self, messages, context):
            raise RuntimeError("service down")
            yield ""

    state, _ = await EvidenceAssessor(llm=BrokenLLM()).assess("q", [cite(0.85), cite(0.70)])
    assert state is EvidenceState.STRONG


def test_limits_must_be_ordered():
    with pytest.raises(ValueError):
        EvidenceAssessor(llm=None, partial_limit=0.8, strong_limit=0.5)
