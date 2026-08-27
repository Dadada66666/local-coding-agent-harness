def total(values: list[int]) -> int:
    return sum(values)


def percentage(part: int, whole: int) -> float:
    if whole == 0:
        raise ValueError("whole must be non-zero")
    return part / whole


def bounded_percentage(part: int, whole: int) -> float:
    return min(100.0, max(0.0, percentage(part, whole)))
