"""Diagnostics must enumerate the real runtime, never a hard-coded list."""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from qqofficial_hub import diagnostics as diag
from qqofficial_hub.action_registry import ActionRegistry, ActionSpec
from qqofficial_hub.store import PanelStore


async def _cb(context, params):
    return 0


def _plugin():
    registry = ActionRegistry()
    store = PanelStore(Path(tempfile.mkdtemp()))
    plugin = SimpleNamespace(
        actions=registry,
        store=store,
        experimental_bridge=False,
        _card_providers={},
        get_action_catalog=lambda: registry.catalog(),
        send_ephemeral_card=lambda *a, **k: None,
        end_ephemeral_session=lambda *a, **k: None,
        register_card_provider=lambda *a, **k: None,
        unregister_card_provider=lambda *a, **k: None,
    )
    return plugin, registry, store


def test_no_hub_module_is_missing_from_the_report():
    """A diagnostics page that silently omits a module is worse than none.

    This is the guard that keeps HUB_MODULES honest as files are added.
    """
    listed = {r["name"] for r in diag.module_report()}
    on_disk = {
        p.stem for p in Path(diag.__file__).parent.glob("*.py")
        if p.stem not in ("__init__", "diagnostics")
    }
    assert on_disk == listed, (
        f"漏报: {on_disk - listed}；多报: {listed - on_disk}"
    )


def test_import_failures_are_reported_not_swallowed():
    """Modules needing astrbot fail to import outside AstrBot; that must show
    up as an explicit error rather than a silent pass."""
    rows = {r["name"]: r for r in diag.module_report()}
    pure = rows["ephemeral"]          # no astrbot dependency
    assert pure["ok"] and pure["symbols"] > 0
    for row in rows.values():
        assert row["ok"] or row["error"], "失败必须带原因"


def test_api_report_flags_a_missing_method():
    plugin, _, _ = _plugin()
    assert all(r["present"] for r in diag.api_report(plugin))
    del plugin.send_ephemeral_card
    missing = [r for r in diag.api_report(plugin) if not r["present"]]
    assert [r["name"] for r in missing] == ["send_ephemeral_card"]


def test_external_plugin_actions_are_marked_external():
    plugin, registry, _ = _plugin()
    registry.register(ActionSpec(
        action_id="tictactoe.move", title="落子", description="",
        owner="astrbot_plugin_tictactoe", default_permission="everyone",
        callback=_cb,
    ))
    report = diag.action_report(plugin)
    owners = {o["owner"]: o for o in report["owners"]}
    assert owners["astrbot_plugin_tictactoe"]["external"] is True
    assert report["total"] == 1


def test_unregistering_removes_it_from_the_report():
    """Hot-unplug must be visible on refresh."""
    plugin, registry, _ = _plugin()
    registry.register(ActionSpec(
        action_id="x.y", title="t", description="", owner="other",
        default_permission="everyone", callback=_cb,
    ))
    assert diag.action_report(plugin)["total"] == 1
    registry.unregister_owner("other")
    assert diag.action_report(plugin)["total"] == 0


def test_provider_report_lists_registered_builders():
    plugin, _, _ = _plugin()
    plugin._card_providers = {"menu": _cb}
    rows = diag.provider_report(plugin)
    assert rows[0]["card_id"] == "menu"
    assert rows[0]["external"] is True


def test_build_report_survives_a_broken_section():
    plugin, _, store = _plugin()
    plugin.get_action_catalog = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

    async def scenario():
        await store.bootstrap()
        report = await diag.build_report(plugin)
        assert "boom" in report["actions"]["error"]
        assert report["modules"], "其它区块仍需可用"
    asyncio.run(scenario())


def test_storage_counts_live_versus_expired_cards():
    async def scenario():
        _, _, store = _plugin()
        await store.bootstrap()
        from qqofficial_hub.ephemeral import validate_card
        card = validate_card({"markdown": "x", "rows": [[
            {"id": "a", "label": "A", "action_id": "act"}]]})
        await store.issue_ephemeral_card("qq_official:GroupMessage:G1", card)
        report = await diag.storage_report(store)
        assert report["ephemeral_cards_live"] == 1
        assert report["ephemeral_sessions_live"] == 1
    asyncio.run(scenario())
