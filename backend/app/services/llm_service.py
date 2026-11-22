"""
OpenAI LLM Provider with streaming support.
"""

from __future__ import annotations

import logging
import typing

from openai import AsyncOpenAI

from app.core.interfaces import LLMProvider
from app.core.models import ChatMessage

logger = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    """LLM Provider using the official OpenAI Python client."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        # Fail after `timeout` seconds instead of the client default of 10 minutes (SRS: AI WAIT LIMIT).
        self._client = (
            AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1)
            if api_key
            else None
        )
        self._model = model
        
        if not api_key:
            logger.warning("OPENAI_API_KEY is missing. Chat generation will fail.")

    async def generate_stream(
        self, messages: list[ChatMessage], context: str = ""
    ) -> typing.AsyncGenerator[str, None]:
        """Generate response via OpenAI Chat Completions API with streaming."""
        if not self._client:
            yield "Error: OPENAI_API_KEY is not configured."
            return

        formatted_messages = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=formatted_messages,
                stream=True,
                temperature=0.0,  # low temperature for RAG grounding
            )
            
            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
                        
        except Exception as exc:
            logger.exception("Error calling OpenAI API")
            yield f"\n[LLM Error: {str(exc)}]"
