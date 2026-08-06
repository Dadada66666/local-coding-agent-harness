from runtime.config import RunConfig
from runtime.session_factory import create_agent_session


def test_create_agent_session_composes_runtime_services(tmp_path) -> None:
    config = RunConfig(permission_mode="accept_edits")
    initial_messages = [{"role": "user", "content": "inspect the repository"}]

    context = create_agent_session(
        repo_path=tmp_path,
        task="inspect the repository",
        permission_mode="accept_edits",
        config=config,
        initial_messages=initial_messages,
        system_prompt="system prompt",
        include_initial_message=True,
        model_context_window_tokens="64000",
    )

    assert context.config is config
    assert context.config.context_window_tokens == 64000
    assert context.messages == initial_messages
    assert context.conversation_messages == initial_messages
    assert context.messages is not initial_messages
    assert context.run_dir == tmp_path / ".agent" / "runs" / context.run_id
    assert context.run_dir.is_dir()
    assert context.task_id == "task-1"
    assert context.task_sequence == 1
    assert context.permission_gate is not None
    assert context.trace is not None
    assert context.artifacts is not None
    assert context.sandbox is not None


def test_create_agent_session_can_start_without_initial_task_message(tmp_path) -> None:
    context = create_agent_session(
        repo_path=tmp_path,
        task="Interactive coding session",
        permission_mode="manual_approval",
        config=None,
        initial_messages=[],
        system_prompt="system prompt",
        include_initial_message=False,
    )

    assert context.messages == []
    assert context.conversation_messages == []
    assert context.task_id is None
    assert context.task_sequence == 0
