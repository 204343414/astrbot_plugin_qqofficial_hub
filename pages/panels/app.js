const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);
let state = null;
let selected = null;
let dragging = null;

function currentPanel() {
  const origin = $("group").value;
  if ($("scope").value === "group" && origin && state.group_overrides[origin]) return state.group_overrides[origin];
  return state.templates.default_panel;
}
function clonePanel(panel) { return JSON.parse(JSON.stringify(panel)); }
function editablePanel() {
  const origin = $("group").value;
  if ($("scope").value === "group" && origin && !state.group_overrides[origin]) {
    state.group_overrides[origin] = clonePanel(state.templates.default_panel);
  }
  return currentPanel();
}
function setNotice(text, error = false) { const el = $("notice"); el.textContent = text; el.className = error ? "error" : "ok"; }
function selectedButton() { return selected ? editablePanel().rows[selected.row]?.[selected.col] : null; }
function safeUrl(value) { try { const url = new URL(value); return url.protocol === "https:" ? url.href : ""; } catch { return ""; } }

function decodeValue(value) { try { return decodeURIComponent(value || ""); } catch { return value || ""; } }
function renderInline(target, text) {
  const pattern = /<qqbot-cmd-input\s+text="([^"]+)"(?:\s+show="([^"]*)")?(?:\s+reference="(true|false)")?\s*\/>|!\[([^\]]+?)\s+#(\d+)px\s+#(\d+)px\]\((https:\/\/[^\s)]+)\)|\[([^\]]+)\]\((https:\/\/[^\s)]+)\)|<(https:\/\/[^>]+)>|\*\*([^*]+)\*\*|~~([^~]+)~~/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    target.append(document.createTextNode(text.slice(cursor, match.index)));
    if (match[1]) {
      const command = document.createElement("button");
      command.className = "text-command-preview";
      command.textContent = decodeValue(match[2] || match[1]);
      command.title = `点击后写入输入框：${decodeValue(match[1])}${match[3] === "true" ? "（引用本卡）" : ""}`;
      command.onclick = () => setNotice(command.title);
      target.append(command);
    } else if (match[4]) {
      const width = Math.min(Number(match[5]), 720), height = Math.min(Number(match[6]), 1080);
      const frame = document.createElement("span");
      frame.className = "markdown-image-frame";
      frame.style.width = `${width}px`;
      frame.style.aspectRatio = `${width} / ${height}`;
      const image = document.createElement("img");
      image.alt = match[4]; image.src = safeUrl(match[7]);
      image.referrerPolicy = "no-referrer";
      frame.append(image); target.append(frame);
    } else if (match[8]) {
      const link = document.createElement("a"); link.textContent = match[8]; link.href = safeUrl(match[9]); link.target = "_blank"; link.rel = "noopener noreferrer"; target.append(link);
    } else if (match[10]) {
      const link = document.createElement("a"); link.textContent = match[10]; link.href = safeUrl(match[10]); link.target = "_blank"; link.rel = "noopener noreferrer"; target.append(link);
    } else if (match[11]) {
      const strong = document.createElement("strong"); strong.textContent = match[11]; target.append(strong);
    } else if (match[12]) {
      const del = document.createElement("del"); del.textContent = match[12]; target.append(del);
    }
    cursor = match.index + match[0].length;
  }
  target.append(document.createTextNode(text.slice(cursor)));
}
const PREVIEW_TOKENS = {
  "{{group_openid_short}}": "1A2B3C4D",
};
function previewTokens(text) {
  let out = String(text || "");
  for (const [token, sample] of Object.entries(PREVIEW_TOKENS)) out = out.split(token).join(sample);
  return out;
}
function renderMarkdown(text, targetId = "preview-markdown") {
  const root = $(targetId); root.innerHTML = "";
  for (const raw of previewTokens(text).split("\n")) {
    let line = raw, tag = "p";
    if (line.startsWith("### ")) { tag = "h3"; line = line.slice(4); }
    else if (line.startsWith("## ")) { tag = "h2"; line = line.slice(3); }
    else if (line.startsWith("# ")) { tag = "h1"; line = line.slice(2); }
    else if (line.startsWith("> ")) { tag = "blockquote"; line = line.slice(2); }
    else if (/^[-*] /.test(line)) { tag = "li"; line = line.slice(2); }
    const element = document.createElement(tag);
    if (!line) element.append(document.createElement("br")); else renderInline(element, line);
    root.append(element);
  }
}

function render() {
  const panel = editablePanel();
  $("name").value = panel.name; $("markdown").value = panel.markdown;
  $("mention-clicker").checked = Boolean(panel.mention_clicker);
  $("preview-title").textContent = panel.name; renderMarkdown(panel.markdown);
  syncCardEphemeralUi();
  if (panel.mention_clicker) {
    const mention = document.createElement("div");
    mention.className = "mention-preview";
    mention.textContent = "@点击者（仅 type=1 回调后生成的新卡）";
    $("preview-markdown").prepend(mention);
  }
  const canvas = $("canvas"); canvas.innerHTML = "";
  for (let rowIndex = 0; rowIndex < 5; rowIndex += 1) {
    const row = document.createElement("div"); row.className = "row";
    for (let column = 0; column < 5; column += 1) {
      const cell = document.createElement("div"); cell.className = "cell"; cell.dataset.row = rowIndex; cell.dataset.col = column;
      cell.addEventListener("dragover", (event) => event.preventDefault()); cell.addEventListener("drop", drop);
      const item = panel.rows[rowIndex]?.[column];
      if (item) {
        const button = document.createElement("button"); button.className = `card-button style-${item.style}`;
        button.textContent = `${item.anchor === 1 ? "📎 " : ""}${item.label}`; button.draggable = true;
        if (selected?.row === rowIndex && selected?.col === column) button.classList.add("selected");
        button.onclick = () => { selected = { row: rowIndex, col: column }; render(); };
        button.ondragstart = () => { dragging = { row: rowIndex, col: column }; };
        cell.append(button);
      }
      row.append(cell);
    }
    canvas.append(row);
  }
  renderForm();
}
function compactRows(panel) { panel.rows = panel.rows.map((row) => row || []); while (panel.rows.length && panel.rows.at(-1).length === 0) panel.rows.pop(); }
function drop(event) {
  event.preventDefault(); if (!dragging) return;
  const targetRow = Number(event.currentTarget.dataset.row), targetColumn = Number(event.currentTarget.dataset.col), panel = editablePanel();
  const item = panel.rows[dragging.row]?.[dragging.col]; if (!item) return;
  if (targetRow !== dragging.row && (panel.rows[targetRow]?.length || 0) >= 5) { setNotice("目标行已满（每行最多5个）", true); return; }
  panel.rows[dragging.row].splice(dragging.col, 1); while (panel.rows.length <= targetRow) panel.rows.push([]);
  const position = Math.min(targetColumn, panel.rows[targetRow].length); panel.rows[targetRow].splice(position, 0, item);
  compactRows(panel); selected = { row: targetRow, col: position }; dragging = null; render();
}
function matchingCatalogCommand(data) {
  return (state.command_catalog || []).find((item) => data === item.command || data.startsWith(`${item.command} `));
}
function renderForm() {
  const button = selectedButton(), form = $("form"); form.hidden = !button; if (!button) return;
  $("label").value = button.label; $("visited-label").value = button.visited_label; $("style").value = button.style;
  $("action-type").value = button.action_type; $("data").value = button.data; $("permission").value = button.permission;
  syncButtonEphemeralUi(button);
  $("users").value = (button.specified_users || []).join("\n"); $("users-wrap").hidden = button.permission !== "specified_users";
  $("reply").checked = Boolean(button.reply); $("enter").checked = Boolean(button.enter); $("anchor").checked = button.anchor === 1;
  $("unsupport-tips").value = button.unsupport_tips || "当前 QQ 版本不支持该按钮";
  $("action-wrap").hidden = button.action_type !== 1;
  $("action-params-wrap").hidden = button.action_type !== 1;
  $("action-params").value = JSON.stringify(button.action_params || {}, null, 2);
  $("command-wrap").hidden = button.action_type !== 2;
  $("data-wrap").hidden = button.action_type === 1;
  $("data-label").textContent = button.action_type === 0 ? "HTTPS 跳转地址" : "写入聊天框的指令/文字";
  const action = (state.action_catalog || []).find((item) => item.id === button.data);
  $("action-preset").value = action?.id || "";
  $("action-meta").textContent = action ? action.description : "当前 action_id 未注册，保存或点击时会被拒绝";
  const catalog = matchingCatalogCommand(button.data || "");
  $("command-preset").value = catalog?.command || "";
  $("command-meta").textContent = catalog ? `${catalog.permission === "admin" ? "仅管理员 · " : ""}${catalog.parameters || "无参数"}${catalog.description ? ` · ${catalog.description}` : ""}` : "";
}
function editSelected() {
  const button = selectedButton(); if (!button) return;
  button.label = $("label").value; button.visited_label = $("visited-label").value; button.style = Number($("style").value);
  const oldType = button.action_type, newType = Number($("action-type").value);
  button.action_type = newType;
  if (newType !== oldType) {
    if (newType === 1) button.data = state.action_catalog?.[0]?.id || "hub.test";
    else if (newType === 0) button.data = "https://example.com";
    else button.data = "/myrss list";
  } else if (newType !== 1) button.data = $("data").value;
  button.permission = $("permission").value;
  button.specified_users = $("users").value.split("\n").map((item) => item.trim()).filter(Boolean);
  button.reply = $("reply").checked; button.enter = $("enter").checked; button.anchor = $("anchor").checked ? 1 : 0;
  button.unsupport_tips = $("unsupport-tips").value;
  button.one_shot = $("btn-one-shot").checked;
  button.owner_mode = $("btn-owner-mode").value;
  button.owner_openid = button.owner_mode === OWNER_MODE_NEEDS_OPENID
    ? $("btn-owner-openid").value.trim() : "";
  syncButtonEphemeralUi(button);
  render();
}


const OWNER_MODE_NEEDS_OPENID = "specified";

function syncCardEphemeralUi() {
  const panel = editablePanel();
  const mode = panel.owner_mode || "everyone";
  $("card-one-shot").checked = Boolean(panel.one_shot);
  $("card-owner-mode").value = mode;
  $("card-owner-openid").value = panel.owner_openid || "";
  $("card-owner-tip").value = panel.owner_reject_tip || "";
  $("card-owner-openid-wrap").hidden = mode !== OWNER_MODE_NEEDS_OPENID;
  $("card-owner-tip-wrap").hidden = mode === "everyone";
  const warn = $("card-owner-warn");
  if (mode === OWNER_MODE_NEEDS_OPENID && !String(panel.owner_openid || "").trim()) {
    warn.textContent = "⚠️ 指定 OpenID 不能为空，否则没有人能点这张卡片。";
    warn.hidden = false;
  } else if (mode === "initiator") {
    warn.textContent = "ℹ️ 「仅发起者」需要由点击触发。主动推送、定时任务或后台测试发送时没有发起者，发送会被拒绝。";
    warn.hidden = false;
  } else {
    warn.hidden = true;
  }
}

function syncButtonEphemeralUi(button) {
  const mode = button.owner_mode || "everyone";
  $("btn-one-shot").checked = Boolean(button.one_shot);
  $("btn-owner-mode").value = mode;
  $("btn-owner-openid").value = button.owner_openid || "";
  $("btn-owner-openid-wrap").hidden = mode !== OWNER_MODE_NEEDS_OPENID;
}

function renderSnippetLibrary() {
  const host = $("snippet-groups");
  if (!host) return;
  host.innerHTML = "";
  const items = state.snippet_catalog || [];
  const groups = new Map();
  for (const item of items) {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  }
  for (const [groupName, entries] of groups) {
    const section = document.createElement("div");
    section.className = "snippet-group";
    const title = document.createElement("h4");
    title.textContent = groupName;
    section.append(title);
    const row = document.createElement("div");
    row.className = "snippet-row";
    for (const entry of entries) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary snippet-chip";
      button.textContent = entry.label;
      if (entry.dynamic) {
        const badge = document.createElement("span");
        badge.className = "dyn-badge";
        badge.textContent = "动态";
        button.append(badge);
      }
      button.title = entry.hint || "";
      button.onclick = () => insertMarkdownSnippet(entry.snippet);
      row.append(button);
    }
    section.append(row);
    host.append(section);
  }
}

function ensureRow(panel) { if (!panel.rows.length || panel.rows.at(-1).length >= 5) { if (panel.rows.length >= 5) throw Error("已经达到5行上限"); panel.rows.push([]); } return panel.rows.length - 1; }
async function load() {
  await bridge.ready(); state = await bridge.apiGet("bootstrap"); const groups = $("group"); groups.innerHTML = "";
  for (const item of Object.values(state.observed_groups)) { const option = document.createElement("option"); option.value = item.origin; option.textContent = item.origin; groups.append(option); }
  const actionPreset = $("action-preset");
  for (const item of state.action_catalog || []) {
    const option = document.createElement("option"); option.value = item.id;
    option.textContent = `${item.owner?.endsWith(".commands") ? "指令｜" : "动作｜"}${item.title}`;
    actionPreset.append(option);
  }
  const preset = $("command-preset");
  for (const item of state.command_catalog || []) {
    const option = document.createElement("option"); option.value = item.command;
    option.textContent = `${item.command}${item.parameters ? `  (${item.parameters})` : ""}${item.permission === "admin" ? "  🔒" : ""}`;
    preset.append(option);
  }
  renderSnippetLibrary();
  $("group-wrap").hidden = !groups.options.length; if (!groups.options.length) $("scope").value = "global"; render();
}
async function save() {
  try {
    const scope = $("scope").value, origin = $("group").value, panel = editablePanel();
    const result = await bridge.apiPost("panel", { scope, origin, panel });
    if (scope === "global") state.templates.default_panel = result.panel; else state.group_overrides[origin] = result.panel;
    setNotice("已原子保存；版本已更新，旧回调卡自动失效。"); render();
  } catch (error) { setNotice(error.message || "保存失败", true); }
}
async function sendTest(mode = "configured") {
  const origin = $("group").value;
  if (!origin) { setNotice("尚未观察到可测试的 QQ Official 群。", true); return; }
  const ephemeral = mode === "ephemeral";
  const button = $(ephemeral ? "send-test-ephemeral" : "send-test");
  const original = button.textContent;
  button.disabled = true; button.textContent = "发送中…";
  try {
    await bridge.apiPost("send-test", { origin, mode });
    setNotice(ephemeral
      ? "已按一次性卡片发送：仅 type=1 按钮会保留，可验证一次性与归属限制。"
      : "测试卡已发送，请在群内核对 Markdown、图片、链接、按钮和权限。");
  }
  catch (error) { setNotice(error.message || "测试发送失败", true); }
  finally { button.disabled = false; button.textContent = original; }
}
function insertMarkdownSnippet(snippet) {
  const textarea = $("markdown"), start = textarea.selectionStart, end = textarea.selectionEnd;
  textarea.value = textarea.value.slice(0, start) + snippet + textarea.value.slice(end);
  const panel = editablePanel(); panel.markdown = textarea.value; render();
  textarea.focus(); textarea.selectionStart = textarea.selectionEnd = start + snippet.length;
}
["name", "markdown"].forEach((id) => $(id).addEventListener("input", () => { const panel = editablePanel(); panel[id] = $(id).value; render(); }));
$("mention-clicker").addEventListener("input", () => { editablePanel().mention_clicker = $("mention-clicker").checked; render(); });
$("card-one-shot").addEventListener("input", () => { editablePanel().one_shot = $("card-one-shot").checked; syncCardEphemeralUi(); });
$("card-owner-mode").addEventListener("input", () => {
  const panel = editablePanel();
  panel.owner_mode = $("card-owner-mode").value;
  if (panel.owner_mode !== OWNER_MODE_NEEDS_OPENID) panel.owner_openid = "";
  syncCardEphemeralUi();
});
["card-owner-openid", "card-owner-tip"].forEach((id) => $(id).addEventListener("input", () => {
  const panel = editablePanel();
  panel.owner_openid = $("card-owner-openid").value.trim();
  panel.owner_reject_tip = $("card-owner-tip").value.trim();
  syncCardEphemeralUi();
}));
$("insert-link").onclick = () => insertMarkdownSnippet("[🔗链接文字](https://example.com)");
$("insert-image").onclick = () => {
  const url = safeUrl($("image-url").value.trim());
  const alt = $("image-alt").value.trim() || "图片";
  const maxWidth = Math.max(1, Math.min(Number($("image-max-width").value) || 720, 720));
  if (!url) { setNotice("请输入可公开访问的 HTTPS 图片 URL", true); return; }
  const probe = new Image();
  probe.onload = () => {
    if (!probe.naturalWidth || !probe.naturalHeight) { setNotice("无法读取图片真实尺寸", true); return; }
    const scale = Math.min(1, maxWidth / probe.naturalWidth, 1080 / probe.naturalHeight);
    const width = Math.max(1, Math.round(probe.naturalWidth * scale));
    const height = Math.max(1, Math.round(probe.naturalHeight * scale));
    insertMarkdownSnippet(`![${alt} #${width}px #${height}px](${url})`);
    setNotice(`已按真实尺寸 ${probe.naturalWidth}×${probe.naturalHeight} 等比例插入为 ${width}×${height}`);
  };
  probe.onerror = () => setNotice("浏览器无法加载该图片；请检查公网访问、HTTPS、防盗链或URL有效期", true);
  probe.referrerPolicy = "no-referrer";
  probe.src = url;
};
$("insert-cmd-input").onclick = () => {
  const text = $("cmd-text").value.trim(), show = $("cmd-show").value.trim() || text;
  if (!text) { setNotice("写入输入框的内容不能为空", true); return; }
  const reference = $("cmd-reference").checked ? "true" : "false";
  insertMarkdownSnippet(`<qqbot-cmd-input text="${encodeURIComponent(text)}" show="${encodeURIComponent(show)}" reference="${reference}" />`);
};
function openMarkdownEditor() {
  $("markdown-full").value = editablePanel().markdown;
  renderMarkdown($("markdown-full").value, "markdown-full-preview");
  $("markdown-modal").hidden = false;
  $("markdown-full").focus();
}
function closeMarkdownEditor() { $("markdown-modal").hidden = true; }
$("fullscreen-markdown").onclick = openMarkdownEditor;
$("markdown-full").addEventListener("input", () => renderMarkdown($("markdown-full").value, "markdown-full-preview"));
$("markdown-cancel").onclick = closeMarkdownEditor;
$("markdown-apply").onclick = () => {
  editablePanel().markdown = $("markdown-full").value;
  $("markdown").value = editablePanel().markdown;
  closeMarkdownEditor(); render(); setNotice("正文已应用到当前编辑状态；点击保存后才会持久化。");
};
$("markdown-modal").addEventListener("click", (event) => { if (event.target === $("markdown-modal")) closeMarkdownEditor(); });
window.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("markdown-modal").hidden) closeMarkdownEditor(); });
["label", "visited-label", "style", "action-type", "data", "permission", "users", "reply", "enter", "anchor", "unsupport-tips", "btn-one-shot", "btn-owner-mode", "btn-owner-openid"].forEach((id) => $(id).addEventListener("input", editSelected));
$("action-preset").addEventListener("change", () => {
  const button = selectedButton(); if (!button || !$("action-preset").value) return;
  button.data = $("action-preset").value;
  const spec = (state.action_catalog || []).find((item) => item.id === button.data);
  if (spec?.default_permission && button.permission === "everyone") button.permission = spec.default_permission;
  button.action_params = spec?.owner?.endsWith(".commands") ? { arguments: "" } : {};
  render();
});
$("action-params").addEventListener("change", () => {
  const button = selectedButton(); if (!button) return;
  try {
    const value = JSON.parse($("action-params").value || "{}");
    if (!value || Array.isArray(value) || typeof value !== "object") throw Error("必须是 JSON 对象");
    button.action_params = value; setNotice("Action 参数已更新，保存面板后生效。");
  } catch (error) { setNotice(`Action 参数格式错误：${error.message}`, true); }
});
$("command-preset").addEventListener("change", () => {
  const button = selectedButton(); if (!button || !$("command-preset").value) return;
  button.data = $("command-preset").value;
  const command = matchingCatalogCommand(button.data);
  if (command?.permission === "admin" && button.permission === "everyone") button.permission = "astrbot_admin";
  render();
});
$("scope").onchange = () => { selected = null; $("group-wrap").hidden = $("scope").value !== "group"; render(); };
$("group").onchange = () => { selected = null; render(); };
$("add").onclick = () => { try { const panel = editablePanel(), row = ensureRow(panel); panel.rows[row].push({ id: `button-${Date.now()}`, label: "新按钮", visited_label: "新按钮", style: 0, action_type: 2, data: "/myrss list", permission: "everyone", specified_users: [], action_params: {}, reply: false, enter: false, anchor: 0, unsupport_tips: "当前 QQ 版本不支持该按钮" }); selected = { row, col: panel.rows[row].length - 1 }; render(); } catch (error) { setNotice(error.message, true); } };
$("delete").onclick = () => { if (!selected) return; const panel = editablePanel(); panel.rows[selected.row].splice(selected.col, 1); compactRows(panel); selected = null; render(); };
$("save").onclick = save;
$("send-test").onclick = () => sendTest("configured");
$("send-test-ephemeral").onclick = () => sendTest("ephemeral");
load().catch((error) => setNotice(`加载失败：${error.message}`, true));


// --- 运行诊断（只读抽屉）---------------------------------------------------
// AstrBot 侧边栏只为每个插件显示第一个页面（usePluginSidebarItems 取 pages[0]），
// 所以诊断不能独立成页，否则没有入口。做成编辑器内的抽屉即可随时打开。
function diagEl(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function diagTable(headers, rows) {
  return diagEl("table", { className: "diag-table" },
    diagEl("thead", {}, diagEl("tr", {}, ...headers.map((h) => diagEl("th", {}, h)))),
    diagEl("tbody", {}, ...rows.map((cells) => diagEl("tr", {}, ...cells.map((c) => diagEl("td", {}, c))))));
}

function diagMark(ok, okText = "正常", badText = "缺失") {
  return diagEl("span", { className: ok ? "diag-ok" : "diag-bad" }, ok ? `✓ ${okText}` : `✗ ${badText}`);
}

function renderDiagnostics(report) {
  const host = $("diag-body");
  host.innerHTML = "";
  const storage = report.storage || {};
  const actions = report.actions || {};
  const external = (actions.owners || []).filter((o) => o.external);

  const stats = diagEl("div", { className: "diag-stats" });
  const pairs = [
    [report.healthy ? "✓" : "✗", report.healthy ? "整体健康" : "存在异常"],
    [`${(report.modules || []).filter((m) => m.ok).length}/${(report.modules || []).length}`, "模块加载"],
    [actions.total ?? 0, "已注册 Action"],
    [external.length, "外部插件接入"],
    [(report.providers || []).length, "卡片提供者"],
    [storage.ephemeral_cards_live ?? 0, "存活一次性卡片"],
    [storage.ephemeral_sessions_live ?? 0, "进行中会话"],
  ];
  for (const [value, label] of pairs) {
    stats.append(diagEl("div", { className: "diag-stat" },
      diagEl("b", {}, value), diagEl("span", {}, label)));
  }
  host.append(stats);

  host.append(diagEl("h3", {}, "已注册 Action"));
  host.append(diagEl("p", { className: "hint" },
    "外部插件出现在这里即代表与 Hub 握手成功；卸载后刷新即消失。"));
  const owners = actions.owners || [];
  if (actions.error) host.append(diagEl("p", { className: "diag-bad" }, actions.error));
  else if (!owners.length) host.append(diagEl("p", { className: "hint" }, "暂无注册"));
  else for (const group of owners) {
    host.append(diagEl("div", { className: "diag-owner" },
      diagEl("h4", {}, diagEl("code", {}, group.owner),
        diagEl("span", { className: `diag-tag ${group.external ? "ext" : "own"}` },
          group.external ? "外部插件" : "Hub 自带")),
      diagTable(["Action ID", "标题", "权限"],
        group.actions.map((a) => [diagEl("code", {}, a.id), a.title, a.permission || "-"]))));
  }

  host.append(diagEl("h3", {}, "卡片提供者（next_card）"));
  const providers = report.providers || [];
  host.append(providers.length
    ? diagTable(["card_id", "归属", "回调"],
        providers.map((p) => [diagEl("code", {}, p.card_id),
          p.external ? "外部" : "Hub", diagEl("code", {}, p.callback)]))
    : diagEl("p", { className: "hint" }, "暂无注册"));

  const bridge = report.bridge || {};
  host.append(diagEl("h3", {}, "Interaction 兼容桥"));
  host.append(bridge.error
    ? diagEl("p", { className: "diag-bad" }, bridge.error)
    : diagTable(["项目", "值"], [
        ["配置开关", diagMark(bridge.enabled, "已开启", "未开启")],
        ["已安装", diagMark(bridge.installed, "已安装", "未安装")],
        ["回调存活", diagMark(bridge.callback_alive, "存活", "已失效")],
        ["处理的类型", (bridge.handled_types || []).join(", ")],
      ]));

  host.append(diagEl("h3", {}, "模块与 API"));
  host.append(diagTable(["模块", "状态"],
    (report.modules || []).map((m) => [diagEl("code", {}, m.name),
      m.ok ? diagMark(true, "已加载") : diagEl("span", { className: "diag-bad" }, m.error)])));
  host.append(diagTable(["对外接口", "状态"],
    (report.api || []).map((a) => [diagEl("code", {}, a.name), diagMark(a.present)])));
}

async function loadDiagnostics() {
  const button = $("diag-refresh");
  button.disabled = true;
  try {
    renderDiagnostics(await bridge.apiGet("diagnostics"));
  } catch (error) {
    $("diag-body").textContent = error.message || "读取诊断信息失败";
  } finally {
    button.disabled = false;
  }
}

$("open-diagnostics").onclick = () => { $("diag-modal").hidden = false; loadDiagnostics(); };
$("diag-close").onclick = () => { $("diag-modal").hidden = true; };
$("diag-refresh").onclick = loadDiagnostics;
$("diag-modal").addEventListener("click", (event) => {
  if (event.target === $("diag-modal")) $("diag-modal").hidden = true;
});
