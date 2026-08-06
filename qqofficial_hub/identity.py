"""Who is behind an OpenID, what they may do, and whether they may click.

Where the data comes from
-------------------------
``GROUP_AT_MESSAGE_CREATE`` carries an ``author`` object with three fields
this module cares about::

    member_openid   opaque per-group user id
    username        display name
    member_role     "member" | "admin" | "owner"

Two caveats that decide the whole design here:

* **botpy 1.2.1 parses almost none of it.** ``GroupMessage._User`` reads only
  ``member_openid``. AstrBot v4.26.7 patches the class to add ``username``
  and friends, but *not* ``member_role`` -- so the role has to be read off
  the preserved raw payload (``raw_data["author"]["member_role"]``) rather
  than off the object. Reading it from the object would silently yield None
  forever, which looks exactly like "nobody is an admin".
* **Only messages carry any of it.** ``INTERACTION_CREATE`` -- a button click
  -- has just ``group_member_openid``. There is no nickname and no role on a
  click, and no API to look either up (every ``get_*_member`` endpoint is
  guild-only). A click can therefore only be attributed by remembering what
  a previous *message* said.

That asymmetry is why this book exists: it turns "seen once in a message"
into something a click can be checked against later.

What it does
------------
* record that an OpenID has spoken at least once -- the anti-abuse gate
  behind ``require_known_clicker``;
* remember the last known nickname, whether QQ supplied it or the user set
  it with ``/我叫``;
* remember the last known group role, so a ``group_manager`` button can be
  enforced on the server instead of trusted to the client.

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


#: QQ's own vocabulary for ``author.member_role``.
ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"
ROLE_OWNER = "owner"
#: Roles that count as "manages this group". Owner is not listed as an admin
#: by QQ, so checking for "admin" alone would lock out the group owner --
#: the one person who most obviously qualifies.
MANAGER_ROLES = frozenset({ROLE_ADMIN, ROLE_OWNER})
VALID_ROLES = frozenset({ROLE_MEMBER, ROLE_ADMIN, ROLE_OWNER})

ROLE_LABELS = {ROLE_MEMBER: "群成员", ROLE_ADMIN: "管理员", ROLE_OWNER: "群主"}


def role_from_event(event: Any) -> str:
    """Dig ``author.member_role`` out of an inbound group message.

    Goes to the raw payload rather than the parsed object on purpose: botpy
    1.2.1's ``GroupMessage._User`` does not define ``member_role``, and
    AstrBot's patched subclass does not add it either. Reading it as an
    attribute therefore yields None forever -- indistinguishable from "this
    person is not an admin", which is precisely the bug that would make a
    permission check quietly refuse everyone.

    Returns "" when the field is absent, which is normal: interaction events
    and older payloads simply do not carry it.
    """
    message = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if message is None:
        return ""
    # AstrBot preserves the untouched payload here; prefer it over attributes.
    raw = getattr(message, "raw_data", None)
    if isinstance(raw, dict):
        author = raw.get("author")
        if isinstance(author, dict):
            role = str(author.get("member_role") or "").strip().lower()
            if role in VALID_ROLES:
                return role
    # A future botpy may expose it properly; use it if it is really there.
    role = str(getattr(getattr(message, "author", None), "member_role", "") or "")
    role = role.strip().lower()
    return role if role in VALID_ROLES else ""


class IdentityBook:
    """OpenID -> last known nickname and group role, scoped per origin."""

    def __init__(self, store: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._store = store
        self.ttl_seconds = max(int(ttl_seconds), 3600)

    @staticmethod
    def key(origin: str, openid: str) -> str:
        return f"{origin}|{openid}"

    async def remember(self, origin: str, openid: str, name: str,
                       role: str = "") -> bool:
        """Record the latest nickname and role. True when something changed.

        Called on every inbound message, so renames and promotions are picked
        up naturally. An empty name still marks the user as "has spoken",
        which is what the gate below cares about.
        """
        openid = str(openid or "").strip()
        if not origin or not openid:
            return False
        cleaned = normalize_name(name)
        role = str(role or "").strip().lower()
        return await self._store.remember_identity(
            self.key(origin, openid),
            cleaned,
            self.ttl_seconds,
            # QQ sends an empty nickname on every group message; that must not
            # wipe a name the user set with /我叫.
            keep_existing_name=True,
            role=role if role in VALID_ROLES else "",
        )

    async def set_name(self, origin: str, openid: str, name: str) -> str:
        """Explicitly set a self-declared display name.

        Unlike :meth:`remember`, an empty value here *clears* the name, so a
        user can withdraw it and fall back to the anonymous placeholder.
        """
        cleaned = normalize_name(name)
        if cleaned and looks_like_openid(cleaned):
            raise ValueError("昵称不能是一串十六进制 ID")
        await self._store.remember_identity(
            self.key(origin, str(openid or "").strip()), cleaned, self.ttl_seconds
        )
        return cleaned

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

    async def role_of(self, origin: str, openid: str) -> str:
        """Last seen group role, or "" if this person has never spoken.

        Never guesses. An unknown role is reported as unknown rather than as
        ``member``, so a caller can tell "definitely not an admin" apart from
        "no idea yet" -- they deserve different answers.
        """
        entry = await self.lookup(origin, openid)
        role = str((entry or {}).get("role") or "").strip().lower()
        return role if role in VALID_ROLES else ""

    async def is_group_manager(self, origin: str, openid: str) -> bool:
        """Whether this OpenID was last seen as group admin or owner."""
        return await self.role_of(origin, openid) in MANAGER_ROLES
