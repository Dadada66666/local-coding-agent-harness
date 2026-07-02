from __future__ import annotations

import json
from pathlib import Path


class CostTracker:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "cost.json"
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add_usage(self, usage) -> None:
        if not usage:
            return

        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    def write(self, context=None) -> Path:
        self.path.write_text(
            json.dumps(
                {
                    "calls": self.calls,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "total_tokens": self.input_tokens + self.output_tokens,
                    "estimated_cost_usd": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return self.path

