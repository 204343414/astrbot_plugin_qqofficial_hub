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
