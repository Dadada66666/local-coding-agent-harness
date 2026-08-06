from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

from agent.messages import ModelResponse, TokenUsage, ToolCall


DEFAULT_MAX_TOKENS = 4096
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
CONTEXT_OVERFLOW_MARKERS = (
    "context length",
    "context window",
    "maximum context",
    "model_context_window_exceeded",
    "prompt is too long",
    "prompt too long",
    "request too large",
    "too many tokens",
)


class ModelContextOverflowError(RuntimeError):
    pass


class ModelClient:
    """Anthropic Messages API adapter."""

    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        context_window_tokens: int | None = None,
    ) -> None:
        env_file = os.getenv("LCAH_ENV_FILE")
        dotenv_path = Path(env_file).expanduser() if env_file else None
        if dotenv_path is None and (DEFAULT_ENV_FILE.parent / ".env.example").is_file():
            dotenv_path = DEFAULT_ENV_FILE
        if dotenv_path is not None and dotenv_path.is_file():
            load_dotenv(dotenv_path=dotenv_path)
        self.model = model or os.environ["MODEL_ID"]
        self.max_tokens = int(max_tokens)
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        if context_window_tokens is not None and context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be > 0")
        self.context_window_tokens = (
            int(context_window_tokens)
            if context_window_tokens is not None
            else self._positive_env_int("MODEL_CONTEXT_WINDOW_TOKENS")
        )

        base_url = os.getenv("ANTHROPIC_BASE_URL")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key

        self.client = Anthropic(**kwargs)

    def call(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:
            if self._is_context_overflow(exc):
                raise ModelContextOverflowError(str(exc)) from exc
            raise

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
            cache_creation_input_tokens=(
                getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=(
                getattr(response.usage, "cache_read_input_tokens", 0) or 0
            ),
            cache_deleted_input_tokens=(
                getattr(response.usage, "cache_deleted_input_tokens", 0) or 0
            ),
        )

        return ModelResponse(
            message={"role": "assistant", "content": blocks},
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=getattr(response, "stop_reason", None),
        )

    def _block_to_dict(self, block) -> dict:
        if isinstance(block, dict):
            return block
        if hasattr(block, "model_dump"):
            return block.model_dump(exclude_none=True)
        if hasattr(block, "to_dict"):
            return block.to_dict()
        return dict(vars(block))

    def _positive_env_int(self, name: str) -> int | None:
        raw = os.getenv(name)
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    def _is_context_overflow(self, exc: Exception) -> bool:
        values = [str(exc)]
        for name in ("body", "message", "response"):
            value = getattr(exc, name, None)
            if value is not None:
                values.append(str(value))
        rendered = " ".join(values).lower()
        return any(marker in rendered for marker in CONTEXT_OVERFLOW_MARKERS)
