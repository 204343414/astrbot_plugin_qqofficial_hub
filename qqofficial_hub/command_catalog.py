"""Read-only AstrBot command catalog for Hub type=2 button editing."""
from __future__ import annotations

from typing import Any

from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.permission import PermissionTypeFilter
from astrbot.core.star.star_handler import StarHandlerMetadata, star_handlers_registry


def build_command_catalog(context: Any) -> list[dict[str, Any]]:
    """Return active command handlers with canonical path, aliases and metadata."""
    active_modules: set[str] = set()
    try:
        for star in context.get_all_stars():
            if getattr(star, "activated", False) and getattr(star, "module_path", None):
                active_modules.add(str(star.module_path))
    except Exception:
        # Registry filtering still works if one AstrBot version lacks get_all_stars.
        pass

    commands: dict[str, dict[str, Any]] = {}
    for handler in star_handlers_registry:
        if not isinstance(handler, StarHandlerMetadata):
            continue
        module_path = str(getattr(handler, "handler_module_path", "") or "")
        if active_modules and not any(
            module_path.startswith(module) or module.startswith(module_path)
            for module in active_modules
        ):
            continue
        permission = "admin" if any(
            isinstance(item, PermissionTypeFilter)
            for item in getattr(handler, "event_filters", [])
        ) else "everyone"
        description = str(getattr(handler, "desc", "") or "").split("\n", 1)[0].strip()
        if not description:
            description = str(getattr(getattr(handler, "handler", None), "__doc__", "") or "").split("\n", 1)[0].strip()
        for item in getattr(handler, "event_filters", []):
            if not isinstance(item, CommandFilter):
                continue
            names = [str(name).strip() for name in item.get_complete_command_names() if str(name).strip()]
            if not names:
                continue
            canonical = "/" + names[0]
            commands.setdefault(canonical, {
                "command": canonical,
                "aliases": ["/" + name for name in names[1:]],
                "description": description,
                "parameters": item.print_types(),
                "permission": permission,
                "module": module_path,
            })
    return sorted(commands.values(), key=lambda item: (item["module"], item["command"]))
