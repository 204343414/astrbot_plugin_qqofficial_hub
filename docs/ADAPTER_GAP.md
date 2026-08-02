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


## type=1 回调按钮：被动消息 vs 主动推送

早期实地测试得出的「type=1 需群主开启主动消息推送才能用」是**实现缺口造成的假象**，不是平台限制。

官方口径（`server-inter/message/send-receive/send.html`）：

- **主动消息**：发送时未填充 `msg_id`/`event_id` 的消息。
- **被动消息**：填充了 `msg_id`/`event_id` 的消息。
- `event_id` 支持的事件明确含 `INTERACTION_CREATE`（群聊还支持 `GROUP_ADD_ROBOT`、`GROUP_MSG_RECEIVE`）。

而 `INTERACTION_CREATE` 事件体的 `id` 字段，文档注明「平台方事件 ID，可以用于被动消息发送」。

因此点击 type=1 按钮后回一条消息的正确姿势是：把 `interaction.id` 作为 `event_id` 传给发消息接口。这条回复属于被动消息，
受被动额度约束（群聊有效期 5 分钟、每事件最多回 5 次），**不需要主动推送权限，也不占用每月 4 条主动配额**。

本插件的两条回复路径均已接上：

1. `_action_refresh` → `_send_configured_panel(event_id=...)`；
2. `HubSyntheticCommandEvent.send` → 先尝试 `post_group_message(event_id=...)`，
   失败/缺失/已用尽时回退 `send_by_session` 主动推送。

`event_id` 每个事件只用一次（`_event_id_used`），避免同一事件重复下发被 QQ 拒绝。
