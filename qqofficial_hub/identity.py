"""Who is behind an OpenID, and whether they may press buttons.

Why this exists
---------------
QQ **never sends a nickname in the group scene**. Verified against botpy 1.2.1:
``GroupMessage._User`` has exactly one field, ``member_openid``. There is no
``username``, and the API exposes no group-member lookup either (every
``get_*_member`` endpoint is guild-only). ``INTERACTION_CREATE`` likewise
carries only ``group_member_openid``.

So this book cannot invent names out of thin air. What it *can* do:

* record that an OpenID has spoken to the bot at least once, which is the
  anti-abuse gate behind ``require_known_clicker``;
* store a name when one is genuinely available -- the C2C/guild scenes do
  provide ``username``, and a plugin may set a nickname explicitly via
  :meth:`IdentityBook.remember`.

When no name is known the display falls back to a stable short label rather
than an opaque hex string, because showing a raw OpenID looks like a bug.
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


#: Stable, friendly placeholders. QQ gives no nickname in groups, so most
#: players will be shown one of these plus a short suffix. Picked by hashing
#: the OpenID so the same person keeps the same word within a group.
ANONYMOUS_WORDS = (
    "玩家", "旅人", "路人", "访客", "同学", "朋友", "邻座", "对手",
)


def display_label(name: str, openid: str) -> str:
    """A human-readable label, never a raw OpenID.

    QQ does not provide nicknames in the group scene, so this usually returns
    a friendly placeholder. It is deterministic: the same OpenID always maps to
    the same word, which lets players tell each other apart in a match.
    """
    cleaned = normalize_name(name)
    if cleaned and not looks_like_openid(cleaned):
        return cleaned
    openid = str(openid or "")
    if not openid:
        return "某人"
    word = ANONYMOUS_WORDS[sum(openid.encode()) % len(ANONYMOUS_WORDS)]
    return f"{word}{openid[-4:]}"


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
