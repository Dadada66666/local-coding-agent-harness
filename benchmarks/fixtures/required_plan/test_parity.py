from parity import is_even


def test_even_numbers() -> None:
    assert is_even(0)
    assert is_even(8)


def test_odd_numbers() -> None:
    assert not is_even(3)
    assert not is_even(-5)
