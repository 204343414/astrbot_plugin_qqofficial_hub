"""Mixins that keep ``main.py`` focused on plugin lifecycle and commands.

These are deliberately mixins rather than free functions: they need the
plugin's ``store``, ``actions`` and config, and splitting them into services
with explicit dependency injection would be a bigger refactor than the current
size justifies. Grouping by responsibility is the goal -- keyboard rendering
and ephemeral-card routing are each self-contained concerns.
"""
from __future__ import annotations

from typing import Any


class KeyboardBuildMixin:
    """Render stored buttons into QQ keyboard payloads."""

    @staticmethod
    def _permission_payload(button: dict[str, Any]) -> dict[str, Any]:
        policy = str(button.get("permission", ""))
        if policy == "specified_users":
            return {"type": 0, "specify_user_ids": list(button.get("specified_users", []))}
        if policy == "group_manager":
            return {"type": 1}
        # AstrBot-admin/operator are verified by Hub after a callback. QQ has
        # no equivalent policy field, so it must allow the click through.
        return {"type": 2}

    @classmethod
    def _button(cls, button: dict[str, Any], nonce: str) -> dict[str, Any]:
        action_type = int(button["action_type"])
        data = str(button["data"])
        if action_type == 1:
            data = f"qqhub:v2:{nonce}:{button['id']}"
        return {
            "id": str(button["id"]),
            "render_data": {"label": button["label"], "visited_label": button["visited_label"], "style": int(button["style"])},
            "action": {
                "type": action_type,
                "permission": cls._permission_payload(button),
                "data": data,
                "reply": bool(button.get("reply", False)),
                "enter": bool(button.get("enter", False)),
                "anchor": int(button.get("anchor", 0) or 0),
                "unsupport_tips": str(button.get("unsupport_tips") or "当前 QQ 版本不支持该按钮"),
            },
        }
