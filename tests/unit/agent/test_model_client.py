from pathlib import Path

import agent.model_client as model_client_module
from agent.model_client import ModelClient


def test_default_env_file_remains_at_harness_root_after_src_migration() -> None:
    module_path = Path(model_client_module.__file__).resolve()

    assert model_client_module.DEFAULT_ENV_FILE == module_path.parents[2] / ".env"
    assert model_client_module.DEFAULT_ENV_FILE.parent / ".env.example" == (
        module_path.parents[2] / ".env.example"
    )


def test_explicit_lcah_env_file_keeps_configuration_semantics(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / "provider.env"
    env_file.write_text(
        "MODEL_ID=explicit-model\n"
        "MODEL_CONTEXT_WINDOW_TOKENS=64000\n"
        "ANTHROPIC_BASE_URL=https://provider.example\n"
        "ANTHROPIC_API_KEY=explicit-key\n",
        encoding="utf-8",
    )
    for name in (
        "MODEL_ID",
        "MODEL_CONTEXT_WINDOW_TOKENS",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LCAH_ENV_FILE", str(env_file))

    captured_kwargs = {}

    def fake_anthropic(**kwargs):
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(model_client_module, "Anthropic", fake_anthropic)

    client = ModelClient()

    assert client.model == "explicit-model"
    assert client.context_window_tokens == 64000
    assert captured_kwargs == {
        "base_url": "https://provider.example",
        "api_key": "explicit-key",
    }
