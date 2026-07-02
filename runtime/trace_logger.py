from __future__ import annotations

import json
import time
from pathlib import Path


class TraceLogger:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "trace.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.step = 0

    def log(self, event: dict) -> None:
        self.step += 1
        event["step"] = self.step
        event["ts"] = time.time()

        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def log_model_usage(self, usage) -> None:
        if not usage:
            return

        self.log(
            {
                "type": "model_usage",
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            }
        )

