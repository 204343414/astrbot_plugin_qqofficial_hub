"""Named cards: a library of reusable panels addressed by id.

The original editor managed exactly one panel per group. That is fine for a
single hub panel, but a game or a menu tree needs several: a lobby, a rules
page, a leaderboard. Named cards give each panel a stable id so buttons can
point at one another via ``next_card`` and so a command can open one directly.

Ids are global (shared by every group) because ``next_card`` targets must
resolve identically wherever the card is sent.
"""
from __future__ import annotations

import re

MAX_CARDS = 100
CARD_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")

#: Commands the Hub itself owns; a card must not shadow them.
RESERVED_COMMANDS = {"qqhub", "qqhub 面板", "help", "帮助"}


def validate_card_id(card_id: object) -> str:
    text = str(card_id or "").strip()
    if not CARD_ID_RE.fullmatch(text):
        raise ValueError("卡片编号只能包含字母、数字、下划线和短横线，最长 40 位")
    return text


def validate_command(command: object) -> str:
    """Normalise an optional trigger command; empty means "no command"."""
    text = str(command or "").strip().lstrip("/").strip()
    if not text:
        return ""
    if len(text) > 32:
        raise ValueError("指令不能超过 32 个字符")
    if any(ch.isspace() for ch in text):
        raise ValueError("指令不能包含空格")
    if text in RESERVED_COMMANDS:
        raise ValueError(f"指令 /{text} 是 Hub 保留指令")
    return text


def conflicts_with_astrbot(command: str, catalog: list[dict]) -> str:
    """Return the clashing AstrBot command, or "" when the name is free.

    Checks aliases too: binding a card to a name that is merely an alias of an
    existing command would silently shadow it.
    """
    command = str(command or "").strip().lstrip("/")
    if not command:
        return ""
    wanted = f"/{command}"
    for item in catalog or []:
        names = [str(item.get("command") or "")]
        names += [str(alias) for alias in (item.get("aliases") or [])]
        for name in names:
            if name and name.lower() == wanted.lower():
                return str(item.get("command") or name)
    return ""
