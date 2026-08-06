"""Runtime inventory of everything plugged into the Hub.

Purpose
-------
The Hub is a platform: other plugins register Actions and card providers into
it, and they may be installed, upgraded or removed at any time. When something
does not appear on a card, the question is always the same -- *is it registered
right now?* Guessing wastes time, so this exposes the live registry.

Design rule: **enumerate, never hard-code.** Every list here is derived from
the actual runtime registries, so a newly added module shows up without anyone
remembering to update this file. A diagnostics page that silently omits a
module is worse than no page at all.
"""
from __future__ import annotations

import importlib
import inspect
from typing import Any

#: Capabilities a companion plugin is expected to reach through. Missing ones
#: mean an outdated Hub, which is the usual cause of "not installed" reports.
REQUIRED_HUB_API = (
    "send_ephemeral_card",
    "end_ephemeral_session",
    "register_card_provider",
    "unregister_card_provider",
    "get_action_catalog",
    "send_media_message",
    "publish_image",
    "publish_image_checked",
    "image_host_ready",
    "image_host_reachable",
    "actions",
)

#: Modules that make up the Hub. Presence and import health are both reported
#: so a broken module cannot hide behind a working UI.
HUB_MODULES = (
    "action_registry",
    "command_catalog",
    "command_dispatch",
    "ephemeral",
    "ephemeral_routes",
    "identity",
    "image_host",
    "interaction_bridge",
    "issued_cards",
    "keyboard",
    "named_cards",
    "panel_convert",
    "passive_reply",
    "snippets",
    "store",
)


def module_report() -> list[dict[str, Any]]:
    """Import every Hub module and report whether it loaded."""
    rows = []
    for name in HUB_MODULES:
        entry: dict[str, Any] = {"name": name, "ok": False, "error": "", "symbols": 0}
        try:
            module = importlib.import_module(f".{name}", __package__)
            entry["ok"] = True
            entry["symbols"] = len(
                [s for s in dir(module) if not s.startswith("_")]
            )
        except Exception as exc:  # pragma: no cover - defensive
            entry["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(entry)
    return rows


def api_report(plugin: Any) -> list[dict[str, Any]]:
    """Report which public API surfaces companion plugins depend on."""
    rows = []
    for name in REQUIRED_HUB_API:
        attr = getattr(plugin, name, None)
        rows.append({
            "name": name,
            "present": attr is not None,
            "kind": "coroutine" if inspect.iscoroutinefunction(attr)
                    else "method" if callable(attr)
                    else "attribute" if attr is not None else "missing",
        })
    return rows


def action_report(plugin: Any) -> dict[str, Any]:
    """Group every registered Action by the plugin that owns it.

    This is the answer to "did my game plugin actually register?" -- the Hub
    itself owns some actions, so a companion plugin appearing here proves the
    handshake worked.
    """
    catalog = []
    try:
        catalog = plugin.get_action_catalog() or []
    except Exception as exc:  # pragma: no cover - defensive
        return {"owners": [], "total": 0, "error": f"{type(exc).__name__}: {exc}"}

    owners: dict[str, list[dict[str, str]]] = {}
    for item in catalog:
        owner = str(item.get("owner") or "unknown")
        owners.setdefault(owner, []).append({
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "description": str(item.get("description") or ""),
            "permission": str(item.get("permission") or ""),
        })
    hub_owner_prefix = "astrbot_plugin_qqofficial_hub"
    rows = [
        {
            "owner": owner,
            "external": not owner.startswith(hub_owner_prefix),
            # Command actions carry hashed ids, so ordering by id looks random.
            # Sort by the human-readable title, falling back to the id.
            "actions": sorted(items, key=lambda x: (x["title"] or x["id"], x["id"])),
        }
        for owner, items in sorted(owners.items())
    ]
    return {"owners": rows, "total": len(catalog), "error": ""}


def provider_report(plugin: Any) -> list[dict[str, str]]:
    """Card providers registered for ``next_card`` targets."""
    providers = getattr(plugin, "_card_providers", {}) or {}
    rows = []
    for card_id, func in sorted(providers.items()):
        module = getattr(func, "__module__", "?")
        rows.append({
            "card_id": card_id,
            "callback": getattr(func, "__qualname__", repr(func)),
            "module": module,
            "external": not str(module).startswith("astrbot_plugin_qqofficial_hub"),
        })
    return rows


def bridge_report(plugin: Any) -> dict[str, Any]:
    from . import interaction_bridge

    state = interaction_bridge._state()
    callback_ref = state.get("callback")
    return {
        "enabled": bool(getattr(plugin, "experimental_bridge", False)),
        "installed": bool(state.get("installed")),
        "owner": str(state.get("owner") or ""),
        "generation": int(state.get("generation") or 0),
        "callback_alive": bool(callback_ref() if callback_ref else None),
        "ack_types": sorted(interaction_bridge.ACK_TYPES),
        "handled_types": sorted(interaction_bridge.HANDLED_TYPES),
        "seen_cache": len(state.get("seen") or {}),
        "inflight": len(state.get("inflight") or set()),
    }


async def identity_report(store: Any) -> dict[str, Any]:
    """Who the Hub can actually name.

    Makes the "why is it still an OpenID" question answerable without reading
    logs: if `named` is 0 while `seen` is high, no nickname is reaching us.
    """
    import time
    raw = getattr(store, "_data", {}) or {}
    now = int(time.time())
    entries = [
        item for item in (raw.get("identities", {}) or {}).values()
        if isinstance(item, dict) and int(item.get("expires_at", 0)) > now
    ]
    named = [item for item in entries if str(item.get("name") or "").strip()]
    roles: dict[str, int] = {}
    for item in entries:
        role = str(item.get("role") or "").strip()
        if role:
            roles[role] = roles.get(role, 0) + 1
    return {
        "seen": len(entries),
        "named": len(named),
        "samples": [str(item.get("name")) for item in named[:5]],
        # Roles are only learned from messages, so a group_manager button
        # refusing everyone usually means nobody has spoken yet rather than
        # that the check is broken. Showing the counts makes that visible.
        "roles": roles,
        "managers": roles.get("admin", 0) + roles.get("owner", 0),
    }


async def storage_report(store: Any) -> dict[str, Any]:
    """Live counts from the state file, so stale data is visible."""
    import time

    data = await store.bootstrap()
    raw = getattr(store, "_data", {}) or {}
    now = int(time.time())
    ephemeral_cards = raw.get("ephemeral_cards", {}) or {}
    live = [c for c in ephemeral_cards.values()
            if isinstance(c, dict) and int(c.get("expires_at", 0)) > now]
    sessions = {str(c.get("session_id")) for c in live if c.get("session_id")}
    issued = raw.get("issued_test_cards", {}) or {}
    return {
        "observed_groups": len(data.get("observed_groups", {}) or {}),
        "group_overrides": len(data.get("group_overrides", {}) or {}),
        "issued_panel_cards": len(issued),
        "ephemeral_cards_total": len(ephemeral_cards),
        "ephemeral_cards_live": len(live),
        "ephemeral_sessions_live": len(sessions),
    }


def image_host_report(plugin: Any) -> dict[str, Any]:
    """Whether cards can embed images, and why not when they cannot.

    Split into "enabled / configured / running" because those fail for
    different reasons and need different fixes: a missing base URL is a
    config typo, a stopped listener is a port clash, and neither looks like
    the other in a log.
    """
    host = getattr(plugin, "image_host", None)
    if host is None:
        return {"enabled": False, "error": "未初始化"}
    status = host.status()
    status["enabled"] = bool(getattr(plugin, "image_host_enabled", False))
    if not status["enabled"]:
        status["hint"] = "未开启：配置里打开 image_host_enabled"
    elif not status["configured"]:
        status["hint"] = "缺少 image_host_base_url（隧道的公网域名）"
    elif not status["running"]:
        status["hint"] = f"端口 {status['port']} 未监听，可能被占用"
    else:
        status["hint"] = "就绪"
    return status


async def build_report(plugin: Any) -> dict[str, Any]:
    """Full diagnostics payload. Never raises: a broken section reports itself."""
    report: dict[str, Any] = {}
    sections = {
        "modules": lambda: module_report(),
        "api": lambda: api_report(plugin),
        "actions": lambda: action_report(plugin),
        "providers": lambda: provider_report(plugin),
        "bridge": lambda: bridge_report(plugin),
        "image_host": lambda: image_host_report(plugin),
    }
    for key, build in sections.items():
        try:
            report[key] = build()
        except Exception as exc:  # pragma: no cover - defensive
            report[key] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        report["identities"] = await identity_report(plugin.store)
    except Exception as exc:  # pragma: no cover - defensive
        report["identities"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        report["storage"] = await storage_report(plugin.store)
    except Exception as exc:  # pragma: no cover - defensive
        report["storage"] = {"error": f"{type(exc).__name__}: {exc}"}

    modules = report.get("modules") or []
    api = report.get("api") or []
    report["healthy"] = (
        all(m.get("ok") for m in modules if isinstance(m, dict))
        and all(a.get("present") for a in api if isinstance(a, dict))
    )
    return report


def format_report(report: dict[str, Any]) -> str:
    """Condense the report into one chat message.

    QQ Official counts every reply against a tight quota, so a diagnostic that
    needs three messages is a diagnostic people stop running. Failures are
    spelled out; healthy sections collapse to a count.
    """
    lines: list[str] = []

    modules = [m for m in (report.get("modules") or []) if isinstance(m, dict)]
    broken = [m for m in modules if not m.get("ok")]
    lines.append(
        f"模块 {len(modules) - len(broken)}/{len(modules)}"
        + ("" if not broken else
           " ✗ " + "，".join(f"{m['name']}: {m['error']}" for m in broken))
    )

    api = [a for a in (report.get("api") or []) if isinstance(a, dict)]
    missing = [a["name"] for a in api if not a.get("present")]
    lines.append(
        f"接口 {len(api) - len(missing)}/{len(api)}"
        + ("" if not missing else " ✗ 缺 " + "，".join(missing))
    )

    actions = report.get("actions") or {}
    owners = [o for o in (actions.get("owners") or []) if o.get("external")]
    lines.append(
        f"Action {actions.get('total', 0)} 个"
        + (f"，外部插件 {len(owners)} 个：" + "，".join(
            f"{o['owner'].split('.')[-1]}×{len(o['actions'])}" for o in owners)
           if owners else "，无外部插件")
    )

    providers = report.get("providers") or []
    lines.append(f"卡片提供者 {len(providers)} 个")

    bridge = report.get("bridge") or {}
    lines.append(
        "交互桥 " + ("已装载" if bridge.get("installed") else
                    "已开启但未装载" if bridge.get("enabled") else "未开启")
    )

    host = report.get("image_host") or {}
    if host.get("error"):
        lines.append(f"图床 ✗ {host['error']}")
    else:
        detail = f"，图 {host.get('stored_images', 0)} 张"
        # Fetch counters are the only proof the public side actually works:
        # publishing succeeds locally whether or not anyone can reach us.
        hits = int(host.get("hits", 0) or 0)
        detail += f"，被抓取 {hits} 次" if hits else "，尚未被抓取"
        moved = int(host.get("rediscoveries", 0) or 0)
        if moved:
            # Says out loud what is otherwise a silent, group-wide broken
            # image: the tunnel restarted and every card sent before it died.
            detail += f"，域名换过 {moved} 次"
        lines.append(f"图床 {host.get('hint', '?')}{detail}")
        if host.get("last_error"):
            lines.append(f"　└ ⚠️ {host['last_error']}")
        if host.get("base_url"):
            source = "固定" if host.get("pinned") else "自动发现"
            lines.append(f"　└ {host['base_url']}（{source}）")

    storage = report.get("storage") or {}
    lines.append(
        f"存量 群 {storage.get('observed_groups', 0)}"
        f"／临时卡 {storage.get('ephemeral_cards_live', 0)} 活"
        f"／会话 {storage.get('ephemeral_sessions_live', 0)}"
    )

    identities = report.get("identities") or {}
    identity_line = (
        f"身份 见过 {identities.get('seen', 0)} 人"
        f"／有昵称 {identities.get('named', 0)} 人"
    )
    managers = int(identities.get("managers", 0) or 0)
    identity_line += f"／管理 {managers} 人" if managers else "／暂无已知管理"
    lines.append(identity_line)

    head = "✅ Hub 自检通过" if report.get("healthy") else "⚠️ Hub 自检有问题"
    return head + "\n" + "\n".join(lines)
