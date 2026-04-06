"""Direct LLM call wrapper for the agent (independent of LightRAG's pipeline)."""

from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """Get or create the shared AsyncOpenAI client."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.getenv("LLM_BINDING_API_KEY", ""),
            base_url=os.getenv("LLM_BINDING_HOST", "https://api.openai.com/v1"),
        )
    return _client


async def llm_complete(
    prompt: str,
    system_prompt: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    enable_thinking: bool = False,
) -> str:
    """Call the LLM directly via OpenAI-compatible API.

    This bypasses LightRAG's pipeline so the agent can make its own LLM calls
    for intent parsing and plan generation.
    """
    client = get_client()
    model = model or os.getenv("LLM_MODEL", "qwen3.5-plus")

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    logger.debug("LLM call: model=%s, prompt_len=%d", model, len(prompt))

    # Disable thinking mode for qwen models to reduce latency
    extra_body = {}
    if not enable_thinking:
        extra_body["enable_thinking"] = False

    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        extra_body=extra_body if extra_body else None,
    )

    content = response.choices[0].message.content or ""
    logger.debug("LLM response: %d chars", len(content))
    return content
