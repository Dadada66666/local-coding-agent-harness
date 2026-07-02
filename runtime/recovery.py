from __future__ import annotations


class TestRepairPolicy:
    def should_repair(self, returncode: int, attempt: int, max_attempts: int) -> bool:
        return returncode != 0 and attempt < max_attempts

