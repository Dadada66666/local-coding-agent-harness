from __future__ import annotations

import cli.main as cli_main


def test_interactive_prompt_marks_ansi_sequences_for_readline(monkeypatch) -> None:
    monkeypatch.setattr(cli_main, "_readline_prompt_markers_supported", lambda: True)

    prompt = cli_main.interactive_prompt()

    assert prompt == "\001\033[36m\002s01 >> \001\033[0m\002"


def test_interactive_prompt_uses_plain_ansi_without_readline(monkeypatch) -> None:
    monkeypatch.setattr(cli_main, "_readline_prompt_markers_supported", lambda: False)

    prompt = cli_main.interactive_prompt()

    assert prompt == "\033[36ms01 >> \033[0m"
