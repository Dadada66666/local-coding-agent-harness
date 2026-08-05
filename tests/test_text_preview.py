from __future__ import annotations

import re

from runtime.text_preview import head_tail_preview


def test_head_tail_preview_is_strictly_bounded_and_reports_actual_omission() -> None:
    text = "0123456789" * 100

    preview = head_tail_preview(text, 100)

    match = re.search(r"\n\.\.\. (\d+) chars omitted \.\.\.\n", preview)
    assert match is not None
    retained_chars = len(preview) - len(match.group(0))
    assert len(preview) == 100
    assert int(match.group(1)) == len(text) - retained_chars
    assert preview.startswith(text[:10])
    assert preview.endswith(text[-10:])


def test_head_tail_preview_handles_zero_and_short_inputs() -> None:
    assert head_tail_preview("content", 0) == ""
    assert head_tail_preview("content", 20) == "content"
