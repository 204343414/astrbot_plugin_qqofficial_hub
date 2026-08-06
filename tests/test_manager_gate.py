"""Server-side enforcement of the ``group_manager`` button policy.

Before author.member_role existed in a usable form, this policy was enforced
by nothing at all. The code said QQ withheld the callback -- but
``permission.type=1`` governs how a button *renders*, and no documented
contract promises the click is suppressed. So the strictest-looking option in
the editor may well have been decorative, and nobody could tell.

These tests pin the gate closed and, just as importantly, pin the escape
hatches open so a group cannot lock out its own administrators.
"""
import asyncio
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

if "astrbot" not in sys.modules:
    _api = types.ModuleType("astrbot.api")
    _api.logger = SimpleNamespace(**{
        name: (lambda *a, **k: None)
        for name in ("debug", "info", "warning", "error", "exception")
    })
    _root = types.ModuleType("astrbot")
    _root.api = _api
    sys.modules["astrbot"] = _root
    sys.modules["astrbot.api"] = _api

from qqofficial_hub.identity import IdentityBook  # noqa: E402
from qqofficial_hub.store import PanelStore  # noqa: E402

ORIGIN = "qq_official:GroupMessage:G1"
ADMIN = "AAAA096E28B40FEDEB3320E5E8D7C2A7"
PLAIN = "BBBB6AB7A714145630DF8DEBD0CA9294"
STRANGER = "CCCC6AB7A714145630DF8DEBD0CA9294"


class _Hub:
    """Only the collaborators ``_clicker_is_manager`` actually reaches for."""

    def __init__(self, operators=(), astrbot_admins=()):
        self.store = PanelStore(Path(tempfile.mkdtemp()))
        self.identities = IdentityBook(self.store)
        self.operator_openids = set(operators)
        self._astrbot_admins = set(astrbot_admins)

    def _is_astrbot_admin_openid(self, openid, origin):
        return openid in self._astrbot_admins

    # Copied in behaviour from main.QQOfficialHubPlugin so the logic can be
    # tested without importing the whole AstrBot runtime.
    async def _clicker_is_manager(self, origin, member_openid):
        member = str(member_openid or "")
        if member and member in self.operator_openids:
            return True
        if self._is_astrbot_admin_openid(member, origin):
            return True
        try:
            if await self.identities.is_group_manager(origin, member):
                return True
        except Exception:
            return False
        return False


def _run(coro_factory, **kwargs):
    hub = _Hub(**kwargs)

    async def scenario():
        await hub.store.bootstrap()
        return await coro_factory(hub)

    return asyncio.run(scenario())


def test_an_admin_who_has_spoken_may_click():
    async def scenario(hub):
        await hub.identities.remember(ORIGIN, ADMIN, "阿管", role="admin")
        return await hub._clicker_is_manager(ORIGIN, ADMIN)

    assert _run(scenario) is True


def test_the_group_owner_may_click():
    async def scenario(hub):
        await hub.identities.remember(ORIGIN, ADMIN, "群主", role="owner")
        return await hub._clicker_is_manager(ORIGIN, ADMIN)

    assert _run(scenario) is True


def test_a_plain_member_may_not():
    async def scenario(hub):
        await hub.identities.remember(ORIGIN, PLAIN, "路人", role="member")
        return await hub._clicker_is_manager(ORIGIN, PLAIN)

    assert _run(scenario) is False


def test_an_unknown_clicker_is_refused_rather_than_assumed_harmless():
    """Fails closed. A permission check that fails open is not a check, and
    recovery costs one message in the group."""
    async def scenario(hub):
        return await hub._clicker_is_manager(ORIGIN, STRANGER)

    assert _run(scenario) is False


def test_a_hub_operator_is_allowed_even_with_no_known_role():
    """Otherwise a fresh restart could leave a group with nobody able to
    press the very buttons meant for its administrators."""
    async def scenario(hub):
        return await hub._clicker_is_manager(ORIGIN, STRANGER)

    assert _run(scenario, operators={STRANGER}) is True


def test_an_astrbot_admin_is_allowed_even_with_no_known_role():
    async def scenario(hub):
        return await hub._clicker_is_manager(ORIGIN, STRANGER)

    assert _run(scenario, astrbot_admins={STRANGER}) is True


def test_being_an_admin_elsewhere_does_not_help_here():
    other = "qq_official:GroupMessage:G2"

    async def scenario(hub):
        await hub.identities.remember(other, ADMIN, "阿管", role="owner")
        return await hub._clicker_is_manager(ORIGIN, ADMIN)

    assert _run(scenario) is False


def test_a_demoted_admin_loses_access_on_their_next_message():
    async def scenario(hub):
        await hub.identities.remember(ORIGIN, ADMIN, "阿管", role="admin")
        assert await hub._clicker_is_manager(ORIGIN, ADMIN) is True
        await hub.identities.remember(ORIGIN, ADMIN, "阿管", role="member")
        return await hub._clicker_is_manager(ORIGIN, ADMIN)

    assert _run(scenario) is False


def test_an_empty_openid_is_refused():
    async def scenario(hub):
        return await hub._clicker_is_manager(ORIGIN, "")

    assert _run(scenario) is False


def test_an_empty_openid_does_not_match_an_empty_operator_entry():
    """A blank line in the operator config must not become a wildcard."""
    async def scenario(hub):
        return await hub._clicker_is_manager(ORIGIN, "")

    assert _run(scenario, operators={""}) is False


def test_the_real_hub_logic_matches_what_is_tested_here():
    """This file reimplements _clicker_is_manager to avoid importing the
    AstrBot runtime, which makes it worth almost nothing if the real one
    drifts. Compare the essential decisions in the source instead.
    """
    import ast

    source = (Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8")
    tree = ast.parse(source)
    func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_clicker_is_manager"),
        None,
    )
    assert func is not None, "main.py 里找不到 _clicker_is_manager"
    body = ast.unparse(func)
    assert "operator_openids" in body, "必须放行 Hub 操作员"
    assert "_is_astrbot_admin_openid" in body, "必须放行 AstrBot 管理员"
    assert "is_group_manager" in body, "必须查 member_role"
    assert "return False" in body, "未知身份必须拒绝"


def test_the_callback_path_actually_calls_the_gate():
    """The gate is worthless if nothing invokes it -- which was the previous
    state of affairs, with a comment where the check should have been."""
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8")
    assert 'policy == "group_manager"' in source
    assert "_clicker_is_manager(origin, member)" in source
