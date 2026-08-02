"""Identity book: names come only from messages, and gate button access."""
import asyncio
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
    assert label.startswith("未知用户")


def test_display_label_falls_back_to_a_short_suffix():
    label = identity.display_label("", ALICE)
    assert label == "未知用户…9294"


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
        assert (await book.label_for(ORIGIN, ALICE)).startswith("未知用户")
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
