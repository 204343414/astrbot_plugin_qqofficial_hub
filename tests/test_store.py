import asyncio
import tempfile
from pathlib import Path

import pytest

from qqofficial_hub import ephemeral as ep
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


def test_markdown_parameter_command_is_validated_and_limited():
    panel = {
        "name": "文本按钮",
        "markdown": '<qqbot-cmd-input text="%2Fmyrss%20%2B%20" show="%F0%9F%93%A1%20%E6%B7%BB%E5%8A%A0%E8%AE%A2%E9%98%85" reference="true" />',
        "rows": [],
    }
    validate_panel(panel)
    malformed = {**panel, "markdown": '<qqbot-cmd-input text="/myrss + " />'}
    with pytest.raises(ValueError, match="格式错误"):
        validate_panel(malformed)
    too_long = {**panel, "markdown": f'<qqbot-cmd-input text="{"x" * 101}" show="x" reference="false" />'}
    with pytest.raises(ValueError, match="1~100"):
        validate_panel(too_long)


def test_callback_action_params_must_be_small_json_object():
    panel = {
        "name": "动作参数",
        "markdown": "# 参数",
        "rows": [[{
            "id": "page-next", "label": "下一页", "visited_label": "下一页",
            "style": 1, "action_type": 1, "data": "hub.test",
            "permission": "everyone", "action_params": {"page": 2},
        }]],
    }
    assert validate_panel(panel)["rows"][0][0]["action_params"] == {"page": 2}
    invalid = {**panel, "rows": [[{**panel["rows"][0][0], "action_params": [1, 2]}]]}
    with pytest.raises(ValueError, match="JSON 对象"):
        validate_panel(invalid)


# --- surviving a plugin reload ----------------------------------------------
#
# AstrBot keeps the OLD plugin instance alive across a reload (pending tasks,
# the interaction bridge), so two PanelStore objects share one file. Every
# write serialises a whole in-memory snapshot, so whichever flushed last used
# to erase the other's cards -- and the next tap answered
# "卡片不存在或已过期" seconds after the card was sent.

def _lobby_card():
    return ep.validate_card({
        "id": "demo", "markdown": "# demo",
        "rows": [[{"id": "go", "label": "go", "action_id": "demo.run"}]],
    })


def test_a_stale_instance_does_not_erase_a_new_instances_cards():
    async def scenario():
        directory = Path(tempfile.mkdtemp())
        origin = "qq_official:GroupMessage:G1"

        old = PanelStore(directory, callback_ttl_seconds=3600)
        await old.bootstrap()
        old._data.setdefault("groups", {})[origin] = {"seen_at": 1}
        old_nonce, _ = await old.issue_ephemeral_card(origin, _lobby_card(), "s")

        fresh = PanelStore(directory, callback_ttl_seconds=3600)
        await fresh.bootstrap()
        fresh._data.setdefault("groups", {})[origin] = {"seen_at": 1}
        new_nonce, _ = await fresh.issue_ephemeral_card(origin, _lobby_card(), "s")

        # The old instance flushes for an unrelated reason, after the reload.
        old._write_atomic(old._data)

        reader = PanelStore(directory, callback_ttl_seconds=3600)
        await reader.bootstrap()
        table = reader._data["ephemeral_cards"]
        assert old_nonce in table, "旧卡不该消失"
        assert new_nonce in table, "重载后发的卡被旧实例覆盖了"
    asyncio.run(scenario())


def test_a_card_issued_after_reload_is_still_clickable():
    """The symptom users actually saw."""
    async def scenario():
        directory = Path(tempfile.mkdtemp())
        origin = "qq_official:GroupMessage:G1"

        old = PanelStore(directory, callback_ttl_seconds=3600)
        await old.bootstrap()
        old._data.setdefault("groups", {})[origin] = {"seen_at": 1}
        await old.issue_ephemeral_card(origin, _lobby_card(), "s")

        fresh = PanelStore(directory, callback_ttl_seconds=3600)
        await fresh.bootstrap()
        fresh._data.setdefault("groups", {})[origin] = {"seen_at": 1}
        nonce, _ = await fresh.issue_ephemeral_card(origin, _lobby_card(), "s")

        old._write_atomic(old._data)

        # Either instance may receive the interaction; both must honour it.
        await old.claim_ephemeral_click(origin, nonce, "go", "U1")
        await fresh.claim_ephemeral_click(origin, nonce, "go", "U1")
    asyncio.run(scenario())


def test_merging_never_resurrects_a_consumed_one_shot():
    """The writer's own view wins, so a spent button stays spent."""
    async def scenario():
        directory = Path(tempfile.mkdtemp())
        origin = "qq_official:GroupMessage:G1"
        card = ep.validate_card({
            "id": "d", "markdown": "# d", "one_shot": True,
            "rows": [[{"id": "go", "label": "go", "action_id": "demo.run"}]],
        })

        store = PanelStore(directory, callback_ttl_seconds=3600)
        await store.bootstrap()
        store._data.setdefault("groups", {})[origin] = {"seen_at": 1}
        nonce, _ = await store.issue_ephemeral_card(origin, card, "s")
        await store.claim_ephemeral_click(origin, nonce, "go", "U1")

        with pytest.raises(ep.EphemeralError):
            await store.claim_ephemeral_click(origin, nonce, "go", "U1")
    asyncio.run(scenario())
