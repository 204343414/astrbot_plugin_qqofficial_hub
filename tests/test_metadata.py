from pathlib import Path

import yaml


def test_metadata_required_fields_are_nonempty_strings():
    metadata = yaml.safe_load(
        Path(__file__).parents[1].joinpath("metadata.yaml").read_text(encoding="utf-8")
    )
    for field in ("name", "desc", "version", "author"):
        assert isinstance(metadata[field], str)
        assert metadata[field].strip()
