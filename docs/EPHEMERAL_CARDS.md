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
| `owner_openid` | 绑定 OpenID，别人点击返回"这不是你的操作" |
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
    "owner_openid": "OPENID_A",    # 仅此人可点，留空=所有人
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

## 发送卡片

```python
hub = context.get_registered_star("astrbot_plugin_qqofficial_hub").star_cls_obj

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
