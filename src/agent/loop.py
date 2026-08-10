from __future__ import annotations

import time
from dataclasses import dataclass
from inspect import signature
from pathlib import Path

from agent.model_client import ModelClient, ModelContextOverflowError
from agent.prompts import build_initial_messages, build_system_prompt
from runtime.bootstrap import RuntimeBundle
from runtime.config import RunConfig
from runtime.hooks import HookEvent
from runtime.plan import (
    PLAN_APPROVAL_CHOICES,
    ExecutionPath,
    PlanPhase,
    PlanPolicy,
    PlanStepStatus,
    apply_plan_response,
    deterministic_plan_response,
)
from runtime.session import AgentContext
from runtime.session_factory import create_agent_session
from runtime.task import TaskStatus, TaskTransitionError, is_terminal_task_status


MAX_ERROR_CHARS = 1000
COMPLETE_STOP_REASONS = {"end_turn", "stop_sequence"}
INCOMPLETE_STOP_REASONS = {"max_tokens", "model_context_window_exceeded"}
MAX_PLAN_RESPONSE_RETRIES = 1


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
        return self.start_task(context, prompt)

    def start_task(self, context: AgentContext, prompt: str) -> AgentContext:
        context.begin_task(prompt)
        context.add_user_message({"role": "user", "content": prompt})
        self._prepare_invocation(context)
        try:
            self.runtime.hooks.trigger(
                HookEvent.USER_PROMPT_SUBMIT,
                task=prompt,
                context=context,
            )
            self.runtime.context_manager.compact_task_boundary(context)
            self.run_until_idle(context)
        except KeyboardInterrupt as exc:
            self.abort(context, reason="interrupted", message="Stopped: interrupted by user (Ctrl+C).", exc=exc)
        return context

    def continue_task(self, context: AgentContext, user_text: str) -> AgentContext:
        context.add_user_continuation(user_text)
        if deterministic_plan_response(user_text) == "approve":
            resolution = apply_plan_response(
                context,
                "approve",
                source="deterministic_exact_match",
                require_continuation=True,
            )
            context.trace.log(
                {
                    "type": "plan_response_fast_path",
                    "task_id": context.task_id,
                    "continuation_id": resolution.continuation_id,
                    "action": resolution.action,
                    "plan_phase": resolution.state.phase.value,
                    "plan_version": resolution.state.version,
                }
            )
        self._prepare_invocation(context)
        try:
            self.run_until_idle(context)
        except KeyboardInterrupt as exc:
            self.abort(
                context,
                reason="interrupted",
                message="Stopped: interrupted by user (Ctrl+C).",
                exc=exc,
            )
        return context

    def resume_runtime(self, context: AgentContext, runtime_text: str) -> AgentContext:
        if context.task_id is None or is_terminal_task_status(context.task_status):
            raise TaskTransitionError("runtime continuation requires an active task")
        context.add_runtime_message({"role": "user", "content": runtime_text})
        self._prepare_invocation(context)
        try:
            self.run_until_idle(context)
        except KeyboardInterrupt as exc:
            self.abort(
                context,
                reason="interrupted",
                message="Stopped: interrupted by user (Ctrl+C).",
                exc=exc,
            )
        return context

    def resume(self, context: AgentContext, runtime_text: str) -> AgentContext:
        return self.resume_runtime(context, runtime_text)

    def _prepare_invocation(self, context: AgentContext) -> None:
        context.finished = False
        context.final_text = ""
        context.abort_reason = None
        context.success = False

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
        self._transition_task(
            context,
            TaskStatus.CANCELLED if reason == "interrupted" else TaskStatus.FAILED,
            trigger=f"abort:{reason}",
        )
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
        if self._pause_waiting_plan_without_user_input(context):
            return
        plan_response_retries = 0
        while not context.finished:
            validator = getattr(context, "validate_lifecycle_invariants", None)
            if callable(validator):
                validator()
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
                    "task_id": getattr(context, "task_id", None),
                    "task_model_call": context.task_model_calls,
                    "message_count": len(context.messages),
                }
            )

            if self._has_pending_plan_response(context):
                compact_boundary = getattr(
                    self.runtime.context_manager,
                    "compact_control_plane_boundary",
                    None,
                )
                if callable(compact_boundary):
                    compact_boundary(context)

            pending_continuation = getattr(
                context,
                "has_pending_user_continuation",
                None,
            )
            context.system_prompt = build_system_prompt(
                self.repo_path,
                getattr(context, "plan_state", None),
                has_user_continuation=(
                    bool(pending_continuation())
                    if callable(pending_continuation)
                    else False
                ),
                source_context=(
                    context.source_prompt_context()
                    if callable(getattr(context, "source_prompt_context", None))
                    else None
                ),
            )
            tool_schemas = self._tool_schemas(context)
            max_output_tokens = int(getattr(self.model_client, "max_tokens", 4096))
            preparation = self.runtime.context_manager.prepare_context(
                context,
                system=context.system_prompt,
                tools=tool_schemas,
                max_output_tokens=max_output_tokens,
            )

            context.trace.log(
                {
                    "type": "model_call_start",
                    "turn_id": turn_id,
                    "message_count": len(context.messages),
                    "tool_schema_count": len(tool_schemas),
                    "context_tokens": preparation.measurement.used_tokens,
                    "context_source": preparation.measurement.source,
                    "context_soft_limit": preparation.measurement.soft_limit_tokens,
                }
            )
            self.runtime.hooks.trigger(
                HookEvent.MODEL_CALL_START,
                context=context,
                task_model_call=context.task_model_calls,
            )
            request_message_count = len(context.messages)
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
            except ModelContextOverflowError as exc:
                recovered = self._attempt_context_overflow_recovery(
                    context,
                    tool_schemas,
                    max_output_tokens=max_output_tokens,
                    turn_id=turn_id,
                    source="provider_error",
                    detail=self._preview_error(str(exc)),
                )
                if recovered:
                    self._log_turn_end(context, turn_id, turn_started)
                    continue
                self._stop_for_context_overflow(
                    context,
                    turn_id=turn_id,
                    detail=self._preview_error(str(exc)),
                )
                self._log_turn_end(context, turn_id, turn_started)
                break
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
                    "cache_creation_input_tokens": getattr(
                        response.usage, "cache_creation_input_tokens", None
                    ),
                    "cache_read_input_tokens": getattr(
                        response.usage, "cache_read_input_tokens", None
                    ),
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

            if response.stop_reason == "model_context_window_exceeded":
                recovered = self._attempt_context_overflow_recovery(
                    context,
                    tool_schemas,
                    max_output_tokens=max_output_tokens,
                    turn_id=turn_id,
                    source="stop_reason",
                    detail=response.stop_reason,
                )
                if recovered:
                    self._log_turn_end(context, turn_id, turn_started)
                    continue
                self._stop_for_context_overflow(
                    context,
                    turn_id=turn_id,
                    detail=response.stop_reason,
                )
                self._log_turn_end(context, turn_id, turn_started)
                break

            context.mark_model_request_consumed(request_message_count)
            context.add_assistant_message(response.message)
            context.record_model_usage(response.usage, len(context.messages) - 1)

            response_action = self._response_action(response)
            if self._plan_response_protocol_violation(
                context,
                response,
                response_action=response_action,
            ):
                cancelled = [
                    self._cancelled_tool_result(
                        context,
                        tool_call,
                        turn_id,
                        reason="plan_response_protocol_violation",
                        metadata={"model_contract_violation": True},
                    )
                    for tool_call in response.tool_calls
                ]
                context.add_tool_results(cancelled)
                context.trace.log(
                    {
                        "type": "plan_response_protocol_violation",
                        "turn_id": turn_id,
                        "continuation_id": context.pending_user_continuation_id,
                        "tool_names": [call.name for call in response.tool_calls],
                        "response_action": response_action,
                    }
                )
                if plan_response_retries < MAX_PLAN_RESPONSE_RETRIES:
                    plan_response_retries += 1
                    context.add_runtime_message(
                        {
                            "role": "user",
                            "content": (
                                "A real user continuation is pending. Call "
                                "resolve_plan_response exactly once; do not perform repository "
                                "work in this control-plane turn."
                            ),
                        }
                    )
                    self._log_plan_response_retry(
                        context,
                        turn_id,
                        retry_count=plan_response_retries,
                        exhausted=False,
                        cause="protocol_violation",
                    )
                    self._log_turn_end(context, turn_id, turn_started)
                    continue
                self._log_plan_response_retry(
                    context,
                    turn_id,
                    retry_count=plan_response_retries,
                    exhausted=True,
                    cause="protocol_violation",
                )
                self._apply_plan_boundary(context)
                self._log_turn_end(context, turn_id, turn_started)
                break

            if response_action == "final":
                plan_retry = self._plan_final_retry(context)
                if plan_retry:
                    if self._has_pending_plan_response(context):
                        if plan_response_retries >= MAX_PLAN_RESPONSE_RETRIES:
                            self._log_plan_response_retry(
                                context,
                                turn_id,
                                retry_count=plan_response_retries,
                                exhausted=True,
                                cause="final_response",
                            )
                            self._apply_plan_boundary(context)
                            self._log_turn_end(context, turn_id, turn_started)
                            break
                        plan_response_retries += 1
                        self._log_plan_response_retry(
                            context,
                            turn_id,
                            retry_count=plan_response_retries,
                            exhausted=False,
                            cause="final_response",
                        )
                    context.add_runtime_message({"role": "user", "content": plan_retry})
                    context.trace.log(
                        {
                            "type": "plan_final_deferred",
                            "turn_id": turn_id,
                            "plan_phase": context.plan_state.phase.value,
                            "reason": plan_retry,
                        }
                    )
                    self._log_turn_end(context, turn_id, turn_started)
                    continue
                redactor = getattr(context, "redactor", None)
                context.final_text = (
                    redactor.redact(response.text)
                    if redactor is not None
                    else response.text
                )
                context.finished = True
                context.success = self.infer_success(context)
                self._transition_task(
                    context,
                    TaskStatus.COMPLETED if context.success else TaskStatus.FAILED,
                    trigger="final_response",
                )
                context.trace.log(
                    {
                        "type": "final_response",
                        "turn_id": turn_id,
                        "task_id": getattr(context, "task_id", None),
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
            executions = []
            cancelled_count = 0
            control_plane_boundary = False
            for tool_call in response.tool_calls:
                if context.finished or control_plane_boundary:
                    tool_results.append(
                        self._cancelled_tool_result(
                            context,
                            tool_call,
                            turn_id,
                            reason=(
                                "control_plane_transition"
                                if control_plane_boundary
                                else "earlier_tool_call_ended_task"
                            ),
                        )
                    )
                    cancelled_count += 1
                    continue

                result = self.runtime.executor.execute(tool_call, context)
                executions.append((tool_call, result))
                tool_results.append((tool_call.id, result.content, not result.ok))
                if result.ok and result.metadata.get("control_plane_transition"):
                    control_plane_boundary = True

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

            if not context.finished and self._should_retry_plan_response(context, executions):
                if plan_response_retries < MAX_PLAN_RESPONSE_RETRIES:
                    plan_response_retries += 1
                    context.add_runtime_message(
                        {
                            "role": "user",
                            "content": (
                                "The latest real user response is still pending. The previous tool "
                                "call was rejected by the runtime. Call resolve_plan_response "
                                "exactly once with approve, revise (with feedback), or cancel. Do "
                                "not call repository tools until it succeeds."
                            ),
                        }
                    )
                    self._log_plan_response_retry(
                        context,
                        turn_id,
                        retry_count=plan_response_retries,
                        exhausted=False,
                        cause="rejected_tool_call",
                    )
                    self._log_turn_end(context, turn_id, turn_started)
                    continue
                self._log_plan_response_retry(
                    context,
                    turn_id,
                    retry_count=plan_response_retries,
                    exhausted=True,
                    cause="rejected_tool_call",
                )

            if not context.finished and self._apply_plan_boundary(context):
                self._log_turn_end(context, turn_id, turn_started)
                break

            if not context.finished:
                progress = self.runtime.progress_policy.evaluate(
                    context,
                    response,
                    executions,
                    max_output_tokens=max_output_tokens,
                )
                if progress.action != "continue":
                    self._log_tool_progress(context, turn_id, progress, response)
                if progress.action == "retry" and progress.message:
                    context.add_runtime_message({"role": "user", "content": progress.message})
                elif progress.action == "stop":
                    self._stop_for_tool_stall(context, progress)

            if not context.finished and self.runtime.recovery_policy.should_inject_retry(context):
                retry_message = self.runtime.recovery_policy.build_retry_message(context)
                context.add_runtime_message(retry_message)
                context.repair_attempts += 1
                test_result = getattr(context, "task_test_result", context.last_test_result)
                if test_result is not None:
                    test_result["repair_injected"] = True

            self._log_turn_end(context, turn_id, turn_started)

    def _tool_schemas(self, context: AgentContext) -> list[dict]:
        schemas = self.runtime.tool_registry.schemas
        try:
            parameters = signature(schemas).parameters
        except (TypeError, ValueError):
            parameters = {}
        if parameters:
            return schemas(context)
        return schemas()

    def _plan_final_retry(self, context: AgentContext) -> str | None:
        state = getattr(context, "plan_state", None)
        if state is None or state.policy is PlanPolicy.OFF:
            return None
        if state.execution_path is ExecutionPath.DIRECT:
            return None
        if state.phase is PlanPhase.PLANNING:
            return (
                "Planning is still active. Use update_plan to create a structured plan and "
                "submit it before giving a final response."
            )
        if state.phase is PlanPhase.EXECUTING:
            incomplete = [
                step.id
                for step in state.steps
                if step.status is not PlanStepStatus.COMPLETED
            ]
            if incomplete:
                return (
                    "Plan execution is not complete. Continue the authorized plan and update "
                    f"the unfinished steps: {', '.join(incomplete[:10])}."
                )
            return (
                "All plan steps are complete, but the lifecycle is still executing. "
                "Call update_plan with action complete before the final response."
            )
        if state.phase is PlanPhase.AWAITING_APPROVAL and context.has_pending_user_continuation():
            return (
                "The task is still awaiting approval. Interpret the latest real user response "
                "with resolve_plan_response before giving a final response."
            )
        return None

    def _apply_plan_boundary(self, context: AgentContext) -> bool:
        state = getattr(context, "plan_state", None)
        if state is None or state.policy is PlanPolicy.OFF:
            return False
        if state.phase is PlanPhase.AWAITING_APPROVAL:
            pending_response = self._has_pending_plan_response(context)
            self._transition_task(
                context,
                TaskStatus.WAITING_USER,
                trigger="plan_awaiting_approval",
                waiting_reason="plan_approval",
            )
            context.finished = True
            context.success = False
            context.abort_reason = None
            if pending_response:
                context.final_text = (
                    "The latest user response could not be resolved by the model. The plan "
                    "remains awaiting approval and no repository changes were executed. "
                    "Use /approve, /revise, or /cancel-plan for a deterministic control-plane "
                    "action.\n\n"
                    f"{context.plan_controller.status_text()}\n\n{PLAN_APPROVAL_CHOICES}"
                )
            else:
                context.final_text = (
                    "Plan is awaiting user approval. No repository changes were executed.\n\n"
                    f"{context.plan_controller.status_text()}\n\n{PLAN_APPROVAL_CHOICES}"
                )
            context.trace.log(
                {
                    "type": "plan_awaiting_approval",
                    "turn_id": context.current_turn_id,
                    "version": state.version,
                    "step_count": len(state.steps),
                    "pending_user_response": pending_response,
                }
            )
            return True
        if state.phase is PlanPhase.CANCELLED:
            self._transition_task(
                context,
                TaskStatus.CANCELLED,
                trigger="plan_cancelled",
            )
            context.finished = True
            context.success = False
            context.abort_reason = "plan_cancelled"
            context.final_text = "Stopped: the current plan was cancelled."
            context.trace.log(
                {
                    "type": "plan_execution_cancelled",
                    "turn_id": context.current_turn_id,
                    "version": state.version,
                }
            )
            return True
        return False

    def _has_pending_plan_response(self, context: AgentContext) -> bool:
        state = getattr(context, "plan_state", None)
        pending = getattr(context, "has_pending_user_continuation", None)
        return bool(
            state is not None
            and state.phase is PlanPhase.AWAITING_APPROVAL
            and callable(pending)
            and pending()
        )

    def _should_retry_plan_response(self, context: AgentContext, executions) -> bool:
        if not self._has_pending_plan_response(context) or not executions:
            return False
        return all(not result.ok for _, result in executions)

    def _plan_response_protocol_violation(
        self,
        context: AgentContext,
        response,
        *,
        response_action: str,
    ) -> bool:
        if not self._has_pending_plan_response(context):
            return False
        if response_action == "final":
            return False
        if response_action != "tools":
            return response_action == "protocol_error"
        return not (
            len(response.tool_calls) == 1
            and response.tool_calls[0].name == "resolve_plan_response"
        )

    def _log_plan_response_retry(
        self,
        context: AgentContext,
        turn_id: int,
        *,
        retry_count: int,
        exhausted: bool,
        cause: str,
    ) -> None:
        context.trace.log(
            {
                "type": (
                    "plan_response_retry_exhausted"
                    if exhausted
                    else "plan_response_retry"
                ),
                "turn_id": turn_id,
                "continuation_id": context.pending_user_continuation_id,
                "retry_count": retry_count,
                "max_retries": MAX_PLAN_RESPONSE_RETRIES,
                "cause": cause,
            }
        )

    def _cancelled_tool_result(
        self,
        context,
        tool_call,
        turn_id: int,
        *,
        reason: str,
        metadata: dict | None = None,
    ) -> tuple[str, str, bool]:
        content = (
            "Cancelled because the current task cannot safely execute this tool call "
            f"after {reason}."
        )
        result_metadata = {"cancelled": True, "reason": reason, **(metadata or {})}
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
                "metadata": result_metadata,
            }
        )
        return tool_call.id, content, True

    def _pause_waiting_plan_without_user_input(self, context: AgentContext) -> bool:
        state = getattr(context, "plan_state", None)
        pending = getattr(context, "has_pending_user_continuation", None)
        if (
            state is None
            or state.phase is not PlanPhase.AWAITING_APPROVAL
            or (callable(pending) and pending())
        ):
            return False
        context.finished = True
        context.success = False
        context.abort_reason = None
        context.final_text = (
            "Plan is awaiting user approval. No repository changes were executed.\n\n"
            f"{context.plan_controller.status_text()}\n\n{PLAN_APPROVAL_CHOICES}"
        )
        context.trace.log(
            {
                "type": "plan_waiting_pause",
                "task_id": getattr(context, "task_id", None),
                "plan_version": state.version,
                "reason": "no_fresh_user_continuation",
            }
        )
        return True

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

    def _attempt_context_overflow_recovery(
        self,
        context: AgentContext,
        tool_schemas: list[dict],
        *,
        max_output_tokens: int,
        turn_id: int,
        source: str,
        detail: str,
    ) -> bool:
        max_attempts = int(context.config.max_context_recovery_attempts)
        if context.context_recovery_attempts >= max_attempts:
            context.trace.log(
                {
                    "type": "context_recovery_skipped",
                    "turn_id": turn_id,
                    "source": source,
                    "reason": "attempt_limit",
                    "attempts": context.context_recovery_attempts,
                }
            )
            return False

        context.context_recovery_attempts += 1
        preparation = self.runtime.context_manager.prepare_context(
            context,
            system=context.system_prompt,
            tools=tool_schemas,
            max_output_tokens=max_output_tokens,
            force=True,
            reason="context_overflow",
        )
        recovered = preparation.changed and preparation.saved_tokens > 0
        context.trace.log(
            {
                "type": "context_recovery",
                "turn_id": turn_id,
                "source": source,
                "detail": detail,
                "attempt": context.context_recovery_attempts,
                "recovered": recovered,
                "compacted": preparation.compacted,
                "microcompacted": preparation.microcompacted,
                "saved_tokens": preparation.saved_tokens,
                "context_tokens": preparation.measurement.used_tokens,
            }
        )
        return recovered

    def _stop_for_context_overflow(
        self,
        context: AgentContext,
        *,
        turn_id: int,
        detail: str,
    ) -> None:
        context.finished = True
        context.success = False
        context.abort_reason = "model_context_overflow"
        context.final_text = "Stopped: model context window exceeded after bounded recovery."
        self._transition_task(
            context,
            TaskStatus.FAILED,
            trigger="model_context_overflow",
        )
        context.trace.log(
            {
                "type": "model_context_overflow",
                "turn_id": turn_id,
                "detail": detail,
                "recovery_attempts": context.context_recovery_attempts,
                "message_count": len(context.messages),
            }
        )

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
        self._transition_task(
            context,
            TaskStatus.FAILED,
            trigger=f"model_response_{action}",
        )
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
        self._transition_task(
            context,
            TaskStatus.FAILED,
            trigger="max_model_calls_exceeded",
        )
        context.trace.log(
            {
                "type": "max_turns_exceeded",
                "turn_id": context.current_turn_id or None,
                "max_turns": context.config.max_turns,
                "task_model_calls": getattr(context, "task_model_calls", 0),
                "message_count": len(context.messages),
            }
        )

    def _log_tool_progress(self, context, turn_id: int, progress, response) -> None:
        context.trace.log(
            {
                "type": (
                    "tool_progress_retry"
                    if progress.action == "retry"
                    else "tool_progress_stalled"
                ),
                "turn_id": turn_id,
                "reason": progress.reason,
                "fingerprint": progress.fingerprint,
                "repeat_count": progress.repeat_count,
                "saturated_invalid_calls": progress.saturated_invalid_calls,
                "output_budget_saturated": progress.output_budget_saturated,
                "output_tokens": getattr(response.usage, "output_tokens", None),
                "max_output_tokens": int(getattr(self.model_client, "max_tokens", 4096)),
                "tools": list(progress.tools),
                "errors": list(progress.errors),
            }
        )

    def _stop_for_tool_stall(self, context, progress) -> None:
        tool = progress.tools[0] if progress.tools else "tool"
        error = progress.errors[0] if progress.errors else "invalid arguments"
        context.finished = True
        context.success = False
        context.abort_reason = "repeated_tool_failure"
        context.final_text = (
            f"Stopped: repeated invalid {tool} calls made no progress ({error})."
        )
        self._transition_task(
            context,
            TaskStatus.FAILED,
            trigger="repeated_tool_failure",
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
        self._transition_task(
            context,
            TaskStatus.FAILED,
            trigger="model_call_failed",
        )
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
                "task_id": getattr(context, "task_id", None),
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

    def _transition_task(
        self,
        context: AgentContext,
        status: TaskStatus,
        *,
        trigger: str,
        waiting_reason: str | None = None,
    ) -> None:
        transition = getattr(context, "transition_task", None)
        if not callable(transition):
            return
        if getattr(context, "task_id", None) is None or is_terminal_task_status(
            getattr(context, "task_status", TaskStatus.IDLE)
        ):
            return
        transition(
            status,
            trigger=trigger,
            waiting_reason=waiting_reason,
        )

    def create_context(self, task: str, include_initial_message: bool = True) -> AgentContext:
        repo_path = self.repo_path.resolve()
        initial_messages = build_initial_messages(task) if include_initial_message else []
        return create_agent_session(
            repo_path=repo_path,
            task=task,
            permission_mode=self.permission_mode,
            config=self.config,
            initial_messages=initial_messages,
            system_prompt=build_system_prompt(repo_path),
            include_initial_message=include_initial_message,
            model_context_window_tokens=getattr(
                self.model_client, "context_window_tokens", None
            ),
        )

    def infer_success(self, context: AgentContext) -> bool:
        if getattr(context, "task_unresolved_mutation_failure", False):
            return False

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
