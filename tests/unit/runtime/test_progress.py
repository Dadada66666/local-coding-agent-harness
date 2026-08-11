from types import SimpleNamespace

from agent.messages import TokenUsage
from runtime.progress import ToolProgressPolicy


def make_context():
    return SimpleNamespace(
        task_failure_fingerprint=None,
        task_failure_repeat_count=0,
        task_saturated_invalid_calls=0,
    )


def response(*, output_tokens: int = 0):
    return SimpleNamespace(usage=TokenUsage(output_tokens=output_tokens))


def execution(
    *,
    tool: str = "edit_file",
    arguments: dict | None = None,
    category: str = "validation_error",
    ok: bool = False,
):
    call = SimpleNamespace(name=tool, arguments=arguments or {"path": "demo.py"})
    metadata = {} if ok else {category: True}
    result = SimpleNamespace(
        ok=ok,
        error=None if ok else "deterministic tool failure",
        metadata=metadata,
    )
    return call, result


def test_same_validation_failure_is_bounded() -> None:
    context = make_context()
    policy = ToolProgressPolicy()
    failures = [execution()]

    first = policy.evaluate(context, response(), failures, max_output_tokens=4096)
    second = policy.evaluate(context, response(), failures, max_output_tokens=4096)
    third = policy.evaluate(context, response(), failures, max_output_tokens=4096)

    assert first.action == "continue"
    assert second.action == "retry"
    assert second.reason == "repeated_invalid_tool_call"
    assert third.action == "stop"
    assert third.repeat_count == 3


def test_same_unavailable_tool_failure_is_bounded() -> None:
    context = make_context()
    policy = ToolProgressPolicy()
    failures = [execution(category="unavailable_tool")]

    policy.evaluate(context, response(), failures, max_output_tokens=4096)
    second = policy.evaluate(context, response(), failures, max_output_tokens=4096)
    third = policy.evaluate(context, response(), failures, max_output_tokens=4096)

    assert second.action == "retry"
    assert third.action == "stop"
    assert third.tools == ("edit_file",)


def test_different_arguments_are_not_the_same_failure_loop() -> None:
    context = make_context()
    policy = ToolProgressPolicy()

    policy.evaluate(
        context,
        response(),
        [execution(arguments={"path": "a.py"})],
        max_output_tokens=4096,
    )
    decision = policy.evaluate(
        context,
        response(),
        [execution(arguments={"path": "b.py"})],
        max_output_tokens=4096,
    )

    assert decision.action == "continue"
    assert decision.repeat_count == 1


def test_success_resets_deterministic_failure_history() -> None:
    context = make_context()
    policy = ToolProgressPolicy()
    policy.evaluate(context, response(), [execution()], max_output_tokens=4096)

    decision = policy.evaluate(
        context,
        response(),
        [execution(tool="read_file", ok=True)],
        max_output_tokens=4096,
    )

    assert decision.action == "continue"
    assert context.task_failure_fingerprint is None
    assert context.task_failure_repeat_count == 0


def test_mixed_batch_still_tracks_deterministic_contract_failure() -> None:
    context = make_context()
    policy = ToolProgressPolicy()
    executions = [
        execution(tool="read_file", ok=True),
        execution(tool="edit_file", category="model_contract_violation"),
    ]

    first = policy.evaluate(context, response(), executions, max_output_tokens=4096)
    second = policy.evaluate(context, response(), executions, max_output_tokens=4096)

    assert first.repeat_count == 1
    assert second.action == "retry"
    assert second.tools == ("edit_file",)
