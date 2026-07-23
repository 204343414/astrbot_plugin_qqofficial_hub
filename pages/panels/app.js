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

function renderInline(target, text) {
  const pattern = /!\[([^\]]+?)\s+#(\d+)px\s+#(\d+)px\]\((https:\/\/[^\s)]+)\)|\[([^\]]+)\]\((https:\/\/[^\s)]+)\)|<(https:\/\/[^>]+)>|\*\*([^*]+)\*\*|~~([^~]+)~~/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    target.append(document.createTextNode(text.slice(cursor, match.index)));
    if (match[1]) {
      const image = document.createElement("img");
      image.alt = match[1]; image.src = safeUrl(match[4]);
      image.style.width = `${Math.min(Number(match[2]), 720)}px`;
      image.style.maxHeight = `${Math.min(Number(match[3]), 1080)}px`;
      image.referrerPolicy = "no-referrer"; target.append(image);
    } else if (match[5]) {
      const link = document.createElement("a"); link.textContent = match[5]; link.href = safeUrl(match[6]); link.target = "_blank"; link.rel = "noopener noreferrer"; target.append(link);
    } else if (match[7]) {
      const link = document.createElement("a"); link.textContent = match[7]; link.href = safeUrl(match[7]); link.target = "_blank"; link.rel = "noopener noreferrer"; target.append(link);
    } else if (match[8]) {
      const strong = document.createElement("strong"); strong.textContent = match[8]; target.append(strong);
    } else if (match[9]) {
      const del = document.createElement("del"); del.textContent = match[9]; target.append(del);
    }
    cursor = match.index + match[0].length;
  }
  target.append(document.createTextNode(text.slice(cursor)));
}
function renderMarkdown(text) {
  const root = $("preview-markdown"); root.innerHTML = "";
  for (const raw of String(text || "").split("\n")) {
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
  $("preview-title").textContent = panel.name; renderMarkdown(panel.markdown);
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
function renderForm() {
  const button = selectedButton(), form = $("form"); form.hidden = !button; if (!button) return;
  $("label").value = button.label; $("visited-label").value = button.visited_label; $("style").value = button.style;
  $("action-type").value = button.action_type; $("data").value = button.data; $("permission").value = button.permission;
  $("users").value = (button.specified_users || []).join("\n"); $("users-wrap").hidden = button.permission !== "specified_users";
  $("reply").checked = Boolean(button.reply); $("enter").checked = Boolean(button.enter); $("anchor").checked = button.anchor === 1;
  $("unsupport-tips").value = button.unsupport_tips || "当前 QQ 版本不支持该按钮";
}
function editSelected() {
  const button = selectedButton(); if (!button) return;
  button.label = $("label").value; button.visited_label = $("visited-label").value; button.style = Number($("style").value);
  button.action_type = Number($("action-type").value); button.data = $("data").value; button.permission = $("permission").value;
  button.specified_users = $("users").value.split("\n").map((item) => item.trim()).filter(Boolean);
  button.reply = $("reply").checked; button.enter = $("enter").checked; button.anchor = $("anchor").checked ? 1 : 0;
  button.unsupport_tips = $("unsupport-tips").value; render();
}
function ensureRow(panel) { if (!panel.rows.length || panel.rows.at(-1).length >= 5) { if (panel.rows.length >= 5) throw Error("已经达到5行上限"); panel.rows.push([]); } return panel.rows.length - 1; }
async function load() {
  await bridge.ready(); state = await bridge.apiGet("bootstrap"); const groups = $("group"); groups.innerHTML = "";
  for (const item of Object.values(state.observed_groups)) { const option = document.createElement("option"); option.value = item.origin; option.textContent = item.origin; groups.append(option); }
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
async function sendTest() {
  const origin = $("group").value;
  if (!origin) { setNotice("尚未观察到可测试的 QQ Official 群。", true); return; }
  const button = $("send-test"); button.disabled = true; button.textContent = "发送中…";
  try { await bridge.apiPost("send-test", { origin }); setNotice("测试卡已发送，请在群内核对 Markdown、图片、链接、按钮和权限。"); }
  catch (error) { setNotice(error.message || "测试发送失败", true); }
  finally { button.disabled = false; button.textContent = "发送到群测试"; }
}
["name", "markdown"].forEach((id) => $(id).addEventListener("input", () => { const panel = editablePanel(); panel[id] = $(id).value; render(); }));
["label", "visited-label", "style", "action-type", "data", "permission", "users", "reply", "enter", "anchor", "unsupport-tips"].forEach((id) => $(id).addEventListener("input", editSelected));
$("scope").onchange = () => { selected = null; $("group-wrap").hidden = $("scope").value !== "group"; render(); };
$("group").onchange = () => { selected = null; render(); };
$("add").onclick = () => { try { const panel = editablePanel(), row = ensureRow(panel); panel.rows[row].push({ id: `button-${Date.now()}`, label: "新按钮", visited_label: "新按钮", style: 0, action_type: 2, data: "/myrss list", permission: "everyone", specified_users: [], reply: false, enter: false, anchor: 0, unsupport_tips: "当前 QQ 版本不支持该按钮" }); selected = { row, col: panel.rows[row].length - 1 }; render(); } catch (error) { setNotice(error.message, true); } };
$("delete").onclick = () => { if (!selected) return; const panel = editablePanel(); panel.rows[selected.row].splice(selected.col, 1); compactRows(panel); selected = null; render(); };
$("save").onclick = save; $("send-test").onclick = sendTest;
load().catch((error) => setNotice(`加载失败：${error.message}`, true));
