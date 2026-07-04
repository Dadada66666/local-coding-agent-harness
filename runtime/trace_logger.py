from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class TraceLogger:
    def __init__(self, run_dir: Path, run_id: str) -> None:
        self.run_id = run_id
        self.path = run_dir / "trace.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.step = 0
        self.started_at = time.monotonic()

    def log(self, event: dict) -> None:
        try:
            self.step += 1
            now = time.time()
            common = {
                "event_id": uuid4().hex,
                "run_id": self.run_id,
                "step": self.step,
                "ts": now,
                "ts_iso": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "elapsed_ms": round((time.monotonic() - self.started_at) * 1000, 3),
            }
            enriched = dict(event)
            enriched.update(common)

            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(enriched, ensure_ascii=False) + "\n")
        except Exception:
            return None

    def log_model_usage(self, usage, turn_id: int | None = None) -> None:
        if not usage:
            return

        event = {
            "type": "model_usage",
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
        if turn_id is not None:
            event["turn_id"] = turn_id

        self.log(event)