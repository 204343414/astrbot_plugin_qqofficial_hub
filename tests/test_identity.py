"""Identity book: names come only from messages, and gate button access."""
import asyncio

import pytest
import tempfile
import time
from pathlib import Path

from qqofficial_hub import identity
from qqofficial_hub.store import PanelStore

ORIGIN = "qq_official:GroupMessage:G1"
ALICE = "15CB6AB7A714145630DF8DEBD0CA9294"


def _book():
    store = PanelStore(Path(tempfile.mkdtemp()))
    return identity.IdentityBook(store), store


# --- name hygiene -----------------------------------------------------------

def test_nickname_is_stripped_of_markdown_and_newlines():
    dirty = "小明\n<script>[x](y)`*_|#!"
    cleaned = identity.normalize_name(dirty)
    # newlines collapse to a space; markdown/tag characters are dropped
    assert cleaned == "小明 scriptxy"
    assert "\n" not in cleaned
    assert not set(cleaned) & set("<>[]()`*_|#!")


def test_long_nickname_is_truncated():
    assert len(identity.normalize_name("名" * 100)) == 32


def test_openid_is_never_shown_as_a_name():
    assert identity.looks_like_openid(ALICE)
    label = identity.display_label(ALICE, ALICE)
    assert ALICE not in label, "不得把 OpenID 当昵称显示"
    assert any(word in label for word in identity.ANONYMOUS_WORDS)


def test_display_label_falls_back_to_a_friendly_placeholder():
    """QQ never sends nicknames in groups, so this is the common path."""
    label = identity.display_label("", ALICE)
    assert ALICE not in label
    assert label.endswith("9294")
    assert any(word in label for word in identity.ANONYMOUS_WORDS)


def test_placeholder_is_stable_and_distinguishes_people():
    a = identity.display_label("", ALICE)
    assert a == identity.display_label("", ALICE), "同一人应始终同一称呼"
    other = "BE4A096E28B40FEDEB3320E5E8D7C2A7"
    assert identity.display_label("", other) != a, "不同人应可区分"


def test_real_name_is_used_when_known():
    assert identity.display_label("小明", ALICE) == "小明"


# --- gate -------------------------------------------------------------------

def test_stranger_is_unknown_until_they_speak():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        assert not await book.is_known(ORIGIN, ALICE)
        await book.remember(ORIGIN, ALICE, "小明")
        assert await book.is_known(ORIGIN, ALICE)
    asyncio.run(scenario())


def test_speaking_without_a_nickname_still_counts_as_known():
    """Some events carry an empty username; the person still spoke."""
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "")
        assert await book.is_known(ORIGIN, ALICE)
        label = await book.label_for(ORIGIN, ALICE)
        assert ALICE not in label and label.endswith("9294")
    asyncio.run(scenario())


def test_identity_is_scoped_per_group():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明")
        other = "qq_official:GroupMessage:G2"
        assert not await book.is_known(other, ALICE), "不得跨群继承身份"
    asyncio.run(scenario())


# --- rename -----------------------------------------------------------------

def test_rename_is_picked_up_on_the_next_message():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明")
        assert await book.name_of(ORIGIN, ALICE) == "小明"
        changed = await book.remember(ORIGIN, ALICE, "大明")
        assert changed is True
        assert await book.name_of(ORIGIN, ALICE) == "大明"
    asyncio.run(scenario())


def test_repeating_the_same_name_reports_no_change():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明")
        assert await book.remember(ORIGIN, ALICE, "小明") is False
    asyncio.run(scenario())


def test_expired_identity_is_forgotten():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明")
        raw = store._data["identities"][identity.IdentityBook.key(ORIGIN, ALICE)]
        raw["expires_at"] = int(time.time()) - 1
        assert not await book.is_known(ORIGIN, ALICE)
    asyncio.run(scenario())


def test_identity_survives_reload():
    async def scenario():
        d = Path(tempfile.mkdtemp())
        store = PanelStore(d)
        await store.bootstrap()
        await identity.IdentityBook(store).remember(ORIGIN, ALICE, "小明")
        again = PanelStore(d)
        await again.bootstrap()
        assert await identity.IdentityBook(again).name_of(ORIGIN, ALICE) == "小明"
    asyncio.run(scenario())


def test_header_is_derived_automatically_when_not_supplied():
    """A game plugin only passes initiator_openid; the header must be free.

    Requiring every caller to build the header by hand is why the tic-tac-toe
    board never showed one.
    """
    import inspect
    from qqofficial_hub.ephemeral_routes import EphemeralCardMixin

    sig = inspect.signature(EphemeralCardMixin.send_ephemeral_card)
    default = sig.parameters["clicker_header"].default
    assert default is None, "默认应为 None 以便自动推导，而不是空字符串"
    source = inspect.getsource(EphemeralCardMixin.send_ephemeral_card)
    assert "_clicker_header(origin, initiator_openid)" in source


def test_empty_header_can_still_be_forced():
    import inspect
    from qqofficial_hub.ephemeral_routes import EphemeralCardMixin
    source = inspect.getsource(EphemeralCardMixin.send_ephemeral_card)
    assert "if clicker_header is None:" in source, "显式传 '' 应能抑制顶部行"


def test_group_scene_provides_no_nickname_by_design():
    """Documented reality check, verified against botpy 1.2.1.

    GroupMessage._User exposes only ``member_openid`` -- no ``username`` -- and
    every member-lookup endpoint in the API is guild-only. Any code that expects
    a group nickname from QQ is built on a false premise.
    """
    doc = identity.__doc__ or ""
    assert "never sends a nickname in the group scene" in doc


# --- self-declared nicknames -------------------------------------------------

def test_self_declared_name_overrides_the_placeholder():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "")          # what QQ actually gives
        assert (await book.label_for(ORIGIN, ALICE)) != "小明"
        assert await book.set_name(ORIGIN, ALICE, " 小明 ") == "小明"
        assert await book.label_for(ORIGIN, ALICE) == "小明"
    asyncio.run(scenario())


def test_self_declared_name_can_be_cleared():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.set_name(ORIGIN, ALICE, "小明")
        assert await book.set_name(ORIGIN, ALICE, "") == ""
        label = await book.label_for(ORIGIN, ALICE)
        assert label and ALICE not in label
    asyncio.run(scenario())


def test_cannot_impersonate_an_openid():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        with pytest.raises(ValueError):
            await book.set_name(ORIGIN, ALICE, ALICE)
    asyncio.run(scenario())


def test_plain_message_never_wipes_a_declared_name():
    """QQ sends an empty nickname on every group message."""
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.set_name(ORIGIN, ALICE, "小明")
        await book.remember(ORIGIN, ALICE, "")
        assert await book.name_of(ORIGIN, ALICE) == "小明"
    asyncio.run(scenario())
