import asyncio
import tempfile
from pathlib import Path

import pytest

from qqofficial_hub.store import PanelStore, validate_panel


def test_callback_button_requires_safe_action_id_and_unique_id():
    panel = {
        "name": "测试", "markdown": "正文", "rows": [[
            {"id": "one", "label": "测试", "visited_label": "测试", "style": 1,
             "action_type": 1, "data": "hub.test", "permission": "everyone"},
        ]],
    }
    assert validate_panel(panel)["rows"][0][0]["data"] == "hub.test"
    panel["rows"][0].append({**panel["rows"][0][0]})
    with pytest.raises(ValueError, match="ID 必须唯一"):
        validate_panel(panel)


def test_store_rejects_group_override_before_observation():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            store = PanelStore(Path(temp))
            panel = (await store.bootstrap())["templates"]["default_panel"]
            with pytest.raises(ValueError, match="已观察"):
                await store.save_panel("group", "头条flag:GroupMessage:group", panel)
    asyncio.run(scenario())
