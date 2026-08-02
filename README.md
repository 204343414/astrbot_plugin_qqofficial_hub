# QQ Official Hub

QQ 官方机器人 Markdown＋Keyboard 可视化编辑器与 Interaction 安全中枢。

## 当前能力

- 全局模板与已观察群覆盖模板；
- QQ 自定义 Markdown 预览：标题、粗体、删除线、引用、列表、蓝色 HTTPS 链接、公网图片；
- 最多 5 行×5 个按钮；
- 拖动按钮排序；
- 灰色/蓝色按钮与点击后文字；
- 动作：URL 跳转、后台 Interaction 回调、插入指令；
- 权限：所有人、仅群管理、指定 OpenID、预留 AstrBot 管理员/Hub 操作员；
- reply、enter、anchor（📎选图器）和不支持提示配置；
- UI 保存、预览并向已观察 QQ Official 群发送测试卡；
- 自动扫描 AstrBot 当前已注册指令；type=2 按钮可从目录选择命令并自动填充 data、参数签名和管理员标记；
- 进程级 Action Registry；业务插件可按 owner 注册 type=1 直接执行动作，ID 冲突会拒绝；
- AstrBot 已注册命令会自动生成 `command.<hash>` Action；点击后以点击者 OpenID/当前群构造内部消息并重新进入正常 CommandFilter/Permission/TempBan 流水线，群里不显示用户指令；
- type=1 按钮支持服务端 JSON 参数（≤2048字节），命令 Action 使用 `{"arguments":"..."}`；参数不直接暴露在 QQ button_data；
- 编辑器内置**模板库**：一键插入排版片段与动态占位符（如推送状态指示灯），**按需插入，不插入就不出现**；
- 📖 **[QQ 官方接口与类型完整速查](docs/QQ_OFFICIAL_REFERENCE.md)** —— 动手前必读，含全部 interaction type、ACK code、额度限制与已知陷阱；
- type=1 回调回复走**被动消息**：以 `INTERACTION_CREATE` 事件 ID 作为 `event_id` 下发，因此**无需群主开启主动推送权限**，也不消耗每月 4 条主动配额；每个事件最多用 5 次（官方上限），支持图片/语音/视频等富媒体（自动上传后以 msg_type=7 发送），失败或用尽自动回退主动推送路径；
- `/qqhub` 发送当前群面板（`/qqhub 面板` 仍兼容）；
- 可选 Interaction 兼容桥：单 owner、稳定 generation、interaction_id 去重、4秒回调超时、ACK。

## QQ 已知边界

- Markdown 必须有正文，不能只发 Keyboard；
- 自定义 Keyboard 最多 5×5；
- style 仅 0 灰色、1 蓝色；
- action.type 0=URL、1=后台回调、2=插入指令；
- permission.type 0=指定用户、1=仅管理者、2=所有人；
- enter 自动发送仅单聊有效；
- anchor=1 仅 type=2，单聊移动端唤起选图器；
- type=2 data 按官方文本交互限制最多100字符；
- Markdown 图片必须公网 HTTPS，并显式填写宽高，建议不超过720×1080；
- 蓝色链接显示文字应以 🔗 开头。

## Interaction 桥

AstrBot v4.26.7 未原生转发 `INTERACTION_CREATE`。实验桥在进程内为 QQ Official Adapter 开启 `interaction=1<<26`，并接入 botpy `on_interaction_create`。它不修改 AstrBot 磁盘源码，但属于运行时兼容层。

启用：

```json
{
  "experimental_interaction_bridge": true
}
```

然后完整重启 AstrBot。热重载不能重新协商 WebSocket Intent。

若 QQ gateway 报无 Intent 权限，关闭配置并完整重启。

## 命令

```text
/qqhub
/qqhub 面板
```

`/qqhub` 直接发送当前群面板；`/qqhub 面板` 保留为兼容入口。成功时只发送一张面板；失败时只回复一条错误。

配置 `empty_mention_opens_panel=true` 时，在 QQ 官方群里只 @ 机器人且不输入其他内容，会直接发送当前群面板；关闭后会提示“请@我输入 /qqhub 查看功能”。

私聊/C2C 暂不发送 Hub 卡片；当普通 LLM 对话关闭时，用户私聊机器人会收到“请在群聊中 @我 输入 /qqhub 查看功能。”的短提示。


## 编辑器模板库与动态占位符

卡片编辑页「Markdown 文案」下方有**模板库**，分组列出可一键插入光标处的片段：

- **排版**：标题、粗体、引用、分隔线、列表；
- **状态占位符**（标记为 `动态`）：发送时替换为实时内容。

占位符是**完全可选的**。做蓝图、桌游这类卡片时不插入即可，卡片不会有任何多余内容，
渲染器遇到不含 `{{` 的正文会直接原样返回。

### 现有动态占位符

| 占位符 | 发送时替换为 |
| --- | --- |
| `{{push_lamp}}` | 🟢 已开启 / 🔴 未开启 / ⚪ 未知 |
| `{{push_status}}` | 「当前群未开启主动消息推送功能」等说明文字 |
| `{{group_openid_short}}` | 本群 openid 后 8 位，便于多群蓝图区分 |

编辑器预览会用示例值渲染占位符，所见即所得。

### 推送状态的三态与来源

按可信度排序，高优先级不会被低优先级覆盖：

| 来源 | 说明 |
| --- | --- |
| `authorize` | QQ 授权事件（type=18/19，`scope=group_push`），最权威 |
| `send` | 主动推送的真实结果：失败原因明确指向"未开启主动消息"，或适配器静默跳过了发送 |

> 🚫 **不要**用 AstrBot 的 `_allow_group_proactive_send` 判断。它是**硬编码的 `True`**，
> 含义是"AstrBot 会尝试主动发送"，而非"该群已开启推送"。曾据此点亮绿灯，
> 导致未开启推送的群显示"已开启"。该来源已被列入 `DISTRUSTED_SOURCES`，
> 历史上写入的错误值在读取时会被作废为「未知」。

被动消息不参与推断。审核中、限频、参数错误等**无关失败不会**被误判为"未开启"。

> ⚠️ 注意：AstrBot 的 `qq_official` 适配器在不允许主动推送时是**静默 return**（只打 warning，不抛异常），
> 因此"没报错"并不能证明消息发出去了，这条路径不上报"已开启"。

### 其他插件上报

任何尝试主动推送的插件都可以把结果告诉 Hub，让灯立刻准确：

```python
hub = context.get_registered_star("astrbot_plugin_qqofficial_hub")
await hub.star_cls_obj.report_push_result(origin, exc)   # exc=None 表示成功
```

> ⚠️ 为什么必须有「未知」：QQ 只在授权**发生变化时**推事件。装插件之前就已授权、或从未授权过的群，
> 我们收不到任何事件。此时若显示「未开启」就是误报，因此默认「未知」。

文案刻意不使用「权限」二字（易引起抵触），描述为「功能未开启」。灯与文案可在
`push_status_display` 配置中覆盖。

### 新增一个动态占位符

1. 在 `qqofficial_hub/snippets.py` 的 `SNIPPETS` 里追加一条（编辑器会自动列出，无需改前端）；
2. 在 `main._render_dynamic_markdown` 里解析它。

目录是服务端下发的，所以未来蓝图相关的新占位符不用动 UI 代码。
