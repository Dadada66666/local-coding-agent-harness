from __future__ import annotations


def head_tail_preview(text: str, max_chars: int) -> str:
    """Return a head/tail preview whose rendered length never exceeds max_chars."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text

    omitted = len(text) - max_chars
    for _ in range(3):
        marker = f"\n... {omitted} chars omitted ...\n"
        available = max(max_chars - len(marker), 0)
        updated = len(text) - available
        if updated == omitted:
            break
        omitted = updated

    marker = f"\n... {omitted} chars omitted ...\n"
    available = max(max_chars - len(marker), 0)
    if available == 0:
        return marker[:max_chars]
    head = available // 2
    tail = available - head
    return f"{text[:head]}{marker}{text[-tail:]}"
