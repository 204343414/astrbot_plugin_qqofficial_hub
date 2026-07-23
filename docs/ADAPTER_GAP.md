# AstrBot 4.26.7 QQ Official interaction gap — verified 2026-07-23

The reviewed source is AstrBot 4.26.7, revision
`2035dbd079375046ce7b82171a04b1f9a63d781a`.

## Confirmed working surface

- `qqofficial_message_event.py` supports native `markdown` and `keyboard`
  parameters on its C2C message helper.
- `qqofficial_platform_adapter.py` creates `botpy.Intents` and a `botClient`.
- The current adapter sets public message intents only. It has no
  `interaction=True` setting.

## Blocking gap

A repository-wide review of `astrbot/core/platform/sources/qqofficial` found
no `INTERACTION_CREATE`, `on_interaction_create`, `on_interaction_result`, or
interaction callback bridge. Therefore an ordinary AstrBot plugin cannot
safely receive and ACK QQ button callbacks on this release without either:

1. a small, explicit QQ Official adapter extension; or
2. an upstream AstrBot implementation that exposes this capability.

Monkey-patching the client or replacing global botpy handlers from a plugin is
explicitly rejected: it is fragile across reloads and can duplicate handlers
or ACKs.

## Required adapter patch contract (not yet written)

The patch must:

1. set `adapter.intents.interaction = True`, then assign
   `client.intents = adapter.intents.value` at the verified botpy boundary;
2. receive `INTERACTION_CREATE` once;
3. expose a narrow, lifecycle-safe listener registration API to Hub;
4. let Hub ACK exactly once through the owning client;
5. not change message dispatch, RSS scheduling, or other plugins.

No Hub runtime integration will be claimed before this contract is implemented
and tested against the actual installed Adapter and `qq-botpy==1.2.1`.
