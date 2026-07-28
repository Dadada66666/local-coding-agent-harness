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
COMPLETE_STOP_REASONS = {"end_turn", "stop_sequence"}
INCOMPLETE_STOP_REASONS = {"max_tokens", "model_context_window_exceeded"}


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
        context.reset_task_state()
        context.add_user_message({"role": "user", "content": prompt})
        context.finished = False
        context.final_text = ""
        context.abort_reason = None
        context.success = False
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
            if getattr(context, "task_model_calls", 0) >= context.config.max_turns:
                self._stop_for_model_call_limit(context)
                break

            context.turn_count += 1
            context.task_model_calls = getattr(context, "task_model_calls", 0) + 1
            turn_id = context.turn_count
            context.current_turn_id = turn_id
            turn_started = time.monotonic()
            context.trace.log(
                {
                    "type": "turn_start",
                    "turn_id": turn_id,
                    "task_model_call": context.task_model_calls,
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
                    "stop_reason": response.stop_reason,
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

            response_action = self._response_action(response)
            if response_action == "final":
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

            if response_action != "tools":
                if response.tool_calls:
                    cancelled = [
                        self._cancelled_tool_result(
                            context,
                            tool_call,
                            turn_id,
                            reason=f"model_response_{response_action}",
                        )
                        for tool_call in response.tool_calls
                    ]
                    context.add_tool_results(cancelled)
                self._stop_for_model_response(context, response, response_action, turn_id)
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

            tool_results: list[tuple[str, str, bool]] = []
            cancelled_count = 0
            for tool_call in response.tool_calls:
                if context.finished:
                    tool_results.append(
                        self._cancelled_tool_result(
                            context,
                            tool_call,
                            turn_id,
                            reason="earlier_tool_call_ended_task",
                        )
                    )
                    cancelled_count += 1
                    continue

                result = self.runtime.executor.execute(tool_call, context)
                tool_results.append((tool_call.id, result.content, not result.ok))

            context.add_tool_results(tool_results)
            context.task_tool_rounds = getattr(context, "task_tool_rounds", 0) + 1

            context.trace.log(
                {
                    "type": "tool_batch_end",
                    "turn_id": turn_id,
                    "duration_ms": round((time.monotonic() - tool_batch_started) * 1000, 3),
                    "tool_call_count": len(response.tool_calls),
                    "tool_names": [tool_call.name for tool_call in response.tool_calls],
                    "cancelled_count": cancelled_count,
                    "message_count": len(context.messages),
                }
            )

            if not context.finished and self.runtime.recovery_policy.should_inject_retry(context):
                retry_message = self.runtime.recovery_policy.build_retry_message(context)
                context.messages.append(retry_message)
                context.repair_attempts += 1
                test_result = getattr(context, "task_test_result", context.last_test_result)
                if test_result is not None:
                    test_result["repair_injected"] = True

            self._log_turn_end(context, turn_id, turn_started)

    def _cancelled_tool_result(
        self,
        context,
        tool_call,
        turn_id: int,
        *,
        reason: str,
    ) -> tuple[str, str, bool]:
        content = "Cancelled because the current task cannot safely execute this tool call."
        context.trace.log(
            {
                "type": "tool_result",
                "turn_id": turn_id,
                "tool_call_id": tool_call.id,
                "tool": tool_call.name,
                "ok": False,
                "error": "cancelled",
                "output_preview": content,
                "artifact_path": None,
                "metadata": {"cancelled": True, "reason": reason},
            }
        )
        return tool_call.id, content, True

    def _response_action(self, response) -> str:
        stop_reason = response.stop_reason
        has_tool_calls = bool(response.tool_calls)

        if stop_reason is None:
            return "tools" if has_tool_calls else "final"
        if stop_reason == "tool_use":
            return "tools" if has_tool_calls else "protocol_error"
        if stop_reason in COMPLETE_STOP_REASONS:
            return "protocol_error" if has_tool_calls else "final"
        if stop_reason in INCOMPLETE_STOP_REASONS:
            return "incomplete"
        if stop_reason == "refusal":
            return "refusal"
        return "protocol_error"

    def _stop_for_model_response(self, context, response, action: str, turn_id: int) -> None:
        stop_reason = response.stop_reason or "missing"
        messages = {
            "incomplete": f"Stopped: model response was incomplete ({stop_reason}).",
            "refusal": "Stopped: model refused the request.",
            "protocol_error": f"Stopped: invalid model response protocol ({stop_reason}).",
        }
        context.finished = True
        context.success = False
        context.abort_reason = f"model_{action}"
        context.final_text = messages[action]
        context.trace.log(
            {
                "type": f"model_response_{action}",
                "turn_id": turn_id,
                "stop_reason": response.stop_reason,
                "tool_call_count": len(response.tool_calls),
                "text_preview": response.text[:500] if response.text else "",
            }
        )

    def _stop_for_model_call_limit(self, context) -> None:
        context.finished = True
        context.success = False
        context.abort_reason = "max_model_calls_exceeded"
        context.final_text = "Stopped: max model calls per task exceeded."
        context.trace.log(
            {
                "type": "max_turns_exceeded",
                "turn_id": context.current_turn_id or None,
                "max_turns": context.config.max_turns,
                "task_model_calls": getattr(context, "task_model_calls", 0),
                "message_count": len(context.messages),
            }
        )

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
        changed_files = getattr(context, "task_changed_files", None)
        if changed_files is None:
            changed_files = getattr(context, "changed_files", set())
        has_task_mutations = getattr(context, "has_task_mutations", None)
        task_mutated = (
            bool(has_task_mutations())
            if callable(has_task_mutations)
            else bool(changed_files)
        )
        if hasattr(context, "task_test_result"):
            if context.task_test_result is not None:
                if not context.task_test_result.get("ok"):
                    return False
                if task_mutated and hasattr(context, "task_verification_version"):
                    return context.task_verification_version == context.mutation_version
                return True
        elif context.last_test_result is not None:
            return bool(context.last_test_result.get("ok"))
        if task_mutated:
            return False
        return bool(context.final_text)


AgentRunner = AgentLoop
