from __future__ import annotations

from dataclasses import dataclass

from agent.context import AgentContext
from agent.messages import ModelResponse
from agent.model_client import ModelClient
from agent.prompts import build_initial_messages
from runtime.bootstrap import RuntimeBundle


@dataclass(slots=True)
class AgentRunner:
    context: AgentContext
    model_client: ModelClient
    runtime: RuntimeBundle

    def run(self) -> None:
        self.context.run_dir.mkdir(parents=True, exist_ok=True)
        self.context.messages = build_initial_messages(self.context.config.task)
        self.runtime.hooks.emit("UserPromptSubmit", {"task": self.context.config.task})

        while not self.context.state.stopped:
            if self.context.state.iteration >= self.context.config.max_iterations:
                self.context.state.stopped = True
                self.context.state.stop_reason = "max_iterations"
                break

            self.context.state.iteration += 1
            prepared_messages = self.runtime.context_manager.prepare_context(self.context.messages)
            response = self.model_client.call(prepared_messages)
            self.runtime.cost_tracker.add_response(response)
            self.runtime.trace_logger.model_response(response)
            self.context.messages.append(response.message)

            if not response.tool_calls:
                self.context.state.stopped = True
                self.context.state.stop_reason = "assistant_finished"
                break

            for tool_call in response.tool_calls:
                result = self.runtime.executor.execute(tool_call)
                self.runtime.trace_logger.tool_result(tool_call, result)
                self.context.messages.append(result.to_message(tool_call.id))

        self.runtime.hooks.emit("Stop", {"reason": self.context.state.stop_reason})
        self.runtime.diff_manager.write_diff()
        self.runtime.cost_tracker.write()
        self.runtime.report_writer.write(self.context)

