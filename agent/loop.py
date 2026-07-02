from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.context import AgentContext, RunConfig, make_run_id
from agent.model_client import ModelClient
from agent.prompts import SYSTEM_PROMPT, build_initial_messages
from runtime.artifact_store import ArtifactStore
from runtime.bootstrap import RuntimeBundle
from runtime.cost_tracker import CostTracker
from runtime.diff_manager import DiffManager
from runtime.hooks import HookEvent
from runtime.permission import PermissionGate
from runtime.report_writer import ReportWriter
from runtime.trace_logger import TraceLogger


@dataclass
class AgentLoop:
    model_client: ModelClient
    runtime: RuntimeBundle
    repo_path: Path
    permission_mode: str = "manual_approval"
    config: RunConfig | None = None

    def run(self, task: str) -> AgentContext:
        context = self.create_context(task=task, include_initial_message=True)
        self.runtime.hooks.trigger(
            HookEvent.USER_PROMPT_SUBMIT,
            task=task,
            context=context,
        )
        self.run_until_idle(context)
        self.runtime.hooks.trigger(HookEvent.STOP, context=context)
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
        context.messages.append({"role": "user", "content": prompt})
        context.finished = False
        context.final_text = ""
        self.run_until_idle(context)
        return context

    def finish(self, context: AgentContext) -> None:
        self.runtime.hooks.trigger(HookEvent.STOP, context=context)

    def run_until_idle(self, context: AgentContext) -> None:
        while not context.finished:
            self.runtime.context_manager.prepare_context(context)

            response = self.model_client.call(
                system=context.system_prompt,
                messages=context.messages,
                tools=self.runtime.tool_registry.schemas(),
            )

            context.add_assistant_message(response.message)
            context.trace.log_model_usage(response.usage)
            context.cost_tracker.add_usage(response.usage)

            if not response.tool_calls:
                context.final_text = response.text
                context.finished = True
                context.success = self.infer_success(context)
                break

            for tool_call in response.tool_calls:
                result = self.runtime.executor.execute(tool_call, context)
                context.add_tool_result(
                    tool_call_id=tool_call.id,
                    content=result.content,
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

    def create_context(self, task: str, include_initial_message: bool = True) -> AgentContext:
        repo_path = self.repo_path.resolve()
        run_id = make_run_id()
        run_dir = repo_path / ".agent" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        config = self.config or RunConfig(permission_mode=self.permission_mode)
        config.permission_mode = self.permission_mode

        return AgentContext(
            run_id=run_id,
            task=task,
            repo_path=repo_path,
            run_dir=run_dir,
            messages=build_initial_messages(task) if include_initial_message else [],
            system_prompt=SYSTEM_PROMPT,
            config=config,
            permission_mode=config.permission_mode,
            permission_gate=PermissionGate(),
            trace=TraceLogger(run_dir),
            artifacts=ArtifactStore(run_dir),
            cost_tracker=CostTracker(run_dir),
            diff_manager=DiffManager(repo_path, run_dir),
            report_writer=ReportWriter(),
        )

    def infer_success(self, context: AgentContext) -> bool:
        if context.last_test_result is not None:
            return bool(context.last_test_result.get("ok"))
        return True


AgentRunner = AgentLoop