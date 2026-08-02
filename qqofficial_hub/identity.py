"""Who is behind an OpenID, and whether they may press buttons.

Why this exists
---------------
``INTERACTION_CREATE`` carries only ``group_member_openid`` -- an opaque hex
string, never a nickname. A nickname is available *only* on
``GROUP_AT_MESSAGE_CREATE`` (``message.author.username``). So the only way to
show "who pressed this" is to remember the name from the last time that person
actually talked to the bot.

That same fact doubles as an anti-abuse gate: a stranger who has never spoken
to the bot cannot be named, and is therefore not allowed to drive interactive
cards. Someone spamming a group's game only needs one click otherwise.

Names are refreshed on **every** inbound message, so a rename is picked up the
next time that person speaks rather than being frozen forever.
"""
from __future__ import annotations

import time
from typing import Any

#: Entries older than this are pruned. Long enough that a regular participant
#: stays known across a few days of silence.
DEFAULT_TTL_SECONDS = 30 * 86400

#: Hard cap so a busy group cannot grow the state file without bound.
MAX_ENTRIES = 5000


def normalize_name(raw: object, limit: int = 32) -> str:
    """Clean a nickname for safe display inside Markdown.

    QQ nicknames routinely contain characters that would break a card:
    newlines, Markdown syntax, and the angle brackets used by QQ's own tags.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())            # collapse newlines / runs of space
    for char in ("<", ">", "[", "]", "(", ")", "`", "*", "_", "|", "#", "!"):
        text = text.replace(char, "")
    text = text.strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def looks_like_openid(value: str) -> bool:
    """True when a string is an opaque OpenID rather than a human name."""
    text = str(value or "").strip()
    return (
        len(text) >= 24
        and all(c in "0123456789abcdefABCDEF" for c in text)
    )


def display_label(name: str, openid: str) -> str:
    """A human-readable label, never a raw OpenID.

    Falls back to a short suffix so logs and cards can still distinguish two
    unknown users without exposing the full identifier.
    """
    cleaned = normalize_name(name)
    if cleaned and not looks_like_openid(cleaned):
        return cleaned
    tail = str(openid or "")[-4:]
    return f"未知用户…{tail}" if tail else "未知用户"


class IdentityBook:
    """OpenID -> last known nickname, scoped per group origin."""

    def __init__(self, store: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._store = store
        self.ttl_seconds = max(int(ttl_seconds), 3600)

    @staticmethod
    def key(origin: str, openid: str) -> str:
        return f"{origin}|{openid}"

    async def remember(self, origin: str, openid: str, name: str) -> bool:
        """Record the latest nickname. Returns True when something changed.

        Called on every inbound message, so renames are picked up naturally.
        An empty name still marks the user as "has spoken", which is what the
        gate below cares about.
        """
        openid = str(openid or "").strip()
        if not origin or not openid:
            return False
        cleaned = normalize_name(name)
        return await self._store.remember_identity(
            self.key(origin, openid), cleaned, self.ttl_seconds
        )

    async def lookup(self, origin: str, openid: str) -> dict[str, Any] | None:
        openid = str(openid or "").strip()
        if not origin or not openid:
            return None
        entry = await self._store.get_identity(self.key(origin, openid))
        if not entry:
            return None
        if int(entry.get("expires_at", 0)) <= int(time.time()):
            return None
        return entry

    async def name_of(self, origin: str, openid: str) -> str:
        entry = await self.lookup(origin, openid)
        return str((entry or {}).get("name") or "")

    async def is_known(self, origin: str, openid: str) -> bool:
        """Has this person ever spoken to the bot in this group?"""
        return await self.lookup(origin, openid) is not None

    async def label_for(self, origin: str, openid: str) -> str:
        return display_label(await self.name_of(origin, openid), openid)
