# 一次性卡片（Ephemeral Card）—— 流程与游戏的地基

给游戏 / 问卷 / 菜单树插件作者。Hub 只提供机制，业务放在你自己的插件里。

---

## 为什么不用配置面板

配置面板是**每群一张**，靠 `revision` 失效：改版后旧卡立即作废，防止旧卡执行新权限。
这对配置是对的，对动态内容是致命的——游戏每回合都要发新卡，若每步都改
`group_overrides`，既污染全群配置，又会让旧卡可被重复点击（作弊）。

所以一次性卡片有独立存储与规则：

| 能力 | 说明 |
| --- | --- |
| `one_shot` | 卡片级或按钮级声明，点一次即失效 |
| `owner_mode` | `everyone` / `initiator`（发起者） / `specified`（指定 OpenID） |
| `next_card` | 静态跳转，无需写代码即可表达分支与循环 |
| `session_id` | 一局游戏的所有卡片可一次性回收 |
| `ttl_seconds` | 默认 1 小时，上限 24 小时 |

---

## 卡片结构

```python
card = {
    "id": "board",                 # 可选，供 next_card 引用
    "markdown": "# 井字棋\n轮到 ⭕",
    "one_shot": True,              # 卡片级：任一按钮点后整张失效
    "owner_mode": "initiator",     # everyone / initiator / specified
    "owner_openid": "",            # 仅 specified 需要；initiator 发送时自动填
    "owner_reject_tip": "现在轮到对手",
    "ttl_seconds": 3600,
    "rows": [                      # 最多 5 行 × 每行 5 个
        [
            {
                "id": "c0",
                "label": "1",
                "style": 0,                # 0 灰 / 1 蓝
                "action_id": "ttt.move",   # 走注册的 Action
                "params": {"cell": 0},     # 服务端快照，不暴露给 QQ
                "one_shot": True,          # 按钮级：只此按钮失效
                "owner_openid": "",        # 覆盖卡片级归属
            },
            {"id": "quit", "label": "退出", "next_card": "menu"},  # 静态跳转
        ]
    ],
}
```

`action_id` 与 `next_card` **至少填一个**。

---

## 在编辑器里试发

卡片编辑页有两个发送按钮：

| 按钮 | 走的路径 | 是否吃 `one_shot` / `owner_mode` |
| --- | --- | --- |
| 发送到群测试 | 配置面板（每群一张，改版失效） | ❌ 忽略 |
| **按一次性卡片发送** | 一次性卡片 | ✅ 生效 |

一次性发送只保留 **type=1 回调按钮**：URL 与「填入输入框」按钮不会回到服务端，
无法执行一次性与归属校验，因此会被丢弃而不是装作生效。

## 发送卡片

```python
star = context.get_registered_star("astrbot_plugin_qqofficial_hub")
hub = star.star_cls if star else None          # star_cls 是实例，star_cls_type 是类

session_id = await hub.send_ephemeral_card(
    origin,                 # "平台ID:GroupMessage:群openid"
    card,
    event_id=event_id,      # 有则走被动消息，不消耗主动配额
    session_id=session_id,  # 续用同一局
)
```

> 优先传 `event_id`（来自 `INTERACTION_CREATE`）或 `msg_id`，否则退化为主动推送，
> 需要群主开启主动消息且每月仅 4 条。**游戏必须由点击驱动**，见下方限制。

---

## 处理点击

### 方式一：注册 Action（适合游戏）

```python
from astrbot_plugin_qqofficial_hub.qqofficial_hub.action_registry import ActionSpec

async def on_move(context, params):
    cell = params["cell"]
    session = params["_session_id"]        # Hub 自动注入
    ...                                     # 更新棋盘
    await hub.send_ephemeral_card(
        context.origin, next_board_card(),
        client=context.client,
        session_id=session,
        event_id=passive_event_id(context.interaction),
    )
    return 0                                # ACK：0成功 1失败 3重复 4无权限

hub.actions.register(ActionSpec(
    action_id="ttt.move", title="井字棋落子", description="",
    owner="astrbot_plugin_tictactoe", default_permission="everyone",
    callback=on_move,
))
```

**记得在 `terminate()` 里 `hub.actions.unregister_owner("你的插件名")`。**

### 方式二：注册卡片提供者（适合菜单树 / 循环）

```python
async def build_menu(ctx):          # ctx: EphemeralContext
    return {"markdown": "# 主菜单", "rows": [[
        {"id": "a", "label": "玩井字棋", "next_card": "board"},
        {"id": "b", "label": "关于", "next_card": "about"},
    ]]}

hub.register_card_provider("menu", build_menu)
```

`about` 卡片里再放一个 `next_card: "menu"` 即构成**循环**，全程零业务代码。

---

## 结束一局

```python
await hub.end_ephemeral_session(session_id)   # 该局所有卡片立即失效
```

---

## 并发与安全

- 校验与消费在**同一次加锁**内完成，两个人同时点 `one_shot` 按钮只有一个能成功
  （测例 `test_concurrent_clicks_cannot_both_win` 钉死这一点）；
- `button_data` 只含 `qqhub:e1:{nonce}:{button_id}`，真实参数存服务端；
- nonce 跨群重放会被拒（`CODE_FORBIDDEN`）；
- 过期卡片自动清理，总量硬上限 2000 条。

---

## QQ 平台限制（设计前必读）

| 限制 | 影响 |
| --- | --- |
| 被动消息 5 分钟内最多 5 条 | 一次点击最多回 5 张卡 |
| 群主动消息每月 4 条 | **不能靠定时器推进游戏**，必须点击驱动 |
| 按钮上限 5×5 | 井字棋 3×3 可以；需要更多选项要分页 |
| 群卡片人人可见 | 无法隐藏私密信息 |

**因此**：回合制、公开信息、点击驱动的游戏（井字棋、五子棋、投票、抽卡）可行；
需要私密信息（狼人杀夜晚）或定时推进的游戏会被额度卡死——私聊主动消息同样每月 4 条。


---

## 归属模式与两种非法情况

`owner_mode` 有三种取值，编辑器与后端都会校验：

| 模式 | 含义 | 何时解析 |
| --- | --- | --- |
| `everyone` | 所有人可点（默认） | — |
| `initiator` | 仅**发起者**，即点击/触发本卡的人 | **发送时** |
| `specified` | 指定字面 OpenID | 编辑时 |

### 非法一：`specified` 但 OpenID 为空

锁上了却没有钥匙 —— 谁都匹配不上，**卡片变砖**。校验直接拒绝。

### 非法二：`initiator` 但本次发送没有发起者

这正是主动推送、定时任务、WebUI「发送到群测试」的情况：无人点击，
何来发起者。此时 `bind_initiator()` 会**抛错而不是降级为"所有人"** ——
静默降级会让卡片看起来锁着、实际全群可点，属于安全事故。

```python
await hub.send_ephemeral_card(origin, card, initiator_openid=member)  # 由点击触发
await hub.send_ephemeral_card(origin, card)   # 无发起者：若卡片要 initiator 则报错
```

> 兼容性：只写 `owner_openid` 不写 `owner_mode` 的老卡片，自动视为 `specified`。
> `owner_mode="everyone"` 时残留的 `owner_openid` 会被清空，避免"看起来没锁其实锁着"。


## msg_id 的陷阱

由 type=1 按钮触发的命令走的是**合成事件**，其 `message_obj.message_id` 是
Hub 伪造的 `hub-interaction:<uuid>`——AstrBot 需要它做键，但 QQ 不认识。
直接转发给 QQ 会得到：

```
ServerError: 请求参数msg_id无效或越权
```

`send_ephemeral_card()` 会自动用 `real_msg_id()` 过滤掉这类伪造 id，
所以调用方可以放心地把 `event.message_obj.message_id` 传进来。
优先传 `event_id`（来自 `INTERACTION_CREATE`）仍然是最稳的做法。
