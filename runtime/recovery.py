from __future__ import annotations


class RecoveryPolicy:
    def should_inject_retry(self, context) -> bool:
        test_result = self._test_result(context)
        if not test_result:
            return False

        if test_result.get("ok"):
            return False

        if test_result.get("repair_injected"):
            return False

        if context.repair_attempts >= context.config.max_repair_attempts:
            return False

        return True

    def build_retry_message(self, context) -> dict:
        test = self._test_result(context) or {}

        return {
            "role": "user",
            "content": (
                "The previous test run failed. Analyze the preceding tool result and fix the code.\n\n"
                "<test_failure>\n"
                f"Command: {test.get('command')}\n"
                f"Error: {test.get('error')}\n\n"
                "</test_failure>"
            ),
        }

    def _test_result(self, context) -> dict | None:
        if hasattr(context, "task_test_result"):
            return context.task_test_result
        return context.last_test_result
