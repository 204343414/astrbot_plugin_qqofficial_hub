# QQ Official Hub Roadmap

## ✅ 已完成：协议和编辑地基

- [x] QQ Official Markdown＋Keyboard 发送
- [x] Interaction Intent 兼容桥（不修改磁盘源码）
- [x] interaction_id in-flight/processed/ACK 状态
- [x] ACK 失败只重试 ACK，不重复业务动作
- [x] 回调超时与 ACK code 0~5
- [x] 卡片 nonce、群作用域、TTL、revision 失效
- [x] 5×5 按钮编辑与拖动排序
- [x] 灰/蓝按钮、visited_label
- [x] Type 0 URL、Type 1 直接执行、Type 2 输入框文字
- [x] everyone/group_manager/astrbot_admin/operator/specified_users 权限
- [x] reply/enter/anchor/unsupport_tips
- [x] 全屏 Markdown 编辑器与实时预览
- [x] HTTPS 图片、尺寸校验、等比例自动插入
- [x] 蓝色 HTTPS 链接
- [x] qqbot-cmd-input 蓝色参数指令文本
- [x] 点击者 OpenID 与回调后新卡 At 点击者
- [x] AstrBot Command Registry 自动扫描
- [x] Type 2 已注册指令下拉选择
- [x] Owner-scoped Action Registry
- [x] Type 1 已注册 Action 下拉选择
- [x] Action 服务端 JSON 参数快照
- [x] 已注册命令自动生成 command.<hash> Action
- [x] Type 1 合成内部 AstrBot 群消息事件，保留 TempBan/权限/CommandFilter
- [x] UI 发送真实 QQ 测试卡
- [x] 原子保存与群覆盖模板

## P0：真实客户端验收（蓝图前必须完成）

- [ ] 普通成员点击 permission.type=1 被 QQ 阻止
- [ ] 群管理点击 permission.type=1 可到达 Hub
- [ ] type=1 Loading 在 ACK 后消失
- [ ] 同一 interaction_id 不重复执行
- [ ] 热重载后无重复 Handler
- [ ] 完整重启后 Intent 正常协商
- [ ] Markdown 图片、蓝链、参数指令在手机/桌面端差异记录
- [ ] anchor=1 在支持的单聊移动端实测

## P1：子卡片和蓝图最小运行时

### 数据模型

- [ ] schema_version=2 迁移器
- [ ] 多 Panel 模板（不再只有 default_panel）
- [ ] entry_panel_id
- [ ] PanelNode
- [ ] ActionNode
- [ ] CommandInputNode
- [ ] UrlNode
- [ ] ConfirmNode
- [ ] 有类型的 Edge：source_panel/button_id → target_node

### 编译与校验

- [ ] 每个按钮最多一条出边
- [ ] 禁止悬空边和缺失节点
- [ ] URL 节点只允许 HTTPS
- [ ] Action 节点必须引用已注册 action_id
- [ ] Command 节点必须引用已注册命令或显式自定义文字
- [ ] Confirm 节点必须有确认/取消出口
- [ ] 允许游戏循环，但限制单次自动跳转深度，禁止后台无限递归
- [ ] 编译结果必须仍满足 QQ 5×5、Markdown、权限限制

### 编辑器交互（不使用右键）

- [ ] 左键单击选择节点
- [ ] 再次点击已选节点或 Enter 打开属性
- [ ] 节点显示可连线 Type 1 按钮数量
- [ ] 点击按钮端口进入连线模式
- [ ] 左键点击目标节点完成连线
- [ ] Esc 取消连线/关闭属性
- [ ] Delete/Backspace 删除选中节点或连线（输入框聚焦时不触发）
- [ ] Ctrl/Cmd+S 保存
- [ ] Ctrl/Cmd+D 复制节点
- [ ] F 适配全部节点
- [ ] Space+拖动平移；滚轮缩放
- [ ] 删除被引用节点前显示影响范围并二次确认
- [ ] 全屏蓝图模式

## P1：点击限流、状态和防刷屏

- [ ] 每群 Action 队列
- [ ] 每群/每成员滑动窗口限流
- [ ] 默认同成员同按钮短冷却
- [ ] ACK 2 表示操作频繁，不发送聊天消息
- [ ] repeat_policy：once_per_card / cooldown / unlimited
- [ ] 新面板生成后使同一流程旧卡失效
- [ ] Action 幂等键
- [ ] 游戏 session_id / turn_id / revision
- [ ] 默认回调后新卡 At 点击者（面板可关闭）
- [ ] 一个点击最多一条最终聊天消息
- [ ] Bot 总 QPM 与单群 QPM 接入未来 Commander

## P2：Action 输出模型

- [ ] ActionResult：ack_code
- [ ] ActionResult：next_panel_id
- [ ] ActionResult：Markdown变量/补丁
- [ ] ActionResult：是否 At 点击者
- [ ] ActionResult：错误卡（最多一条）
- [ ] 长任务快速 ACK＋后台任务登记
- [ ] 任务异常统一回收

## P2：小游戏验证项目（井字棋）

- [ ] 每群独立棋局
- [ ] 玩家使用 group_member_openid
- [ ] 回合锁和棋盘 revision
- [ ] 旧棋盘按钮失效
- [ ] 非当前玩家 ACK 4/2，不刷消息
- [ ] 每步只发送一张新棋盘卡
- [ ] 结束后清理 session
- [ ] 不依赖 LLM

井字棋通过后再考虑 Pokémon、GIF 战斗、扑克牌。私聊发牌需要额外维护群成员 OpenID 与 C2C user_openid 的可达关系，不能假设两者相同。

## MyRSS 安全联动（必须人工复核）

当前真实状态：SAFE / REJECT / MALICIOUS。MALICIOUS 不是外部举报。

建议流程：

- [ ] SAFE：正常推送
- [ ] REJECT：标记 seen、跳过本条、保留订阅
- [ ] MALICIOUS：严重拦截、暂停该群该源、进入管理员复核
- [ ] 不在 UI 展示违规原文/图片，只显示源、时间、指纹和抽象原因
- [ ] 记录订阅创建来源：命令点击者 OpenID / Dashboard / 迁移 legacy
- [ ] Hub 群管理按钮：确认严重违规 / 误判恢复 / 退订并拉黑源
- [ ] 只有“管理员确认严重违规”才给创建者记一次 strike
- [ ] 2~3 次确认 strike 后仅禁止该 OpenID 新增订阅
- [ ] 禁止自动向 QQ 平台举报
- [ ] 禁止一次 LLM 判定直接处罚用户
- [ ] 源账号被入侵或后续变质时，不自动归罪历史订阅者

## P2：版本、导入导出和审计

- [ ] 配置迁移 v1→v2→v3
- [ ] 导入/导出模板 JSON
- [ ] 最近 10 个 revision 与回滚
- [ ] 保存人 OpenID（脱敏显示）
- [ ] Interaction 审计：群、成员后8位、action、ACK、耗时、结果
- [ ] GROUP_DEL_ROBOT 清理群配置（需 Adapter 事件支持）
- [ ] GROUP_MSG_REJECT/RECEIVE 状态

## 明确不做

- [ ] 不执行任意 Python/Shell
- [ ] 不允许未注册 Action
- [ ] 不允许无确认的危险删除
- [ ] 不因一次 LLM 判定自动封禁订阅者
- [ ] 不假设 member_openid 与私聊 user_openid 相同
- [ ] 不在 Hub 内实现全局 Monkey Patch 发送队列
