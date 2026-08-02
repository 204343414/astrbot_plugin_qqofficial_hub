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


def test_support_platforms_uses_valid_adapter_ids():
    """Must match ADAPTER_NAME_2_TYPE keys or the WebUI tag renders wrong.

    Note: this field is display-only in AstrBot v4.26.7 — it is read into
    StarMetadata and handed to the dashboard, but the event pipeline never
    consults it. Runtime gating requires filter.platform_adapter_type.
    """
    metadata = yaml.safe_load(
        Path(__file__).parents[1].joinpath("metadata.yaml").read_text(encoding="utf-8")
    )
    known = {
        "aiocqhttp", "qq_official", "qq_official_webhook", "telegram", "wecom",
        "wecom_ai_bot", "lark", "dingtalk", "discord", "slack", "kook",
        "vocechat", "weixin_official_account", "satori", "misskey", "line",
        "matrix", "weixin_oc", "mattermost", "webchat",
    }
    platforms = metadata["support_platforms"]
    assert isinstance(platforms, list) and platforms
    for item in platforms:
        assert item in known, item
    assert "qq_official" in platforms
