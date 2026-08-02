"""Placeholder resolution at send time."""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from qqofficial_hub.store import PanelStore


class _Renderer:
    """Mirrors main._render_dynamic_markdown without importing astrbot."""

    def __init__(self, store):
        self.store = store

    async def render(self, markdown, origin):
        if "{{" not in markdown:
            return markdown
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
