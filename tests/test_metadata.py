from pathlib import Path

import yaml


def test_metadata_required_fields_are_nonempty_strings():
    metadata = yaml.safe_load(
        Path(__file__).parents[1].joinpath("metadata.yaml").read_text(encoding="utf-8")
    )
    for field in ("name", "desc", "version", "author"):
        assert isinstance(metadata[field], str)
        assert metadata[field].strip()


def test_config_schema_has_direct_astrbot_items():
    schema = yaml.safe_load(
        Path(__file__).parents[1].joinpath("_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(schema, dict)
    for name, item in schema.items():
        assert isinstance(item, dict), name
        assert isinstance(item.get("type"), str), name
