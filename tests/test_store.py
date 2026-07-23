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


def test_issued_panel_button_is_scoped_to_its_group():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            store = PanelStore(Path(temp))
            origin = "头条flag:GroupMessage:group-a"
            panel = (await store.bootstrap())["templates"]["default_panel"]
            nonce = await store.issue_panel_card(origin, panel, reply_msg_id="user-msg-1")
            context = await store.get_issued_button_context(origin, nonce, "refresh")
            assert context is not None and context[1] == "user-msg-1"
            assert await store.get_issued_button(origin, nonce, "refresh") is not None
            assert await store.get_issued_button("头条flag:GroupMessage:group-b", nonce, "refresh") is None
    asyncio.run(scenario())


def test_blueprint_rejects_edges_to_missing_nodes_and_persists_valid_graph():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            store = PanelStore(Path(temp))
            graph = {
                "viewport": {"x": 0, "y": 0, "scale": 1},
                "nodes": [
                    {"id": "root", "type": "panel", "title": "主菜单", "x": 0, "y": 0},
                    {"id": "rss", "type": "panel", "title": "RSS", "x": 100, "y": 0},
                ],
                "edges": [{"from": "root", "to": "rss"}],
            }
            saved = await store.save_blueprint(graph)
            assert saved["edges"] == [{"from": "root", "to": "rss"}]
            graph["edges"] = [{"from": "root", "to": "missing"}]
            with pytest.raises(ValueError, match="已有节点"):
                await store.save_blueprint(graph)
    asyncio.run(scenario())
