from strings import normalize_label


def test_normalizes_spacing_and_case() -> None:
    assert normalize_label("  Hello   World ") == "hello-world"


def test_preserves_single_word() -> None:
    assert normalize_label("Agent") == "agent"
