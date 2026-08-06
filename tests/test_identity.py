"""Identity book: names come only from messages, and gate button access."""
import asyncio
from types import SimpleNamespace

import pytest
import tempfile
import time
from pathlib import Path

from qqofficial_hub import identity
from qqofficial_hub.store import PanelStore

ORIGIN = "qq_official:GroupMessage:G1"
ALICE = "15CB6AB7A714145630DF8DEBD0CA9294"
BOB = "BE4A096E28B40FEDEB3320E5E8D7C2A7"


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

    Checked statically: importing ephemeral_routes needs AstrBot, absent here.
    """
    from pathlib import Path
    source = Path(__file__).parents[1].joinpath(
        "qqofficial_hub/ephemeral_routes.py").read_text("utf-8")
    assert "clicker_header: str | None = None" in source, (
        "默认应为 None 以便自动推导，而不是空字符串"
    )
    assert "_clicker_header(origin, initiator_openid)" in source
    assert "if clicker_header is None:" in source, "显式传 '' 应能抑制顶部行"


def test_the_module_documents_where_identity_data_actually_comes_from():
    """This docstring used to assert QQ never sends a group nickname.

    That was true of botpy 1.2.1's parser and false of QQ itself: the
    documented GROUP_AT_MESSAGE_CREATE payload carries ``username`` *and*
    ``member_role``, and AstrBot patches the class to read the former. The
    library's blind spot got written down as a property of the platform,
    which is the kind of mistake that stops anyone re-checking for years.

    What must stay documented is the real constraint: a click carries no
    identity at all, so a click can only be attributed by remembering an
    earlier message.
    """
    doc = identity.__doc__ or ""
    assert "member_role" in doc
    assert "INTERACTION_CREATE" in doc
    assert "never sends a nickname in the group scene" not in doc, (
        "旧结论已被官方文档推翻，不要留在文档里"
    )


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


def test_raw_payload_fallback_finds_a_nickname():
    """AstrBot reads author.username; older/newer botpy may name it otherwise.

    The bot's own log prints "name/id" only when get_sender_name() is non-empty,
    so if a name shows up there it must be reachable from the payload too.
    """
    import ast
    from pathlib import Path
    source = Path(__file__).parents[1].joinpath("main.py").read_text("utf-8")
    tree = ast.parse(source)
    func = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_nickname_from_raw"
    )
    func.decorator_list = []
    ns: dict = {"AstrMessageEvent": object}
    exec(compile(ast.Module([func], []), "<x>", "exec"), ns)
    extract = ns["_nickname_from_raw"]

    class Author:
        nickname = "小明"

    class Raw:
        author = Author()

    class Msg:
        raw_message = Raw()

    class Event:
        message_obj = Msg()

    assert extract(Event()) == "小明"

    class Empty:
        message_obj = None

    assert extract(Empty()) == ""


# --- group roles ------------------------------------------------------------
#
# author.member_role is documented on GROUP_AT_MESSAGE_CREATE but parsed by
# neither botpy 1.2.1 nor AstrBot's patched subclass, so it has to be read off
# the preserved raw payload. These tests are built from the payload shapes in
# the official docs rather than from what the objects happen to expose.

def _event(author: dict, with_raw: bool = True):
    """Mimic AstrBot's event object closely enough to exercise extraction."""
    class _Author:
        def __init__(self, data):
            # Exactly what AstrBot's PatchedGroupMessage._User parses --
            # note the absence of member_role, which is the whole point.
            self.id = data.get("id")
            self.username = data.get("username")
            self.member_openid = data.get("member_openid")

    raw_message = SimpleNamespace(author=_Author(author))
    if with_raw:
        raw_message.raw_data = {"author": author}
    return SimpleNamespace(message_obj=SimpleNamespace(raw_message=raw_message))


def test_the_role_is_read_from_the_documented_payload():
    """Example 3 in the official docs, verbatim."""
    event = _event({
        "id": "D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1",
        "member_openid": "D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1",
        "member_role": "owner",
        "username": "小华",
        "bot": False,
    })
    assert identity.role_from_event(event) == identity.ROLE_OWNER


def test_every_documented_role_value_is_recognised():
    for role in ("member", "admin", "owner"):
        assert identity.role_from_event(_event({"member_role": role})) == role


def test_a_missing_role_is_empty_rather_than_a_guess():
    """Interaction events carry no author at all. Inventing 'member' would
    make 'definitely not an admin' indistinguishable from 'no idea yet'."""
    assert identity.role_from_event(_event({"member_openid": "X"})) == ""
    assert identity.role_from_event(SimpleNamespace()) == ""


def test_an_unexpected_role_value_is_rejected_not_stored():
    assert identity.role_from_event(_event({"member_role": "superadmin"})) == ""


def test_the_role_is_not_read_off_the_parsed_object_alone():
    """botpy 1.2.1 does not define member_role on GroupMessage._User, and
    AstrBot's patch does not add it. Code reading the attribute would get
    None forever -- which looks exactly like 'nobody is an admin'."""
    event = _event({"member_role": "admin"}, with_raw=False)
    assert not hasattr(event.message_obj.raw_message.author, "member_role")
    assert identity.role_from_event(event) == ""


def test_the_owner_counts_as_a_manager():
    """QQ reports the owner as 'owner', never 'admin'. Checking only for
    'admin' would lock out the one person who most obviously qualifies."""
    assert identity.ROLE_OWNER in identity.MANAGER_ROLES
    assert identity.ROLE_ADMIN in identity.MANAGER_ROLES
    assert identity.ROLE_MEMBER not in identity.MANAGER_ROLES


def test_a_remembered_role_can_be_read_back():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明", role="admin")
        assert await book.role_of(ORIGIN, ALICE) == "admin"
        assert await book.is_group_manager(ORIGIN, ALICE) is True
    asyncio.run(scenario())


def test_a_plain_member_is_not_a_manager():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明", role="member")
        assert await book.is_group_manager(ORIGIN, ALICE) is False
    asyncio.run(scenario())


def test_someone_never_seen_has_no_role_and_is_no_manager():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        assert await book.role_of(ORIGIN, BOB) == ""
        assert await book.is_group_manager(ORIGIN, BOB) is False
    asyncio.run(scenario())


def test_a_later_message_without_a_role_does_not_demote_an_admin():
    """The dangerous case. Roles arrive only on messages, and several call
    sites pass none; treating that as a demotion would strip an admin of
    their permissions the moment anything else touched their record.
    """
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明", role="admin")
        await book.remember(ORIGIN, ALICE, "小明")        # no role this time
        assert await book.is_group_manager(ORIGIN, ALICE) is True
    asyncio.run(scenario())


def test_a_real_demotion_is_recorded():
    """The flip side: when QQ *does* say 'member', that must stick, or a
    demoted admin would keep their buttons forever."""
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明", role="owner")
        await book.remember(ORIGIN, ALICE, "小明", role="member")
        assert await book.is_group_manager(ORIGIN, ALICE) is False
    asyncio.run(scenario())


def test_a_promotion_is_picked_up_without_clearing_the_name():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.set_name(ORIGIN, ALICE, "小明")
        await book.remember(ORIGIN, ALICE, "", role="admin")
        assert await book.name_of(ORIGIN, ALICE) == "小明"
        assert await book.is_group_manager(ORIGIN, ALICE) is True
    asyncio.run(scenario())


def test_a_garbage_role_is_never_stored():
    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明", role="root")
        assert await book.role_of(ORIGIN, ALICE) == ""
    asyncio.run(scenario())


def test_roles_do_not_leak_between_groups():
    """An admin of one group is an ordinary member of another; the book is
    keyed per origin precisely so that stays true."""
    other = "qq_official:GroupMessage:G2"

    async def scenario():
        book, store = _book()
        await store.bootstrap()
        await book.remember(ORIGIN, ALICE, "小明", role="owner")
        assert await book.is_group_manager(other, ALICE) is False
    asyncio.run(scenario())
