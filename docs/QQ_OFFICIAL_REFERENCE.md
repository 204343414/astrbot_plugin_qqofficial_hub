# QQ 官方机器人 接口与类型 完整速查

> 给未来的人类与 LLM：动手前先读这里，**不要臆猜接口**。
> 每条都出自官方文档，链接在文末。与文档冲突时以官方为准，并回来修正本文件。

---

## 0. 最容易踩的五个坑（先看这个）

| # | 坑 | 正确做法 |
| --- | --- | --- |
| 1 | `interaction.id` 当成 `event_id` 用 | botpy 里 `.event_id` 才是被动消息凭据；`.id` 只用于 ACK。传错报 `请求参数event_id无效` |
| 2 | 以为 type=1 按钮回消息必须要主动推送权限 | 带上 `event_id` 就是**被动消息**，不需要权限、不占主动配额 |
| 3 | `msg_seq` 用随机数 | `msg_id + msg_seq` 必须唯一，随机数会碰撞；用单调递增计数器 |
| 4 | 富媒体和 markdown/keyboard 塞进同一条消息 | 不支持。富媒体走 `msg_type=7`，需分成多条发送 |
| 5 | 不回 `PUT /interactions/{id}` | 客户端会一直 loading 到超时。仅 type=11/12 需要回 |

---

## 1. 主动消息 vs 被动消息（核心概念）

- **主动消息**：发送时**未**填充 `msg_id`/`event_id`。
- **被动消息**：填充了 `msg_id` **或** `event_id`（任一即可）。

### 额度限制

| 场景 | 主动消息 | 被动消息 |
| --- | --- | --- |
| 群聊 | 每月 4 条（同一群） | 有效期 **5 分钟**，每条消息最多回 **5 次** |
| 单聊 | 每月 4 条（同一用户） | 有效期 **60 分钟**，最多回 5 次 |
| 文字子频道 | 默认每天每子频道 20 条，每天最多 2 个子频道 | 有效期 5 分钟 |
| 频道私信 | 每天每用户 2 条，累计 200 条 | 有效期 5 分钟 |

> 任何子频道内，不论主动被动，**每秒最多 5 条**。

### `event_id` 支持的事件

| 场景 | 可用作 event_id 的事件 |
| --- | --- |
| 群聊 | `INTERACTION_CREATE`、`GROUP_ADD_ROBOT`、`GROUP_MSG_RECEIVE` |
| 单聊 | `INTERACTION_CREATE`、`C2C_MSG_RECEIVE`、`FRIEND_ADD` |

---

## 2. 发送消息接口

`POST /v2/groups/{group_openid}/messages` ／ `POST /v2/users/{openid}/messages`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `content` | string | 文本内容 |
| `msg_type` | int | **0** 文本、**2** markdown、**3** ark、**4** embed、**7** media 富媒体 |
| `markdown` | object | Markdown 对象 |
| `keyboard` | object | Keyboard 对象 |
| `media` | object | 富媒体 `file_info` |
| `ark` | object | Ark 模板 |
| `message_reference` | object | 消息引用 |
| `event_id` | string | 前置事件 ID → **被动消息** |
| `msg_id` | string | 前置用户消息 ID → **被动消息（回复）** |
| `msg_seq` | int | 回复序号，与 msg_id 联用；相同组合重复发送会失败 |
| `is_wakeup` | bool | 仅单聊：互动召回消息，与 msg_id/event_id 互斥 |

botpy 签名：
```python
await client.api.post_group_message(group_openid=..., msg_type=0, content=...,
    media=..., markdown=..., keyboard=..., msg_id=..., event_id=..., msg_seq=...)
await client.api.post_c2c_message(openid=..., ...)   # 单聊，参数同上
```

---

## 3. 富媒体

`POST /v2/groups/{group_openid}/files` ／ `POST /v2/users/{openid}/files`

| `file_type` | 含义 |
| --- | --- |
| 1 | 图片 png/jpg |
| 2 | 视频 mp4 |
| 3 | 语音 silk |
| 4 | 文件（暂不开放） |

请求体：`file_type`、`url` 或 `file_data`(base64)、`srv_send_msg`（false=只上传不发送）、`file_name`。
返回 `file_info`，再以 `msg_type=7` + `media.file_info` 发送。

- 视频软限制 30MB，硬限制 200MB。
- **富媒体不能与 markdown/keyboard 同条发送。**

---

## 4. 消息按钮（Keyboard）

`action.type`：
- **0** 跳转按钮：http / 小程序 scheme
- **1** 回调按钮：触发 `INTERACTION_CREATE`，`data` 传回后台
- **2** 指令按钮：在输入框插入 `@bot data`

`action.permission.type`：
- **0** 指定用户 (`specify_user_ids`)
- **1** 仅管理者
- **2** 所有人
- **3** 指定身份组 (`specify_role_ids`，仅频道)

其它：`render_data.label` / `visited_label` / `style`(0灰1蓝)、`action.reply`、`action.enter`、
`action.anchor`(1=唤起选图器，仅单聊手机端8983+)、`action.unsupport_tips`（必填）。
最多 **5 行 × 每行 5 个**。`click_limit` 与 `at_bot_show_channel_list` 已弃用。

### 实测结论（本仓库）

- type=2 指令按钮是否自动 @ 点击者，取决于「获取全部群消息」还是「仅@机器人消息」权限。
- 群聊主动消息路径里 `<qqbot-at-user id="..."/>` 与 `<@openid>` 都可能被**原样显示**，不要画蛇添足。

---

## 5. `INTERACTION_CREATE` 互动事件

**Intent：`1 << 26`**（botpy: `Intents(interaction=True)`）

### 事件体

| 字段 | 说明 |
| --- | --- |
| `id` | **事件 ID**，用于被动消息发送**和**互动回调 |
| `type` | 互动类型，见下表 |
| `scene` | `c2c` / `group` / `guild` |
| `chat_type` | 0 频道、1 群聊、2 单聊 |
| `timestamp` | RFC3339 |
| `guild_id` / `channel_id` | 仅频道 |
| `user_openid` | 仅单聊 |
| `group_openid` / `group_member_openid` | 仅群聊 |
| `data.resolved` | 见下 |
| `application_id` | 机器人 AppID |

### type 全表

| type | 含义 | 需要 ACK |
| --- | --- | --- |
| 11 | 消息按钮回调 INLINE_KEYBOARD | ✅ |
| 12 | 单聊快捷菜单 CALLBACK_COMMAND | ✅ |
| 13 | 消息反馈 MESSAGE_FEEDBACK（点赞/点踩） | ❌ |
| 14 | 清空会话 CLEAR_SESSION（code=0 会下发小灰条） | ❌ |
| 15 | 进出故事集 IN_OUT_STORY | ❌ |
| 16 | 切换模型 SWITCH_MODEL | ❌ |
| 18 | 用户授权 USER_AUTHORIZE | ❌ |
| 19 | 群授权 GROUP_AUTHORIZE | ❌ |
| 20 | 群授权状态变更 GROUP_AUTHORIZE_STATUS | ❌ |

### `data.resolved` 字段

| 字段 | 说明 |
| --- | --- |
| `button_data` | 按钮 data（type=11）；消息反馈时为回调数据 |
| `button_id` | 按钮 id |
| `user_id` | 操作用户 ID，仅频道 |
| `feature_id` | 功能 ID，**仅快捷菜单**（管理端设置） |
| `message_id` | 操作的消息 ID |
| `feedback_opt` | `LIKE` / `UNLIKE`，仅 type=13 |
| `checked` | 反馈是否选中，仅 type=13 |
| `action` | `ENTER_STORY`/`QUIT_STORY`(15)、切换模型动作(16) |
| `message_scene.ext` | 扩展 KV，如 `disable_net_search=1` |
| `authorize_data` | **仅 18/19**：`opt_scene`(setting/dialog)、`scope`(`c2c_push`/`group_push`) |

> `authorize_data.scope` 是判断「该群/该用户是否授权主动推送」的**权威信号**。

---

## 6. 互动事件响应 ACK

`PUT /interactions/{interaction_id}`，限频 **50 QPS**。

- `interaction_id` 取自事件的 **`id`** 字段（botpy: `interaction.id`）。
- 请求体 `code`：**0** 成功、**1** 操作失败、**2** 操作频繁、**3** 重复操作、**4** 没有权限、**5** 仅管理员操作。
- 同一 interaction_id **只能回应一次**，超时失效。
- 仅 type=11/12 需要；其它类型调用也不报错但无意义。

错误码：630001 参数非法、630002/630006 Authorization 有误、630003 AppID 与 interaction_id 不匹配、
630004/630005 稍后重试、630007 请求体过大、630008 预处理失败。

botpy：`await client.api.on_interaction_result(interaction_id, code)`

---

## 7. 群/单聊生命周期事件

**Intent：`GROUP_AND_C2C_EVENT (1 << 25)`**

`C2C_MESSAGE_CREATE`、`FRIEND_ADD`、`FRIEND_DEL`、`C2C_MSG_REJECT`、`C2C_MSG_RECEIVE`、
`GROUP_AT_MESSAGE_CREATE`、`GROUP_ADD_ROBOT`、`GROUP_DEL_ROBOT`、`GROUP_MSG_REJECT`、`GROUP_MSG_RECEIVE`

> AstrBot v4.26.7 的 `qq_official` 适配器**只**原生处理 `on_group_at_message_create`
> 与 `on_group_message_create`，其余需运行时 hook `botClient`（见 `interaction_bridge.py`）。

---

## 8. botpy 关键对象映射（极易混淆）

`connection.py` 中：
```python
Interaction(self.api, payload.get('id'), payload.get('d', {}))
```
`Interaction.__init__(self, api, event_id, data)`：

| 属性 | 来源 | 用途 |
| --- | --- | --- |
| `.event_id` | WS 信封 `payload["id"]` | **被动消息的 event_id** |
| `.id` | 事件体 `d["id"]` | **ACK 的 interaction_id** |

**两者都叫 id，绝不可互换。** 统一走 `passive_reply.passive_event_id()` / `interaction_ack_id()`。

其它：`Intents.interaction = 1 << 26`；`Client.intents` 是 **int**，不是 Intents 实例。

---

## 9. Markdown 限制（本插件校验规则）

- 图片：`![说明 #宽px #高px](https://...)`，宽 ≤720、高 ≤1080。
- 蓝色链接：显示文字须以 `🔗` 开头；URL 需在 q.qq.com 后台「消息URL配置」预先报备，否则发送失败。
- 参数指令：`<qqbot-cmd-input text="..." show="..." reference="true|false"/>`，
  text/show 需 URL encode 且解码后 1~100 字符。
- 群控制面板不支持 `<qqbot-cmd-enter>`。

---

## 10. 官方文档索引

- 消息按钮 https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html
- 互动事件 https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/interaction_create.html
- 互动事件响应 https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/interactions_interaction_id.put.html
- 发送消息 https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/send-receive/send.html
- 富媒体 https://bot.qq.com/wiki/develop/api-v2/server-inter/message/send-receive/rich-media.html
- 文本交互（text-chain）https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/text-chain.html
- 事件订阅 Intents https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html
- 群管理事件 https://bot.q.qq.com/wiki/develop/api-v2/server-inter/group/manage/event.html
