"""Atomic, validated persistence for the Hub editor."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

PANEL_ID = "default_panel"
MAX_ROWS = 5
MAX_BUTTONS_PER_ROW = 5
PERMISSIONS = {"everyone", "group_manager", "astrbot_admin", "specified_users", "operator"}
ACTION_TYPES = {0, 1, 2}


def empty_panel() -> dict[str, Any]:
    """A visible, safe default that doubles as the first QQ capability probe."""
    return {
        "id": PANEL_ID,
        "name": "QQ Official Hub 白板测试卡",
        "markdown": "# QQ Official Hub 白板测试卡\n每个按钮演示一种 QQ 官方能力；后台按钮 15 分钟后失效。",
        "rows": [
            [
                {"id": "blue_everyone", "label": "蓝色：所有人可点", "visited_label": "蓝色：所有人可点", "style": 1, "action_type": 1, "data": "hub.test", "permission": "everyone", "specified_users": []},
                {"id": "gray_everyone", "label": "灰色：所有人可点", "visited_label": "灰色：所有人可点", "style": 0, "action_type": 1, "data": "hub.test", "permission": "everyone", "specified_users": []},
            ],
            [
                {"id": "refresh", "label": "刷新测试卡", "visited_label": "刷新测试卡", "style": 1, "action_type": 1, "data": "hub.refresh", "permission": "everyone", "specified_users": []},
                {"id": "manager", "label": "仅群管理可点", "visited_label": "仅群管理可点", "style": 0, "action_type": 1, "data": "hub.test", "permission": "group_manager", "specified_users": []},
            ],
            [
                {"id": "insert", "label": "放入输入框，不发送", "visited_label": "放入输入框，不发送", "style": 0, "action_type": 2, "data": "/头条卡片", "permission": "everyone", "specified_users": []},
                {"id": "docs", "label": "打开 QQ 按钮文档", "visited_label": "打开 QQ 按钮文档", "style": 1, "action_type": 0, "data": "https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html", "permission": "everyone", "specified_users": []},
            ],
        ],
        "revision": 1,
    }


class PanelStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "panels.json"
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = self._load()

    def _initial(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "templates": {PANEL_ID: empty_panel()},
            "group_overrides": {},
            "observed_groups": {},
            "issued_test_cards": {},
        }

    def _load(self) -> dict[str, Any]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            data = self._initial()
            self._write_atomic(data)
            return data
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Hub data file cannot be loaded: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Hub data file root must be an object")
        value.setdefault("templates", {PANEL_ID: empty_panel()})
        value.setdefault("group_overrides", {})
        value.setdefault("observed_groups", {})
        value.setdefault("issued_test_cards", {})
        default = value["templates"].get(PANEL_ID)
        if isinstance(default, dict) and default.get("name") == "头条卡片" and not default.get("rows"):
            value["templates"][PANEL_ID] = empty_panel()
            self._write_atomic(value)
        return value

    def _write_atomic(self, value: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="panels.", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    async def bootstrap(self) -> dict[str, Any]:
        async with self._lock:
            return copy.deepcopy(self._data)

    async def observe_group(self, origin: str, platform_id: str) -> None:
        if not self._valid_group_origin(origin):
            return
        async with self._lock:
            groups = self._data["observed_groups"]
            if origin not in groups:
                groups[origin] = {"origin": origin, "platform_id": platform_id}
                self._write_atomic(self._data)

    async def save_panel(self, scope: str, origin: str, panel: object) -> dict[str, Any]:
        normalized = validate_panel(panel)
        async with self._lock:
            if scope == "global":
                prior = self._data["templates"].get(PANEL_ID, empty_panel())
                normalized["revision"] = int(prior.get("revision", 0)) + 1
                self._data["templates"][PANEL_ID] = normalized
            elif scope == "group":
                if origin not in self._data["observed_groups"]:
                    raise ValueError("只能为已观察到的 QQ Official 群保存覆盖配置")
                base = self._data["group_overrides"].get(origin, {})
                normalized["revision"] = int(base.get("revision", 0)) + 1
                self._data["group_overrides"][origin] = normalized
            else:
                raise ValueError("scope 只能是 global 或 group")
            self._write_atomic(self._data)
            return copy.deepcopy(normalized)


    async def issue_panel_card(self, origin: str, panel: dict[str, Any]) -> str:
        """Persist an opaque, group-scoped snapshot before sending a callback card."""
        import secrets
        import time
        if not self._valid_group_origin(origin):
            raise ValueError("卡片只能发送到 QQ Official 群")
        async with self._lock:
            nonce = secrets.token_urlsafe(18)
            now = int(time.time())
            cards = self._data["issued_test_cards"]
            for key, item in list(cards.items()):
                if not isinstance(item, dict) or int(item.get("expires_at", 0)) <= now:
                    cards.pop(key, None)
            cards[nonce] = {"origin": origin, "expires_at": now + 900, "panel": copy.deepcopy(panel)}
            self._write_atomic(self._data)
            return nonce

    async def get_issued_button(self, origin: str, nonce: str, button_id: str) -> dict[str, Any] | None:
        import time
        async with self._lock:
            item = self._data["issued_test_cards"].get(nonce)
            if not isinstance(item, dict) or item.get("origin") != origin or int(item.get("expires_at", 0)) <= int(time.time()):
                return None
            panel = item.get("panel")
            if not isinstance(panel, dict):
                return None
            for row in panel.get("rows", []):
                if isinstance(row, list):
                    for button in row:
                        if isinstance(button, dict) and button.get("id") == button_id:
                            return copy.deepcopy(button)
            return None

    async def issue_test_card(self, origin: str) -> str:
        """Persist a short-lived opaque callback capability before sending."""
        import secrets
        import time
        if not self._valid_group_origin(origin):
            raise ValueError("测试卡只能发送到 QQ Official 群")
        async with self._lock:
            nonce = secrets.token_urlsafe(18)
            now = int(time.time())
            cards = self._data["issued_test_cards"]
            for key, item in list(cards.items()):
                if not isinstance(item, dict) or int(item.get("expires_at", 0)) <= now:
                    cards.pop(key, None)
            cards[nonce] = {"origin": origin, "expires_at": now + 900}
            self._write_atomic(self._data)
            return nonce

    async def claim_test_action(self, origin: str, nonce: str) -> bool:
        import time
        async with self._lock:
            item = self._data["issued_test_cards"].get(nonce)
            if not isinstance(item, dict):
                return False
            if item.get("origin") != origin or int(item.get("expires_at", 0)) <= int(time.time()):
                return False
            return True

    def _valid_group_origin(self, origin: str) -> bool:
        parts = origin.split(":", 2)
        return len(parts) == 3 and parts[1] == "GroupMessage" and bool(parts[0]) and bool(parts[2])


def validate_panel(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("panel 必须是对象")
    name = _text(value.get("name"), "卡片名称", 64)
    markdown = _text(value.get("markdown"), "Markdown", 2000)
    rows = value.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("按钮最多 5 行")
    normalized_rows: list[list[dict[str, Any]]] = []
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, list) or len(row) > MAX_BUTTONS_PER_ROW:
            raise ValueError(f"第 {row_index} 行最多 5 个按钮")
        normalized_rows.append([_validate_button(button) for button in row])
    button_ids = [button["id"] for row in normalized_rows for button in row]
    if len(button_ids) != len(set(button_ids)):
        raise ValueError("同一张卡片内的按钮 ID 必须唯一")
    return {"id": PANEL_ID, "name": name, "markdown": markdown, "rows": normalized_rows}


def _validate_button(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("按钮必须是对象")
    label = _text(value.get("label"), "按钮文字", 64)
    visited_label = _text(value.get("visited_label") or label, "点击后文字", 64)
    style = value.get("style", 0)
    if style not in {0, 1}:
        raise ValueError("按钮样式只能是灰色或蓝色")
    action_type = value.get("action_type")
    if action_type not in ACTION_TYPES:
        raise ValueError("按钮动作类型无效")
    data = _text(value.get("data"), "动作数据", 256)
    permission = value.get("permission")
    if permission not in PERMISSIONS:
        raise ValueError("按钮权限无效")
    users = value.get("specified_users", [])
    if permission == "specified_users":
        if not isinstance(users, list) or not users or any(not isinstance(item, str) or not item.strip() for item in users):
            raise ValueError("指定用户权限需要至少一个 OpenID")
    else:
        users = []
    if action_type == 0 and not re.fullmatch(r"https://[^\s]+", data):
        raise ValueError("URL 按钮只允许 https:// 地址")
    if action_type == 1 and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", data):
        raise ValueError("后台动作必须是受控 action_id，不能填命令或脚本")
    return {
        "id": str(value.get("id") or "").strip() or f"button-{label}",
        "label": label,
        "visited_label": visited_label,
        "style": style,
        "action_type": action_type,
        "data": data,
        "permission": permission,
        "specified_users": users,
    }


def _text(value: object, name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}不能为空")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name}不能超过 {max_length} 个字符")
    return result
