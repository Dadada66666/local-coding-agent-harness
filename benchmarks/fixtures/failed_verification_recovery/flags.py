TRUE_VALUES = {"true", "1"}
FALSE_VALUES = {"false", "0"}


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"invalid boolean value: {value}")
