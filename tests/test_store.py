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


def test_command_button_supports_reply_enter_and_anchor_fields():
    panel = {
        "name": "高级按钮",
        "markdown": "# 标题\n[🔗文档](https://bot.q.qq.com/)",
        "rows": [[{
            "id": "pick-image",
            "label": "选择图片",
            "visited_label": "已选择",
            "style": 1,
            "action_type": 2,
            "data": "/draw ",
            "permission": "everyone",
            "reply": True,
            "enter": False,
            "anchor": 1,
            "unsupport_tips": "请升级手机QQ",
        }]],
    }
    button = validate_panel(panel)["rows"][0][0]
    assert button["anchor"] == 1
    assert button["reply"] is True
    assert button["unsupport_tips"] == "请升级手机QQ"


def test_markdown_image_limits_and_link_label_are_validated():
    valid = {
        "name": "图片",
        "markdown": "![封面 #720px #1080px](https://example.com/a.png)\n[🔗打开](https://example.com)",
        "rows": [],
    }
    validate_panel(valid)
    invalid_size = {**valid, "markdown": "![封面 #721px #100px](https://example.com/a.png)"}
    with pytest.raises(ValueError, match="图片尺寸"):
        validate_panel(invalid_size)
    invalid_link = {**valid, "markdown": "[打开](https://example.com)"}
    with pytest.raises(ValueError, match="必须以"):
        validate_panel(invalid_link)


def test_saved_revision_invalidates_previously_issued_callback_card():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            store = PanelStore(Path(temp))
            origin = "头条flag:GroupMessage:group-a"
            await store.observe_group(origin, "头条flag")
            panel = (await store.bootstrap())["templates"]["default_panel"]
            nonce = await store.issue_panel_card(origin, panel)
            assert await store.get_issued_button(origin, nonce, "refresh") is not None
            changed = dict(panel)
            changed["name"] = "新版本"
            await store.save_panel("global", "", changed)
            assert await store.get_issued_button(origin, nonce, "refresh") is None
    asyncio.run(scenario())


def test_panel_can_enable_clicker_mention():
    panel = {
        "name": "At点击者",
        "markdown": "# 操作结果",
        "mention_clicker": True,
        "rows": [],
    }
    assert validate_panel(panel)["mention_clicker"] is True
