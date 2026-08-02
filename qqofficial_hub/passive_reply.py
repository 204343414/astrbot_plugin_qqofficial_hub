"""Send a QQ Official reply as a *passive* message using an event_id.

Why this module exists
----------------------
QQ classifies a message as **passive** when it carries ``msg_id`` or ``event_id``
and **proactive** otherwise. Proactive group messages need the group owner to
enable proactive push and burn a 4-per-month quota; passive ones do not.

``INTERACTION_CREATE`` exposes an id that is explicitly valid as ``event_id``,
so every reply triggered by a button click should travel this path.

Constraints encoded here (from the official docs):

* group passive replies stay valid for 5 minutes, at most 5 replies per event;
* ``msg_id + msg_seq`` must be unique, so ``msg_seq`` is a monotonic counter
  rather than a random int (random ints collide and fail opaquely);
* rich media must be uploaded first, then referenced through ``media.file_info``
  with ``msg_type=7``.
"""
from __future__ import annotations

import base64
import itertools
import os
from pathlib import Path
from typing import Any

from astrbot.api import logger

# QQ rich media file types (v2 /files endpoint).
IMAGE_FILE_TYPE = 1
VIDEO_FILE_TYPE = 2
RECORD_FILE_TYPE = 3
FILE_FILE_TYPE = 4

# Docs: a passive reply may be used at most 5 times per event.
MAX_PASSIVE_REPLIES_PER_EVENT = 5

_msg_seq_counter = itertools.count(1)


def next_msg_seq() -> int:
    """Monotonic msg_seq.

    ``msg_id + msg_seq`` must be unique or QQ rejects the send. A random int
    (the previous approach) collides with small but non-zero probability and
    surfaces as a mysterious send failure.
    """
    return next(_msg_seq_counter)


def passive_event_id(interaction: Any) -> str:
    """Return the id QQ accepts as ``event_id`` for a passive reply.

    botpy builds ``Interaction(api, payload["id"], payload["d"])``: the *envelope*
    id becomes ``.event_id`` while ``d["id"]`` becomes ``.id``. Only ``.event_id``
    works as a passive-reply credential; ``.id`` is the interaction_id used for
    ``PUT /interactions/{interaction_id}``. Swapping them yields
    "请求参数event_id无效".
    """
    return str(getattr(interaction, "event_id", "") or "").strip()


def interaction_ack_id(interaction: Any) -> str:
    """Return the id used to ACK via ``PUT /interactions/{interaction_id}``."""
    return str(getattr(interaction, "id", "") or "").strip()


def split_chain(chain: list) -> tuple[str, list[tuple[int, str, str | None]]]:
    """Split an AstrBot message chain into text and uploadable media.

    Returns ``(text, [(file_type, source, file_name), ...])``. Unknown
    components are ignored by the caller's text extraction, but media is never
    silently dropped: everything recognised here gets uploaded.
    """
    texts: list[str] = []
    media: list[tuple[int, str, str | None]] = []
    for part in chain or []:
        name = part.__class__.__name__
        if name == "Plain":
            texts.append(str(getattr(part, "text", "") or ""))
        elif name == "Image":
            source = _image_source(part)
            if source:
                media.append((IMAGE_FILE_TYPE, source, None))
        elif name == "Record":
            source = _first_attr(part, ("file", "path", "url"))
            if source:
                media.append((RECORD_FILE_TYPE, source, None))
        elif name == "Video":
            source = _first_attr(part, ("file", "path", "url"))
            if source:
                media.append((VIDEO_FILE_TYPE, source, None))
        elif name == "File":
            source = _first_attr(part, ("file", "path", "url"))
            if source:
                media.append((FILE_FILE_TYPE, source, str(getattr(part, "name", "") or "") or None))
    return "".join(texts).strip(), media


def _first_attr(part: Any, names: tuple[str, ...]) -> str:
    for attr in names:
        value = str(getattr(part, attr, "") or "").strip()
        if value:
            return value
    return ""


def _image_source(part: Any) -> str:
    value = _first_attr(part, ("url", "file", "path"))
    if value.startswith("file:///"):
        return value[7:]
    return value


async def _upload_media(
    client: Any,
    file_type: int,
    source: str,
    file_name: str | None,
    *,
    group_openid: str = "",
    user_openid: str = "",
) -> dict | None:
    """Upload one media file and return QQ's ``file_info`` payload."""
    from botpy.http import Route

    payload: dict[str, Any] = {"file_type": file_type, "srv_send_msg": False}
    if file_name:
        payload["file_name"] = file_name

    local = source
    if local.startswith("file://"):
        local = local[7:]
    if os.path.exists(local):
        payload["file_data"] = base64.b64encode(Path(local).read_bytes()).decode("ascii")
    elif source.startswith(("http://", "https://")):
        payload["url"] = source
    elif source.startswith("base64://"):
        payload["file_data"] = source[9:]
    else:
        logger.warning("[QQHub] Unsupported media source, skipped: %.60s", source)
        return None

    if user_openid:
        payload["openid"] = user_openid
        route = Route("POST", "/v2/users/{openid}/files", openid=user_openid)
    elif group_openid:
        payload["group_openid"] = group_openid
        route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=group_openid)
    else:
        return None

    result = await client.api._http.request(route, json=payload)
    if not isinstance(result, dict) or not result.get("file_info"):
        logger.warning("[QQHub] Media upload returned no file_info: %s", str(result)[:200])
        return None
    return result


async def send_passive(
    client: Any,
    *,
    event_id: str,
    text: str,
    media: list[tuple[int, str, str | None]] | None = None,
    group_openid: str = "",
    user_openid: str = "",
    markdown: str = "",
    keyboard: dict | None = None,
) -> int:
    """Send one passive reply; returns how many QQ messages were sent.

    QQ cannot mix rich media and markdown/keyboard in a single message, so a
    chain containing both is sent as multiple messages, each carrying the same
    ``event_id`` (allowed up to 5 times per event).
    """
    if not event_id or (not group_openid and not user_openid):
        return 0

    send = client.api.post_c2c_message if user_openid else client.api.post_group_message
    target = {"openid": user_openid} if user_openid else {"group_openid": group_openid}
    sent = 0
    budget = MAX_PASSIVE_REPLIES_PER_EVENT

    if markdown:
        payload: dict[str, Any] = dict(
            target,
            msg_type=2,
            markdown={"content": markdown},
            event_id=event_id,
            msg_seq=next_msg_seq(),
        )
        if keyboard:
            payload["keyboard"] = keyboard
        await send(**payload)
        sent += 1
        budget -= 1

    for file_type, source, file_name in (media or []):
        if budget <= 0:
            logger.warning("[QQHub] Passive reply budget exhausted, dropping extra media")
            break
        uploaded = await _upload_media(
            client, file_type, source, file_name,
            group_openid=group_openid, user_openid=user_openid,
        )
        if uploaded is None:
            continue
        await send(**dict(
            target,
            msg_type=7,
            media={"file_info": uploaded["file_info"]},
            # Attach the caption to the first media message when possible.
            content=text if (text and sent == 0) else None,
            event_id=event_id,
            msg_seq=next_msg_seq(),
        ))
        if text and sent == 0:
            text = ""
        sent += 1
        budget -= 1

    if text and budget > 0:
        await send(**dict(
            target,
            msg_type=0,
            content=text,
            event_id=event_id,
            msg_seq=next_msg_seq(),
        ))
        sent += 1

    return sent
