"""
Prompt Builder — constructs prompts for the LLM using context and instructions.
"""

from __future__ import annotations

from app.core.models import ChatMessage, Citation
from app.core.tutor_models import TeachingAction


class PromptBuilder:
    """Builds LLM prompts strictly adhering to RAG principles.

    Without a teaching action it builds the plain RAG prompt (the conventional
    baseline). With one, it builds the HoloTutor-X tutor prompt.
    """

    SYSTEM_PROMPT_TEMPLATE = """You are a helpful and precise assistant. 
You will be provided with context from uploaded documents to answer the user's question.

Guidelines:
1. Base your answer ONLY on the provided context.
2. If the context does not contain the information needed to answer the question, you must reply EXACTLY with: "Information not found in uploaded documents."
3. Do not use outside knowledge.
4. If you use information from the context, you can cite the document name and page number.

Context Information:
--------------------
{context}
--------------------
"""

    TUTOR_PROMPT_TEMPLATE = """You are HoloTutor-X, a tutor who teaches a student from the student's own study material.

Guidelines:
1. Use ONLY the passages below. Do not use outside knowledge.
2. Answer exactly what the student asked. If the passages do not cover it, say so in one sentence instead of explaining a different topic.
3. Keep the answer under 150 words.
4. You can cite the document name and page number.
5. Teaching method for this answer: {instruction}
{check_question}
Passages:
--------------------
{context}
--------------------
"""

    # SRS 3.2.7 — one instruction per teaching action that calls the LLM.
    ACTION_INSTRUCTIONS: dict[TeachingAction, str] = {
        TeachingAction.SIMPLIFY: "Simplify. Explain with short sentences and simple words.",
        TeachingAction.WORKED_EXAMPLE: "Worked example. Give one step-by-step example.",
        TeachingAction.ADVANCE: (
            "Advance. Confirm the answer in two or three sentences, "
            "then name the next topic the student should study."
        ),
        TeachingAction.CLARIFY: (
            "Clarify. Do not answer yet. Ask the student one short question "
            "to find out exactly what they want to know."
        ),
    }

    # SRS 3.2.4 — a check question follows these three actions.
    CHECK_QUESTION_ACTIONS = {
        TeachingAction.SIMPLIFY,
        TeachingAction.WORKED_EXAMPLE,
        TeachingAction.ADVANCE,
    }
    CHECK_QUESTION_LINE = (
        "6. After the answer, on a new line, write \"Check question:\" followed by "
        "one short question that tests whether the student understood.\n"
    )

    def build_messages(
        self,
        history: list[ChatMessage],
        query: str,
        citations: list[Citation],
        action: TeachingAction | None = None,
    ) -> list[ChatMessage]:
        """Combine system instructions, context, history, and the new query."""

        # Build context block
        if citations:
            context_blocks = []
            for i, cite in enumerate(citations, 1):
                context_blocks.append(
                    f"[Source {i}: {cite.document_name}, Page {cite.page_number}]\n{cite.text}"
                )
            context_str = "\n\n".join(context_blocks)
        else:
            context_str = "No relevant documents found for this query."

        # Format system message
        if action is None:
            system_content = self.SYSTEM_PROMPT_TEMPLATE.format(context=context_str)
        else:
            if action not in self.ACTION_INSTRUCTIONS:
                raise ValueError(f"No LLM prompt for teaching action '{action.value}'")
            system_content = self.TUTOR_PROMPT_TEMPLATE.format(
                instruction=self.ACTION_INSTRUCTIONS[action],
                check_question=self.CHECK_QUESTION_LINE if action in self.CHECK_QUESTION_ACTIONS else "",
                context=context_str,
            )
        system_msg = ChatMessage(role="system", content=system_content)

        # Build final message list
        final_messages = [system_msg]
        
        # Add conversation history
        for msg in history:
            final_messages.append(msg)
            
        # Add the new user query
        final_messages.append(ChatMessage(role="user", content=query))

        return final_messages
