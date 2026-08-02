"""Named cards: a library of panels addressed by id, optionally by command."""
import asyncio
import tempfile
from pathlib import Path

import pytest

from qqofficial_hub import named_cards as nc
from qqofficial_hub.store import PanelStore, empty_panel


def _store():
    return PanelStore(Path(tempfile.mkdtemp()))


# --- id / command validation ------------------------------------------------

def test_card_id_rejects_unsafe_characters():
    for bad in ("", "a/b", "有中文", "x" * 41, "a b"):
        with pytest.raises(ValueError):
            nc.validate_card_id(bad)
    assert nc.validate_card_id(" game_1 ") == "game_1"


def test_command_is_normalised_and_bounded():
    assert nc.validate_command("/小游戏") == "小游戏"
    assert nc.validate_command("") == ""
    for bad in ("a b", "x" * 33, "qqhub"):
        with pytest.raises(ValueError):
            nc.validate_command(bad)


def test_reserved_commands_are_refused():
    with pytest.raises(ValueError, match="保留指令"):
        nc.validate_command("qqhub")


# --- clash detection against AstrBot ---------------------------------------

def test_detects_clash_with_a_registered_command():
    catalog = [{"command": "/井字棋", "aliases": ["/tictactoe"]}]
    assert nc.conflicts_with_astrbot("井字棋", catalog) == "/井字棋"
    assert nc.conflicts_with_astrbot("tictactoe", catalog) == "/井字棋", "别名也要拦"
    assert nc.conflicts_with_astrbot("自由的名字", catalog) == ""


def test_clash_check_is_case_insensitive():
    assert nc.conflicts_with_astrbot("TicTacToe", [{"command": "/tictactoe"}])


# --- storage ----------------------------------------------------------------

def test_create_list_and_delete():
    async def scenario():
        store = _store()
        await store.bootstrap()
        await store.save_card("game", empty_panel(), "小游戏")
        cards = await store.list_cards()
        assert [c["id"] for c in cards] == ["game"]
        assert cards[0]["command"] == "小游戏"
        assert await store.delete_card("game") is True
        assert await store.list_cards() == []
        assert await store.delete_card("game") is False
    asyncio.run(scenario())


def test_command_can_open_a_card():
    async def scenario():
        store = _store()
        await store.bootstrap()
        await store.save_card("game", empty_panel(), "小游戏")
        found = await store.find_card_by_command("/小游戏")
        assert found and found["id"] == "game"
        assert await store.find_card_by_command("不存在") is None
    asyncio.run(scenario())


def test_two_cards_cannot_share_a_command():
    async def scenario():
        store = _store()
        await store.bootstrap()
        await store.save_card("a", empty_panel(), "菜单")
        with pytest.raises(ValueError, match="已被卡片"):
            await store.save_card("b", empty_panel(), "菜单")
        # re-saving the same card keeps its own command
        await store.save_card("a", empty_panel(), "菜单")
    asyncio.run(scenario())


def test_revision_increments_so_stale_callbacks_expire():
    async def scenario():
        store = _store()
        await store.bootstrap()
        first = await store.save_card("game", empty_panel())
        second = await store.save_card("game", empty_panel())
        assert second["panel"]["revision"] > first["panel"]["revision"]
    asyncio.run(scenario())


def test_cards_survive_reload():
    async def scenario():
        d = Path(tempfile.mkdtemp())
        store = PanelStore(d)
        await store.bootstrap()
        await store.save_card("game", empty_panel(), "小游戏")
        again = PanelStore(d)
        await again.bootstrap()
        assert (await again.get_card("game"))["command"] == "小游戏"
    asyncio.run(scenario())


def test_card_count_is_capped():
    async def scenario():
        store = _store()
        await store.bootstrap()
        for i in range(nc.MAX_CARDS):
            await store.save_card(f"c{i}", empty_panel())
        with pytest.raises(ValueError, match="最多"):
            await store.save_card("overflow", empty_panel())
    asyncio.run(scenario())
