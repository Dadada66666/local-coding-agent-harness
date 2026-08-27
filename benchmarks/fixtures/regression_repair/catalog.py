from textnorm import normalize_key


def index_labels(labels: list[str]) -> dict[str, str]:
    return {normalize_key(label): label.strip() for label in labels}
