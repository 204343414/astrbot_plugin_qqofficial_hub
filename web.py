from __future__ import annotations

from typing import Any

from astrbot.api.web import error_response, json_response, request
from astrbot.api.star import Context

from .qqofficial_hub.command_catalog import build_command_catalog
from .qqofficial_hub.diagnostics import build_report
from .qqofficial_hub.named_cards import conflicts_with_astrbot
from .qqofficial_hub.snippets import catalog as snippet_catalog
from .qqofficial_hub.store import PanelStore

PLUGIN_NAME = "astrbot_plugin_qqofficial_hub"


class HubWebController:
    def __init__(self, context: Context, store: PanelStore, plugin: Any) -> None:
        self.context = context
        self.store = store
        self.plugin = plugin

    def register_routes(self) -> None:
        self.context.register_web_api(f"/{PLUGIN_NAME}/bootstrap", self.bootstrap, ["GET"], "QQ Hub editor bootstrap")
        self.context.register_web_api(f"/{PLUGIN_NAME}/panel", self.save_panel, ["POST"], "Save QQ Hub panel")
        self.context.register_web_api(f"/{PLUGIN_NAME}/send-test", self.send_test, ["POST"], "Send panel to an observed QQ group")
        self.context.register_web_api(f"/{PLUGIN_NAME}/diagnostics", self.diagnostics, ["GET"], "QQ Hub runtime diagnostics")
        self.context.register_web_api(f"/{PLUGIN_NAME}/cards", self.save_card, ["POST"], "Create or update a named card")
        self.context.register_web_api(f"/{PLUGIN_NAME}/cards/delete", self.delete_card, ["POST"], "Delete a named card")

    async def bootstrap(self):
        payload = await self.store.bootstrap()
        payload["command_catalog"] = build_command_catalog(self.context)
        payload["action_catalog"] = self.plugin.get_action_catalog()
        payload["snippet_catalog"] = snippet_catalog()
        payload["named_cards"] = await self.store.list_cards()
        return json_response(payload)

    async def save_panel(self):
        payload: dict[str, Any] = await request.json(default={})
        try:
            self.plugin.validate_registered_actions(payload.get("panel"))
            panel = await self.store.save_panel(
                str(payload.get("scope", "")), str(payload.get("origin", "")), payload.get("panel")
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return json_response({"panel": panel})

    async def send_test(self):
        payload: dict[str, Any] = await request.json(default={})
        try:
            result = await self.plugin.send_panel_from_ui(
                str(payload.get("origin", "")),
                mode=str(payload.get("mode", "configured")),
            )
        except (ValueError, RuntimeError) as exc:
            return error_response(str(exc), status_code=400)
        return json_response(result)

    async def diagnostics(self):
        return json_response(await build_report(self.plugin))

    async def save_card(self):
        payload: dict[str, Any] = await request.json(default={})
        command = str(payload.get("command", "") or "")
        try:
            self.plugin.validate_registered_actions(payload.get("panel"))
            clash = conflicts_with_astrbot(
                command, build_command_catalog(self.context)
            )
            if clash:
                return error_response(
                    f"指令与已注册命令 {clash} 冲突，请换一个", status_code=400
                )
            card = await self.store.save_card(
                str(payload.get("id", "")), payload.get("panel"), command
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return json_response({"card": card})

    async def delete_card(self):
        payload: dict[str, Any] = await request.json(default={})
        removed = await self.store.delete_card(str(payload.get("id", "")))
        return json_response({"deleted": removed})
