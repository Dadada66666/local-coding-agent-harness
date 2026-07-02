from __future__ import annotations

from agent.messages import ModelResponse


class ModelClient:
    """Model API adapter.

    The concrete provider implementation will be added after the runtime
    contract is stable.
    """

    def call(self, messages: list[dict]) -> ModelResponse:
        raise NotImplementedError("ModelClient.call is not wired yet.")

