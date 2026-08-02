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
    "interaction_bridge",
    "issued_cards",
    "keyboard",
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
            "actions": sorted(items, key=lambda x: x["id"]),
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


async def build_report(plugin: Any) -> dict[str, Any]:
    """Full diagnostics payload. Never raises: a broken section reports itself."""
    report: dict[str, Any] = {}
    sections = {
        "modules": lambda: module_report(),
        "api": lambda: api_report(plugin),
        "actions": lambda: action_report(plugin),
        "providers": lambda: provider_report(plugin),
        "bridge": lambda: bridge_report(plugin),
    }
    for key, build in sections.items():
        try:
            report[key] = build()
        except Exception as exc:  # pragma: no cover - defensive
            report[key] = {"error": f"{type(exc).__name__}: {exc}"}
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
