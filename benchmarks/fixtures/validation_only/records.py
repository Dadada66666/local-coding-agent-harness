from strings import normalize_label


def build_record(label: str, tags: list[str]) -> dict[str, object]:
    return {
        "label": label.strip(),
        "slug": normalize_label(label),
        "tags": [normalize_label(tag) for tag in tags],
    }
