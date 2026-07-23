"""Experimental, single-owner compatibility bridge for QQ button callbacks.

This is intentionally opt-in and must be enabled before a full AstrBot restart.
It does not edit AstrBot files on disk. Remove it when AstrBot exposes a public
QQ Official interaction API.
"""
from __future__ import annotations

import asyncio
import builtins
import logging
import time
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("astrbot")
_STATE_KEY = "_ASTRBOT_QQHUB_EXPERIMENTAL_INTERACTION_BRIDGE"
Callback = Callable[[Any, Any], Awaitable[int]]


def _state() -> dict[str, Any]:
    state = getattr(builtins, _STATE_KEY, None)
    if not isinstance(state, dict):
        state = {
            "installed": False,
            "owner": None,
            "generation": 0,
            "callback": None,
            "seen": OrderedDict(),
            "inflight": set(),
            "lock": None,
        }
        setattr(builtins, _STATE_KEY, state)
    return state


def install(owner: str, callback: Callback) -> int:
    """Install once and replace only the stable Hub owner's current callback."""
    state = _state()
    if state["owner"] not in (None, owner):
        raise RuntimeError(f"experimental QQ bridge is owned by {state['owner']}")
    state["owner"] = owner
    state["generation"] = int(state["generation"]) + 1
    generation = state["generation"]
    state["callback"] = weakref.WeakMethod(callback)  # bound plugin method
    if state["installed"]:
        return generation

    from astrbot.core.platform.sources.qqofficial import qqofficial_platform_adapter as module

    original_init = module.QQOfficialPlatformAdapter.__init__
    original_interaction = getattr(module.botClient, "on_interaction_create", None)

    def patched_init(adapter, *args, **kwargs):
        original_init(adapter, *args, **kwargs)
        adapter.intents.interaction = True
        # qq-botpy Client.intents is an int, not an Intents instance.
        adapter.client.intents = adapter.intents.value
        logger.warning("[QQHub] Experimental Interaction intent enabled; a full restart was required.")

    async def patched_interaction(client, interaction):
        await _dispatch(client, interaction)

    module.QQOfficialPlatformAdapter.__init__ = patched_init
    module.botClient.on_interaction_create = patched_interaction
    state["installed"] = True
    state["original_init"] = original_init
    state["original_interaction"] = original_interaction
    logger.warning("[QQHub] Experimental in-process Interaction bridge installed.")
    return generation


def detach(owner: str) -> None:
    """Drop a stale plugin callback without restoring live websocket handlers.

    Disabling the bridge requires a full AstrBot restart because the QQ gateway
    Intent was already sent on connection. Keeping the patched handler lets
    late events receive one failure ACK instead of spinning forever.
    """
    state = _state()
    if state.get("owner") == owner:
        state["callback"] = None


async def _ack(client: Any, interaction_id: str, code: int) -> bool:
    try:
        await client.api.on_interaction_result(interaction_id, code)
        return True
    except Exception:
        logger.exception("[QQHub] Interaction ACK failed: %s", interaction_id)
        return False


async def _dispatch(client: Any, interaction: Any) -> None:
    interaction_id = str(getattr(interaction, "id", "") or "").strip()
    interaction_type = getattr(interaction, "type", None)
    if not interaction_id or interaction_type not in {11, 12}:
        return
    state = _state()
    lock = state.get("lock")
    if lock is None:
        lock = asyncio.Lock()
        state["lock"] = lock

    retry_code = None
    async with lock:
        prior = state["seen"].get(interaction_id)
        if isinstance(prior, dict):
            if prior.get("acked"):
                logger.warning("[QQHub] Already ACKed interaction ignored: %s", interaction_id)
                return
            retry_code = int(prior.get("code", 1))
        elif interaction_id in state["inflight"]:
            logger.warning("[QQHub] In-flight duplicate ignored: %s", interaction_id)
            return
        else:
            state["inflight"].add(interaction_id)

    if retry_code is not None:
        acked = await _ack(client, interaction_id, retry_code)
        if acked:
            async with lock:
                if interaction_id in state["seen"]:
                    state["seen"][interaction_id]["acked"] = True
        return

    code = 1
    try:
        callback_ref = state.get("callback")
        callback = callback_ref() if callback_ref else None
        if callback is not None:
            code = int(await asyncio.wait_for(callback(client, interaction), timeout=4))
        if code not in {0, 1, 2, 3, 4, 5}:
            code = 1
    except TimeoutError:
        code = 1
        logger.error("[QQHub] Interaction handler timed out: %s", interaction_id)
    except Exception:
        code = 1
        logger.exception("[QQHub] Interaction handler failed: %s", interaction_id)

    async with lock:
        state["seen"][interaction_id] = {"time": time.monotonic(), "code": code, "acked": False}
        while len(state["seen"]) > 4096:
            state["seen"].popitem(last=False)
        state["inflight"].discard(interaction_id)

    acked = await _ack(client, interaction_id, code)
    if acked:
        async with lock:
            if interaction_id in state["seen"]:
                state["seen"][interaction_id]["acked"] = True
