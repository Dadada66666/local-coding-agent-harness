from __future__ import annotations

import os
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

from agent.messages import ModelResponse, TokenUsage, ToolCall


DEFAULT_MAX_TOKENS = 4096


class ModelClient:
    """Anthropic Messages API adapter."""

    def __init__(self, model: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        load_dotenv()
        self.model = model or os.environ["MODEL_ID"]
        self.max_tokens = max_tokens

        base_url = os.getenv("ANTHROPIC_BASE_URL")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key

        self.client = Anthropic(**kwargs)

    def call(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )

        blocks = [self._block_to_dict(block) for block in response.content]
        tool_calls = [
            ToolCall(
                id=block["id"],
                name=block["name"],
                arguments=block.get("input") or {},
            )
            for block in blocks
            if block.get("type") == "tool_use"
        ]
        text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        usage = TokenUsage(
            input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
        )

        return ModelResponse(
            message={"role": "assistant", "content": blocks},
            text=text,
            tool_calls=tool_calls,
            usage=usage,
        )

    def _block_to_dict(self, block) -> dict:
        if isinstance(block, dict):
            return block
        if hasattr(block, "model_dump"):
            return block.model_dump(exclude_none=True)
        if hasattr(block, "to_dict"):
            return block.to_dict()
        return dict(vars(block))

