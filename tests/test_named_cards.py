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


def test_editor_never_uses_browser_dialogs():
    """AstrBot hosts plugin pages in a sandboxed iframe where prompt() and
    confirm() are disabled. They fail *silently* -- the function exists and
    returns undefined -- so a button using them simply does nothing.
    """
    import re
    js = Path(__file__).parents[1].joinpath("pages/panels/app.js").read_text("utf-8")
    code = "\n".join(
        line for line in js.splitlines() if not line.strip().startswith("//")
    )
    for call in (r"\bprompt\s*\(", r"\bconfirm\s*\("):
        assert not re.search(call, code), f"页面不得调用浏览器弹窗: {call}"


def test_editor_has_inline_card_controls():
    root = Path(__file__).parents[1] / "pages" / "panels"
    html = (root / "index.html").read_text(encoding="utf-8")
    for element in ("card-new-id", "card-new", "card-delete",
                    "card-delete-confirm", "card-delete-cancel"):
        assert f'id="{element}"' in html, f"缺少 {element}"


def test_command_match_survives_the_stripped_wake_prefix():
    """AstrBot's WakingCheck removes the wake prefix before plugins run.

    ``event.message_str`` is therefore "小游戏", not "/小游戏". Matching on a
    leading slash silently found nothing -- no error, no log, no reply.
    """
    source = Path(__file__).parents[1].joinpath("main.py").read_text("utf-8")
    handler = source[source.index("async def open_named_card_by_command"):]
    handler = handler[: handler.index("\n    @filter.")]
    assert 'if not text.startswith("/")' not in handler, "不得依赖前导斜杠"
    assert 'lstrip("/")' in handler, "应同时接受带/不带斜杠两种形式"
    assert "message_obj" in handler, "还应回退检查未被剥前缀的原始文本"


def test_command_lookup_ignores_a_leading_slash():
    async def scenario():
        store = _store()
        await store.bootstrap()
        await store.save_card("game", empty_panel(), "小游戏")
        for probe in ("小游戏", "/小游戏", "  /小游戏  "):
            found = await store.find_card_by_command(probe)
            assert found and found["id"] == "game", probe
    asyncio.run(scenario())


def test_editor_layout_splits_evenly_and_folds_long_sections():
    root = Path(__file__).parents[1] / "pages" / "panels"
    css = (root / "styles.css").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "grid-template-columns:minmax(0,1fr) minmax(0,1fr)" in css, "左右应各占一半"
    assert "max-height:calc(100vh - 150px)" in css, "两栏应各自滚动而非把页面拉长"
    assert html.count('<details class="fold">') >= 4, "长表单应折叠"


def test_named_card_buttons_validate_against_their_own_revision():
    """A named card keeps its own revision counter.

    Validating it against the hub panel's revision made every button on a
    freshly opened named card report "卡片不存在或已过期".
    """
    async def scenario():
        store = _store()
        await store.bootstrap()
        origin = "qq_official:GroupMessage:G1"
        await store.save_card("game", empty_panel(), "小游戏")
        card = await store.save_card("game", empty_panel(), "小游戏")
        panel = card["panel"]
        assert panel["revision"] != (await store.bootstrap())[
            "templates"]["default_panel"]["revision"], "前提：两者版本不同"
        button_id = panel["rows"][0][0]["id"]
        nonce = await store.issue_panel_card(origin, panel, card_id="game")
        assert await store.get_issued_button_context(origin, nonce, button_id)
    asyncio.run(scenario())


def test_editing_a_named_card_expires_its_old_buttons():
    """Bumping a card's revision must still invalidate previously sent cards."""
    async def scenario():
        store = _store()
        await store.bootstrap()
        origin = "qq_official:GroupMessage:G1"
        card = await store.save_card("game", empty_panel())
        button_id = card["panel"]["rows"][0][0]["id"]
        nonce = await store.issue_panel_card(origin, card["panel"], card_id="game")
        await store.save_card("game", empty_panel())     # edit -> revision + 1
        assert await store.get_issued_button_context(origin, nonce, button_id) is None
    asyncio.run(scenario())


def test_hub_panel_buttons_are_unaffected():
    async def scenario():
        store = _store()
        await store.bootstrap()
        origin = "qq_official:GroupMessage:G1"
        snapshot = await store.bootstrap()
        panel = snapshot["templates"]["default_panel"]
        button_id = panel["rows"][0][0]["id"]
        nonce = await store.issue_panel_card(origin, panel)
        assert await store.get_issued_button_context(origin, nonce, button_id)
    asyncio.run(scenario())


def test_synthetic_msg_id_is_never_sent_to_qq():
    """HubSyntheticCommandEvent fabricates a message id for AstrBot's benefit.

    Forwarding it to QQ produces "请求参数msg_id无效或越权", which surfaced as
    a bare "发牌失败" for game plugins that simply passed the event's id along.
    """
    from qqofficial_hub.passive_reply import real_msg_id
    assert real_msg_id("hub-interaction:abc-123") == ""
    assert real_msg_id("") == ""
    assert real_msg_id(None) == ""
    assert real_msg_id("  ") == ""
    real = "ROBOT1.0_abcDEF"
    assert real_msg_id(real) == real


def test_send_paths_filter_the_msg_id():
    root = Path(__file__).parents[1]
    for name in ("main.py", "qqofficial_hub/ephemeral_routes.py"):
        source = root.joinpath(name).read_text(encoding="utf-8")
        assert "real_msg_id(msg_id)" in source, f"{name} 未过滤合成 msg_id"


def test_editor_can_refresh_the_action_catalog():
    """Plugins register Actions after page load; a manual re-pull is needed."""
    root = Path(__file__).parents[1] / "pages" / "panels"
    assert 'id="reload-catalog"' in (root / "index.html").read_text("utf-8")
    assert "reload-catalog" in (root / "app.js").read_text("utf-8")
