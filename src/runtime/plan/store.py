from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from runtime.plan.models import PlanPolicy, PlanState, utc_now


PLAN_SCHEMA_VERSION = 1
MAX_GOAL_CHARS = 4000
MAX_TEXT_CHARS = 2000
MAX_STEP_DESCRIPTION_CHARS = 1000
MAX_STEPS = 100


class PlanStore:
    """Persist bounded audit snapshots; this is not full session recovery."""

    def __init__(self, run_dir: Path, run_id: str, *, trace=None, redactor=None) -> None:
        self.path = Path(run_dir) / "plan.json"
        self.run_id = run_id
        self.trace = trace
        self.redactor = redactor

    def save(self, state: PlanState, *, task: str) -> bool:
        if state.policy is PlanPolicy.OFF:
            return True

        payload = self._payload(state, task=task)
        if self.redactor is not None:
            payload = self.redactor.redact_value(payload)

        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".plan-",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._log_error("write", exc)
            return False
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._log_error("read", exc)
            return None
        if not isinstance(value, dict) or value.get("schema_version") != PLAN_SCHEMA_VERSION:
            self._log_error("read", ValueError("unsupported plan snapshot schema"))
            return None
        return value

    def clear(self) -> bool:
        try:
            self.path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            self._log_error("clear", exc)
            return False

    def _payload(self, state: PlanState, *, task: str) -> dict[str, Any]:
        steps = [
            {
                "id": self._clip(step.id, 200),
                "description": self._clip(step.description, MAX_STEP_DESCRIPTION_CHARS),
                "status": step.status.value,
            }
            for step in state.steps[:MAX_STEPS]
        ]
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "run_id": self.run_id,
            "task": self._clip(str(task), MAX_GOAL_CHARS),
            "policy": state.policy.value,
            "execution_path": state.execution_path.value,
            "selection_reason": self._optional_text(state.selection_reason),
            "phase": state.phase.value,
            "version": state.version,
            "approved_version": state.approved_version,
            "approval_source": state.approval_source,
            "explanation": self._optional_text(state.explanation),
            "revision_feedback": self._optional_text(state.revision_feedback),
            "steps": steps,
            "steps_omitted": max(len(state.steps) - len(steps), 0),
            "updated_at": state.updated_at or utc_now(),
            "snapshot_purpose": "plan audit; not full session recovery",
        }

    def _optional_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self._clip(str(value), MAX_TEXT_CHARS)

    def _clip(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[: max(limit - 3, 0)]}..."

    def _log_error(self, operation: str, exc: Exception) -> None:
        if self.trace is None:
            return
        self.trace.log(
            {
                "type": "plan_snapshot_error",
                "operation": operation,
                "path": str(self.path),
                "exception_type": exc.__class__.__name__,
                "exception": str(exc)[:500],
            }
        )
