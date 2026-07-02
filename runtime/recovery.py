from __future__ import annotations


class RecoveryPolicy:
    def should_inject_retry(self, context) -> bool:
        if not context.last_test_result:
            return False

        if context.last_test_result.get("ok"):
            return False

        if context.last_test_result.get("repair_injected"):
            return False

        if context.repair_attempts >= context.config.max_repair_attempts:
            return False

        return True

    def build_retry_message(self, context) -> dict:
        test = context.last_test_result or {}

        return {
            "role": "user",
            "content": (
                "The previous test run failed. Analyze the failure and fix the code.\n\n"
                "<test_failure>\n"
                f"Command: {test.get('command')}\n"
                f"Error: {test.get('error')}\n\n"
                f"{test.get('output_preview', '')[:8000]}\n"
                "</test_failure>"
            ),
        }

