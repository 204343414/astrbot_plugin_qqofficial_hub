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
- 编辑器内置**模板库**：一键插入排版片段与动态占位符，**按需插入，不插入就不出现**；
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


## 具名卡片（多卡片库）

编辑器左上角「卡片」区可在 **Hub 主面板** 与任意具名卡片间切换，并新建 / 删除。

新建：在输入框填编号 → 点「新建」。删除：点「删除当前卡片」→ 再点「确认删除」。

> 页面**不使用** `prompt()` / `confirm()`：AstrBot 把插件页嵌在沙箱 iframe 中，
> 这两个方法会被禁用且**静默失败**（函数存在、返回 undefined、不报错），
> 表现为「点了没反应」。所有交互均为页内元素。

| 能力 | 说明 |
| --- | --- |
| 卡片编号 | 字母/数字/下划线/短横线，最长 40 位，**全局唯一** |
| 触发指令 | 可留空；填写后群里发 `/指令` 直接打开该卡片 |
| 冲突检测 | 保存时比对全部已注册 AstrBot 命令**及其别名**，冲突则拒绝 |
| next_card | 按钮可指向任意卡片编号，实现菜单树与循环 |

具名卡片是**全局**的（不分群），因为 `next_card` 的目标必须在任何群都能解析到同一张卡。
保存时 `revision` 自增，旧回调按钮自动失效。

### 运行诊断（编辑器内抽屉）

点编辑器右上角 **运行诊断** 打开只读抽屉。

> 为什么不做成独立页面：AstrBot 侧边栏只为每个插件链接**第一个**页面
> （`usePluginSidebarItems` 取 `p.pages[0]`），第二个页面没有任何入口。

实时枚举：

- **已注册 Action**，按所属插件分组并标注「外部插件 / Hub 自带」；
- **卡片提供者**（`next_card` 目标）及其所属模块；
- **Interaction 兼容桥**状态（是否安装、回调是否存活、处理哪些 type）；
- **模块加载**：逐个 import 校验，损坏的模块会显示原因；
- **对外 API**：配套插件依赖的接口是否齐全（缺失通常意味着 Hub 版本过旧）；
- **存储**：已观察群、存活的一次性卡片与会话数。

全部**实时枚举而非写死清单**：外部插件卸载后刷新即消失，新增 Hub 模块若忘记登记，
测例 `test_no_hub_module_is_missing_from_the_report` 会直接失败。

## 一次性卡片（流程 / 游戏）

除「每群一张、改版即失效」的配置面板外，Hub 还提供**一次性卡片**：由其他插件
用代码生成，支持 `one_shot`（点一次即失效）、`owner_mode`（所有人／仅发起者／指定 OpenID）、
`next_card`（静态跳转，可表达分支与循环）、`session_id`（整局回收）。

卡片编辑器左侧「一次性卡片」与按钮的「一次性设置」可勾选这些开关，并用顶部**「按一次性卡片发送」**按钮实测（普通「发送到群测试」走配置面板，不吃这些开关）。

游戏请写成独立插件，通过 `hub.actions.register(...)` 与
`hub.send_ephemeral_card(...)` 接入，Hub 只提供机制。

完整 API 与 QQ 平台限制见 **[docs/EPHEMERAL_CARDS.md](docs/EPHEMERAL_CARDS.md)**。

## 操作者身份与防刷

QQ 的按钮回调事件（`INTERACTION_CREATE`）**只带乱码 OpenID，不带昵称**；昵称只出现在
入站消息（`GROUP_AT_MESSAGE_CREATE` 的 `author.username`）里。由此有两个配置：

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `require_known_clicker` | 开 | 未曾与机器人说过话的人点击按钮会被拒绝（ACK code 4），防止陌生人一键刷卡卡群 |
| `show_clicker_name` | 开 | 卡片最顶部加一行「👤 昵称」，表明这张卡属于谁 / 是谁按的。调用 `send_ephemeral_card` 只需传 `initiator_openid`，顶部行会自动生成；传 `clicker_header=""` 可抑制 |

- 昵称在**每次**入站消息时刷新，因此改名会在对方下次说话后自动更新；
- 显示为**纯文本**而非蓝色 @ —— 实测 QQ 群主动消息路径会把 At 标签原样显示；
- 昵称会去除换行与 Markdown 字符，避免破坏卡片排版；
- 身份**按群隔离**，不跨群继承；30 天未出现即遗忘。

> 关于「双击头像戳一戳」：QQ 官方机器人 API **不推送**戳一戳事件
> （群场景 `1<<25` 只有消息与成员进出类事件，`Poke` 组件仅服务于 OneBot/NapCat），
> 因此没有提供该开关。想要「不打字就唤出面板」，请用已有的
> `empty_mention_opens_panel`：群里空 @ 机器人即可弹出 Hub 面板。

## 平台隔离

插件所有事件入口都带 QQ 官方适配器门禁：

```python
@filter.platform_adapter_type(
    filter.PlatformAdapterType.QQOFFICIAL
    | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK
)
```

适合「NapCat 号跑重型/高风险插件 + 官方号只跑安全插件」的双号部署：即使两个账号
挂在同一个 AstrBot 上，Hub 的指令与事件也只会在官方号上触发，不会污染 NapCat 号。

关于 `metadata.yaml` 里的 `support_platforms`：

| 字段 | 作用 |
| --- | --- |
| `metadata.yaml` 的 `support_platforms` | **仅 WebUI 展示标签**。AstrBot v4.26.7 把它读进 `StarMetadata` 交给 dashboard，事件流水线从不读取 |
| `@filter.platform_adapter_type(...)` | **真正的运行时过滤**，未匹配的平台不会进入 handler |

> ⚠️ 只写 `support_platforms` 不能阻止插件在其它平台被触发，两者必须都写。

同类型的多个账号（例如两个 QQ 官方 Bot）无法用 `platform_adapter_type` 区分，
但 Hub 的数据本身按 `平台ID:GroupMessage:群openid` 分片存储，配置天然互不干扰。

## 编辑器模板库与动态占位符

卡片编辑页「Markdown 文案」下方有**模板库**，分组列出可一键插入光标处的片段：

- **排版**：标题、粗体、引用、分隔线、列表；
- **状态占位符**（标记为 `动态`）：发送时替换为实时内容。

占位符是**完全可选的**。做蓝图、桌游这类卡片时不插入即可，卡片不会有任何多余内容，
渲染器遇到不含 `{{` 的正文会直接原样返回。

### 现有动态占位符

| 占位符 | 发送时替换为 |
| --- | --- |
| `{{group_openid_short}}` | 本群 openid 后 8 位，便于多群蓝图区分 |

编辑器预览会用示例值渲染占位符，所见即所得。

### 新增一个动态占位符

1. 在 `qqofficial_hub/snippets.py` 的 `SNIPPETS` 里追加一条（编辑器会自动列出，无需改前端）；
2. 在 `main._render_dynamic_markdown` 里解析它。

目录是服务端下发的，所以未来蓝图相关的新占位符不用动 UI 代码。

> 🚫 **不要添加"主动推送是否开启"这类占位符。** QQ 没有查询该设置的接口，
> 只在开关**变更时**推送 type=18/19 事件；装插件前就已设置好、或从未变更过的群
> 永远不会有信号，占位符只能长期显示"未知"。曾经实现过并已移除，详见
> [docs/QQ_OFFICIAL_REFERENCE.md](docs/QQ_OFFICIAL_REFERENCE.md)。
