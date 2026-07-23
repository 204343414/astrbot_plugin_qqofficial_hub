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
- `/qqhub 面板` 发送当前群面板；
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
/qqhub 面板
```

成功时只发送一张面板；失败时只回复一条错误。
