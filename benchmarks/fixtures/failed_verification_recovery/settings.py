from flags import parse_bool


def load_feature_state(values: dict[str, str], name: str, default: bool = False) -> bool:
    raw = values.get(name)
    return default if raw is None else parse_bool(raw)
