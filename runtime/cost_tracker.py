from __future__ import annotations

import json
from pathlib import Path

from agent.messages import ModelResponse


class CostTracker:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "cost.json"
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0

    def add_response(self, response: ModelResponse) -> None:
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.cost_usd += response.cost_usd

    def write(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "cost_usd": round(self.cost_usd, 6),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

