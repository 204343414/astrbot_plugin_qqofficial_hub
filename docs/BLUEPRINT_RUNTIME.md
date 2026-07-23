# Hub 蓝图运行时地基

## 分层

1. **编辑器层**：浏览器画布，只负责编辑节点、坐标和连线。
2. **持久化层**：`data/plugin_data/astrbot_plugin_qqofficial_hub/panels.json`，原子写入。
3. **蓝图模型层**：`qqofficial_hub.blueprint` 独立验证根节点、节点类型、按钮端口和连线。
4. **编译层（下一步）**：根据当前群和当前节点，把一个 panel node 编译为 QQ Markdown + Keyboard。
5. **运行时层（下一步）**：签发 `origin + node_id + revision + nonce + expiry`；Interaction 只允许从当前卡按钮跳到该蓝图的合法目标。

## 不变量

- 根节点必须是 `panel`。
- 只有 `panel` 节点能发出连线。
- 一张卡的一个按钮端口最多连到一个目标。
- 指令、URL、Hub 动作、确认都是目标节点，不是任意 Python / Shell 执行入口。
- 浏览器前端不是可信来源；所有图结构必须由后端再次校验。
- 业务命令节点不会由 Hub 直接调用 Python Handler；它们最终编译为 QQ `action.type=2`。
