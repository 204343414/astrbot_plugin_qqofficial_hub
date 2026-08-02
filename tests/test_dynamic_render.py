"""Placeholder resolution at send time."""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from qqofficial_hub import push_status as ps
from qqofficial_hub.store import PanelStore


class _Renderer:
    """Mirrors main._render_dynamic_markdown without importing astrbot."""

    def __init__(self, store):
        self.store = store
        self.push_lamps = {}
        self.push_templates = {}

    async def render(self, markdown, origin):
        if "{{" not in markdown:
            return markdown
        if ps.has_placeholder(markdown):
            markdown = ps.render(
                markdown, await self.store.get_push_state(origin),
                lamps=self.push_lamps, templates=self.push_templates,
            )
        if "{{group_openid_short}}" in markdown:
            markdown = markdown.replace("{{group_openid_short}}", origin.split(":", 2)[-1][-8:])
        return markdown


def _renderer():
    store = PanelStore(Path(tempfile.mkdtemp()))
    return _Renderer(store), store


def test_card_without_tokens_is_returned_untouched():
    async def scenario():
        r, store = _renderer()
        await store.bootstrap()
        md = "# 桌游房间\n**回合 3**\n- 玩家A\n- 玩家B"
        assert await r.render(md, "p:GroupMessage:ABCDEF123456") == md
    asyncio.run(scenario())


def test_push_tokens_resolve_by_stored_state():
    async def scenario():
        r, store = _renderer()
        await store.bootstrap()
        origin = "p:GroupMessage:ABCDEF123456"
        md = "{{push_lamp}} {{push_status}}"
        assert await r.render(md, origin) == "⚪ 当前群主动消息推送状态未知"
        await store.set_push_state(origin, ps.GRANTED, "authorize")
        assert await r.render(md, origin) == "🟢 当前群已开启主动消息推送功能"
    asyncio.run(scenario())


def test_group_token_resolves_to_last_eight_chars():
    async def scenario():
        r, store = _renderer()
        await store.bootstrap()
        out = await r.render("房间 {{group_openid_short}}", "p:GroupMessage:ABCDEF123456")
        assert out == "房间 F123456".replace("F123456", "EF123456")
    asyncio.run(scenario())


def test_no_token_left_unresolved_for_catalog_snippets():
    async def scenario():
        from qqofficial_hub.snippets import SNIPPETS
        r, store = _renderer()
        await store.bootstrap()
        for item in SNIPPETS:
            out = await r.render(item.snippet, "p:GroupMessage:ABCDEF123456")
            assert "{{" not in out, f"{item.id} unresolved"
    asyncio.run(scenario())
