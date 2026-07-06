from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from agent.context import AgentContext, RunConfig, make_run_id
from agent.model_client import ModelClient
from agent.prompts import build_initial_messages, build_system_prompt
from runtime.artifact_store import ArtifactStore
from runtime.bootstrap import RuntimeBundle
from runtime.cost_tracker import CostTracker
from runtime.diff_manager import DiffManager
from runtime.hooks import HookEvent
from runtime.permission import PermissionGate
from runtime.report_writer import ReportWriter
from runtime.sandbox import SandboxRuntime
from runtime.trace_logger import TraceLogger


MAX_ERROR_CHARS = 1000


@dataclass
class AgentLoop:
    model_client: ModelClient
    runtime: RuntimeBundle
    repo_path: Path
    permission_mode: str = "manual_approval"
    config: RunConfig | None = None

    def run(self, task: str) -> AgentContext:
        context = self.create_context(task=task, include_initial_message=True)
        try:
            self.runtime.hooks.trigger(
                HookEvent.USER_PROMPT_SUBMIT,
                task=task,
                context=context,
            )
            self.run_until_idle(context)
        except KeyboardInterrupt as exc:
            self.abort(context, reason="interrupted", message="Stopped: interrupted by user (Ctrl+C).", exc=exc)
        finally:
            self.finish(context)
        return context

    def start_interactive(self, task: str = "Interactive coding session") -> AgentContext:
        context = self.create_context(task=task, include_initial_message=False)
        self.runtime.hooks.trigger(
            HookEvent.USER_PROMPT_SUBMIT,
            task=task,
            context=context,
        )
        return context

    def submit(self, context: AgentContext, prompt: str) -> AgentContext:
        context.task = prompt
        context.add_user_message({"role": "user", "content": prompt})
        context.finished = False
        context.final_text = ""
        context.abort_reason = None
        try:
            self.run_until_idle(context)
        except KeyboardInterrupt as exc:
            self.abort(context, reason="interrupted", message="Stopped: interrupted by user (Ctrl+C).", exc=exc)
        return context

    def finish(self, context: AgentContext) -> None:
        if context.stop_recorded:
            return
        context.stop_recorded = True
        try:
            self.runtime.hooks.trigger(HookEvent.STOP, context=context)
        except Exception as exc:
            context.trace.log(
                {
                    "type": "stop_hook_error",
                    "exception_type": exc.__class__.__name__,
                    "exception": self._preview_error(str(exc)),
                }
            )
            print(f"[stop-error] {exc.__class__.__name__}: {self._preview_error(str(exc))}")

    def abort(self, context: AgentContext, reason: str, message: str, exc: BaseException | None = None) -> None:
        context.finished = True
        context.success = False
        context.abort_reason = reason
        context.final_text = message
        context.trace.log(
            {
                "type": "run_aborted",
                "turn_id": context.current_turn_id or None,
                "reason": reason,
                "message": message,
                "exception_type": exc.__class__.__name__ if exc else None,
                "exception": self._preview_error(str(exc)) if exc else None,
            }
        )

    def run_until_idle(self, context: AgentContext) -> None:
        while not context.finished:
            turn_id = context.turn_count + 1
            context.current_turn_id = turn_id
            turn_started = time.monotonic()
            context.trace.log(
                {
                    "type": "turn_start",
                    "turn_id": turn_id,
                    "message_count": len(context.messages),
                }
            )

            self.runtime.context_manager.prepare_context(context)
            tool_schemas = self.runtime.tool_registry.schemas()

            context.trace.log(
                {
                    "type": "model_call_start",
                    "turn_id": turn_id,
                    "message_count": len(context.messages),
                    "tool_schema_count": len(tool_schemas),
                }
            )
            model_started = time.monotonic()
            try:
                response = self.model_client.call(
                    system=context.system_prompt,
                    messages=context.messages,
                    tools=tool_schemas,
                )
            except KeyboardInterrupt:
                context.trace.log(
                    {
                        "type": "model_call_interrupted",
                        "turn_id": turn_id,
                        "duration_ms": round((time.monotonic() - model_started) * 1000, 3),
                    }
                )
                raise
            except Exception as exc:
                self._fail_model_call(context, turn_id, turn_started, model_started, exc)
                break
            model_duration_ms = round((time.monotonic() - model_started) * 1000, 3)

            context.trace.log(
                {
                    "type": "model_call_end",
                    "turn_id": turn_id,
                    "duration_ms": model_duration_ms,
                    "tool_call_count": len(response.tool_calls),
                    "tool_names": [tool_call.name for tool_call in response.tool_calls],
                    "input_tokens": getattr(response.usage, "input_tokens", None),
                    "output_tokens": getattr(response.usage, "output_tokens", None),
                }
            )
            context.trace.log_model_usage(response.usage, turn_id=turn_id)
            context.cost_tracker.record_model_call(
                turn_id=turn_id,
                system=context.system_prompt,
                messages=context.messages,
                tools=tool_schemas,
                response_message=response.message,
                usage=response.usage,
            )
            context.add_assistant_message(response.message)

            if not response.tool_calls:
                context.final_text = response.text
                context.finished = True
                context.success = self.infer_success(context)
                context.trace.log(
                    {
                        "type": "final_response",
                        "turn_id": turn_id,
                        "message_count": len(context.messages),
                        "success": context.success,
                        "text_preview": context.final_text[:500] if context.final_text else "",
                    }
                )
                self._log_turn_end(context, turn_id, turn_started)
                break

            context.trace.log(
                {
                    "type": "tool_batch_start",
                    "turn_id": turn_id,
                    "tool_call_count": len(response.tool_calls),
                    "tool_names": [tool_call.name for tool_call in response.tool_calls],
                }
            )
            tool_batch_started = time.monotonic()

            for tool_call in response.tool_calls:
                result = self.runtime.executor.execute(tool_call, context)
                context.add_tool_result(
                    tool_call_id=tool_call.id,
                    content=result.content,
                )
                if context.finished:
                    break

            context.trace.log(
                {
                    "type": "tool_batch_end",
                    "turn_id": turn_id,
                    "duration_ms": round((time.monotonic() - tool_batch_started) * 1000, 3),
                    "tool_call_count": len(response.tool_calls),
                    "tool_names": [tool_call.name for tool_call in response.tool_calls],
                    "message_count": len(context.messages),
                }
            )

            if self.runtime.recovery_policy.should_inject_retry(context):
                retry_message = self.runtime.recovery_policy.build_retry_message(context)
                context.messages.append(retry_message)
                context.repair_attempts += 1
                if context.last_test_result is not None:
                    context.last_test_result["repair_injected"] = True

            context.turn_count += 1
            if context.turn_count >= context.config.max_turns:
                context.finished = True
                context.success = False
                context.final_text = "Stopped: max turns exceeded."
                context.trace.log(
                    {
                        "type": "max_turns_exceeded",
                        "turn_id": turn_id,
                        "max_turns": context.config.max_turns,
                        "message_count": len(context.messages),
                    }
                )

            self._log_turn_end(context, turn_id, turn_started)

    def _fail_model_call(
        self,
        context: AgentContext,
        turn_id: int,
        turn_started: float,
        model_started: float,
        exc: Exception,
    ) -> None:
        message = f"Stopped: model call failed: {exc.__class__.__name__}: {self._preview_error(str(exc))}"
        context.finished = True
        context.success = False
        context.abort_reason = "model_call_failed"
        context.final_text = message
        context.trace.log(
            {
                "type": "model_call_error",
                "turn_id": turn_id,
                "duration_ms": round((time.monotonic() - model_started) * 1000, 3),
                "exception_type": exc.__class__.__name__,
                "exception": self._preview_error(str(exc)),
            }
        )
        self._log_turn_end(context, turn_id, turn_started)

    def _log_turn_end(self, context: AgentContext, turn_id: int, started: float) -> None:
        context.trace.log(
            {
                "type": "turn_end",
                "turn_id": turn_id,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "message_count": len(context.messages),
                "finished": context.finished,
                "success": context.success,
            }
        )

    def _preview_error(self, text: str) -> str:
        if len(text) <= MAX_ERROR_CHARS:
            return text
        omitted = len(text) - MAX_ERROR_CHARS
        return f"{text[:MAX_ERROR_CHARS]}... {omitted} chars omitted"

    def create_context(self, task: str, include_initial_message: bool = True) -> AgentContext:
        repo_path = self.repo_path.resolve()
        run_id = make_run_id()
        run_dir = repo_path / ".agent" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        config = self.config or RunConfig(permission_mode=self.permission_mode)
        config.permission_mode = self.permission_mode
        sandbox = SandboxRuntime(repo_path=repo_path, run_dir=run_dir, config=config)

        if config.sandbox_fail_if_unavailable and sandbox.status.enabled and not sandbox.status.available:
            raise RuntimeError(f"Sandbox requested but unavailable: {sandbox.status.reason}")

        initial_messages = build_initial_messages(task) if include_initial_message else []
        return AgentContext(
            run_id=run_id,
            task=task,
            repo_path=repo_path,
            run_dir=run_dir,
            messages=list(initial_messages),
            system_prompt=build_system_prompt(repo_path),
            config=config,
            conversation_messages=list(initial_messages),
            permission_mode=config.permission_mode,
            permission_gate=PermissionGate(),
            trace=TraceLogger(run_dir, run_id=run_id),
            artifacts=ArtifactStore(run_dir),
            cost_tracker=CostTracker(run_dir),
            diff_manager=DiffManager(repo_path, run_dir),
            report_writer=ReportWriter(),
            sandbox=sandbox,
        )

    def infer_success(self, context: AgentContext) -> bool:
        if context.last_test_result is not None:
            return bool(context.last_test_result.get("ok"))
        if context.changed_files:
            return False
        return bool(context.final_text)


AgentRunner = AgentLoop
