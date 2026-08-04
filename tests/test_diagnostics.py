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
        send_media_message=lambda *a, **k: None,
        publish_image=lambda *a, **k: "",
        image_host_ready=lambda: False,
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


def test_image_host_status_explains_what_to_fix():
    """Enabled / configured / running fail for different reasons and need
    different fixes; one boolean would hide which."""
    from types import SimpleNamespace

    off = SimpleNamespace(image_host_enabled=False, image_host=SimpleNamespace(
        status=lambda: {"configured": False, "running": False, "port": 9527}))
    assert "未开启" in diag.image_host_report(off)["hint"]

    no_url = SimpleNamespace(image_host_enabled=True, image_host=SimpleNamespace(
        status=lambda: {"configured": False, "running": False, "port": 9527}))
    assert "base_url" in diag.image_host_report(no_url)["hint"]

    not_listening = SimpleNamespace(
        image_host_enabled=True, image_host=SimpleNamespace(
            status=lambda: {"configured": True, "running": False, "port": 9527}))
    assert "9527" in diag.image_host_report(not_listening)["hint"]

    ready = SimpleNamespace(image_host_enabled=True, image_host=SimpleNamespace(
        status=lambda: {"configured": True, "running": True, "port": 9527}))
    assert diag.image_host_report(ready)["hint"] == "就绪"


def test_a_missing_image_host_does_not_crash_the_report():
    from types import SimpleNamespace

    assert diag.image_host_report(SimpleNamespace())["enabled"] is False


# --- chat-facing formatting -------------------------------------------------
#
# Diagnostics used to exist only as a WebUI drawer, but the moment you need
# them you are in the group, not the dashboard. QQ Official charges per reply,
# so the whole report has to survive being squeezed into one message without
# losing the part that says what is broken.

def _report(**over):
    base = {
        "modules": [{"name": "store", "ok": True, "error": "", "symbols": 9}],
        "api": [{"name": "publish_image", "present": True, "kind": "method"}],
        "actions": {"owners": [], "total": 3, "error": ""},
        "providers": [],
        "bridge": {"enabled": True, "installed": True},
        "image_host": {"hint": "就绪", "base_url": "https://x.trycloudflare.com",
                       "stored_images": 1, "hits": 0},
        "storage": {"observed_groups": 1, "ephemeral_cards_live": 2,
                    "ephemeral_sessions_live": 1},
        "identities": {"seen": 4, "named": 2},
        "healthy": True,
    }
    base.update(over)
    return base


def test_the_whole_report_fits_in_one_message():
    text = diag.format_report(_report())
    assert len(text) < 500, "自检必须一条消息发完，QQ 官方回复配额按条计"
    assert text.startswith("✅")


def test_a_broken_module_is_named_with_its_error():
    """A summary that hides the failure is worse than no summary: the user
    would read '通过' and keep looking in the wrong place."""
    text = diag.format_report(_report(
        modules=[
            {"name": "store", "ok": True, "error": "", "symbols": 9},
            {"name": "image_host", "ok": False,
             "error": "NameError: name 'image_host' is not defined"},
        ],
        healthy=False,
    ))
    assert text.startswith("⚠️")
    assert "image_host" in text and "NameError" in text


def test_a_missing_api_surface_is_listed_by_name():
    text = diag.format_report(_report(
        api=[{"name": "publish_image", "present": False, "kind": "missing"}],
        healthy=False,
    ))
    assert "publish_image" in text


def test_an_unfetched_image_host_says_so_rather_than_claiming_success():
    """'配置正确' and '真的有人来取过图' are different claims.

    Everything local can be green while Tencent still cannot reach the
    tunnel, so the report must not let the first imply the second.
    """
    text = diag.format_report(_report())
    assert "尚未被抓取" in text

    fetched = diag.format_report(_report(
        image_host={"hint": "就绪", "base_url": "https://x.trycloudflare.com",
                    "stored_images": 1, "hits": 5},
    ))
    assert "被抓取 5 次" in fetched


def test_external_plugins_are_visible_because_that_is_the_usual_question():
    text = diag.format_report(_report(actions={
        "owners": [
            {"owner": "astrbot_plugin_qqofficial_hub", "external": False,
             "actions": [{"id": "hub.refresh", "title": "刷新"}]},
            {"owner": "astrbot_plugin_tictactoe", "external": True,
             "actions": [{"id": "ttt.move", "title": "落子"},
                         {"id": "ttt.lobby", "title": "大厅"}]},
        ],
        "total": 3, "error": "",
    }))
    assert "tictactoe×2" in text


def test_no_external_plugin_is_stated_explicitly_not_omitted():
    """Silence reads as 'fine'. The absence of a game plugin is the single
    most common cause of 'my buttons do nothing', so it must be said."""
    assert "无外部插件" in diag.format_report(_report())


def test_formatting_never_explodes_on_a_section_that_failed():
    """build_report puts an {'error': ...} dict in place of a broken section;
    the formatter must not be the thing that then crashes the command."""
    text = diag.format_report({
        "modules": [], "api": [], "actions": {"error": "boom"},
        "providers": [], "bridge": {}, "image_host": {"error": "未初始化"},
        "storage": {}, "identities": {}, "healthy": False,
    })
    assert "未初始化" in text
