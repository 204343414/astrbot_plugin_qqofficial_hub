import asyncio
from types import SimpleNamespace

import pytest

from qqofficial_hub.action_registry import (
    ActionContext,
    ActionRegistry,
    ActionSpec,
)


async def callback(context, params):
    return 0 if params.get("ok") else 1


def spec(owner="plugin-a"):
    return ActionSpec(
        action_id="demo.run",
        title="运行Demo",
        description="测试动作",
        owner=owner,
        default_permission="everyone",
        callback=callback,
    )


def test_registry_rejects_cross_owner_collision_and_executes_params():
    async def scenario():
        registry = ActionRegistry()
        registry.register(spec())
        with pytest.raises(ValueError, match="already owned"):
            registry.register(spec(owner="plugin-b"))
        context = ActionContext(
            client=SimpleNamespace(), interaction=SimpleNamespace(),
            origin="p:GroupMessage:g", group_openid="g", member_openid="u",
        )
        assert await registry.execute("demo.run", context, {"ok": True}) == 0
        assert await registry.execute("demo.run", context, {"ok": False}) == 1
        assert await registry.execute("missing", context, {}) == 1
    asyncio.run(scenario())


def test_unregister_owner_removes_only_its_actions():
    registry = ActionRegistry()
    registry.register(spec())
    registry.unregister_owner("plugin-a")
    assert not registry.contains("demo.run")


# --- surviving a Hub hot-reload ---------------------------------------------
#
# AstrBot's plugin update path purges every ``data.plugins.<hub>.*`` module and
# re-imports it (star_manager._purge_modules / _cleanup_plugin_state). The new
# import produces a *different* ActionRegistry class object, so the registry
# already parked on ``builtins`` fails ``isinstance``. That is exactly what made
# third-party Actions disappear after updating the Hub.

def _reimport_action_registry():
    """Re-import the module the way AstrBot's reload does, and return it."""
    import importlib
    import sys

    saved = sys.modules.pop("qqofficial_hub.action_registry")
    try:
        return importlib.import_module("qqofficial_hub.action_registry")
    finally:
        sys.modules["qqofficial_hub.action_registry"] = saved


def test_third_party_actions_survive_a_hub_module_reload():
    import builtins

    from qqofficial_hub import action_registry as before

    key = before._REGISTRY_KEY
    previous = getattr(builtins, key, None)
    try:
        setattr(builtins, key, before.ActionRegistry())
        before.get_action_registry().register(before.ActionSpec(
            action_id="tictactoe.lobby", title="井字棋", description="",
            owner="astrbot_plugin_tictactoe", default_permission="everyone",
            callback=callback,
        ))

        after = _reimport_action_registry()
        assert after.ActionRegistry is not before.ActionRegistry, (
            "the reload simulation did not actually produce a new class"
        )
        ids = [item["id"] for item in after.get_action_registry().catalog()]
        assert "tictactoe.lobby" in ids
    finally:
        if previous is None:
            delattr(builtins, key)
        else:
            setattr(builtins, key, previous)


def test_registry_with_a_different_protocol_is_replaced():
    """A genuinely incompatible leftover must not be adopted."""
    from qqofficial_hub import action_registry as ar

    class Ancient:
        protocol = -1

    assert not ar.is_compatible_registry(Ancient())
    assert not ar.is_compatible_registry(None)
    assert not ar.is_compatible_registry(object())
    assert ar.is_compatible_registry(ar.ActionRegistry())
