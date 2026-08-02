const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function mark(ok, okText = "正常", badText = "缺失") {
  return el("span", { className: ok ? "ok" : "bad" }, ok ? `✓ ${okText}` : `✗ ${badText}`);
}

function table(headers, rows) {
  const thead = el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", {}, h))));
  const tbody = el("tbody", {}, ...rows.map((cells) => el("tr", {}, ...cells.map((c) => el("td", {}, c)))));
  return el("table", {}, thead, tbody);
}

function stat(value, label) {
  return el("div", { className: "stat" }, el("b", {}, value), el("span", {}, label));
}

function renderSummary(report) {
  const host = $("summary");
  host.innerHTML = "";
  const storage = report.storage || {};
  const actions = report.actions || {};
  const external = (actions.owners || []).filter((o) => o.external);
  const modulesOk = (report.modules || []).filter((m) => m.ok).length;

  host.append(
    stat(report.healthy ? "✓" : "✗", report.healthy ? "整体健康" : "存在异常"),
    stat(`${modulesOk}/${(report.modules || []).length}`, "模块加载"),
    stat(actions.total ?? 0, "已注册 Action"),
    stat(external.length, "外部插件接入"),
    stat((report.providers || []).length, "卡片提供者"),
    stat(storage.ephemeral_cards_live ?? 0, "存活一次性卡片"),
    stat(storage.ephemeral_sessions_live ?? 0, "进行中会话"),
    stat(storage.observed_groups ?? 0, "已观察群"),
  );
}

function renderModules(report) {
  const rows = (report.modules || []).map((m) => [
    el("code", {}, m.name),
    mark(m.ok, "已加载", "导入失败"),
    m.ok ? `${m.symbols} 个公开符号` : el("span", { className: "bad" }, m.error),
  ]);
  return el("section", {},
    el("h2", {}, "模块"),
    el("p", { className: "hint" }, "逐个 import 实际校验，不是写死的清单；任一模块损坏都会在此暴露。"),
    table(["模块", "状态", "详情"], rows));
}

function renderApi(report) {
  const rows = (report.api || []).map((a) => [
    el("code", {}, a.name), mark(a.present), a.kind,
  ]);
  return el("section", {},
    el("h2", {}, "对外 API"),
    el("p", { className: "hint" }, "配套插件依赖的公开接口。缺失通常意味着 Hub 版本过旧。"),
    table(["接口", "状态", "类型"], rows));
}

function renderActions(report) {
  const data = report.actions || {};
  const section = el("section", {}, el("h2", {}, "已注册 Action"));
  section.append(el("p", { className: "hint" },
    "按所属插件分组。外部插件出现在这里，就证明它与 Hub 的握手成功——卸载后刷新即消失。"));
  if (data.error) return section.append(el("p", { className: "empty bad" }, data.error)), section;
  const owners = data.owners || [];
  if (!owners.length) return section.append(el("p", { className: "empty" }, "暂无注册")), section;

  for (const group of owners) {
    const block = el("div", { className: "owner-block" },
      el("h3", {},
        el("code", {}, group.owner),
        el("span", { className: `tag ${group.external ? "ext" : "own"}` },
          group.external ? "外部插件" : "Hub 自带"),
        el("span", { className: "muted" }, `${group.actions.length} 个`)),
      table(["Action ID", "标题", "默认权限", "说明"],
        group.actions.map((a) => [
          el("code", {}, a.id), a.title, a.permission || "-",
          el("span", { className: "muted" }, a.description || "-"),
        ])));
    section.append(block);
  }
  return section;
}

function renderProviders(report) {
  const rows = (report.providers || []).map((p) => [
    el("code", {}, p.card_id),
    el("span", { className: `tag ${p.external ? "ext" : "own"}` }, p.external ? "外部" : "Hub"),
    el("code", {}, p.callback),
    el("span", { className: "muted" }, p.module),
  ]);
  return el("section", {},
    el("h2", {}, "卡片提供者（next_card）"),
    el("p", { className: "hint" }, "按钮 next_card 指向的构建函数，由插件调用 register_card_provider 注册。"),
    rows.length ? table(["card_id", "归属", "回调", "模块"], rows)
                : el("p", { className: "empty" }, "暂无注册"));
}

function renderBridge(report) {
  const b = report.bridge || {};
  if (b.error) return el("section", {}, el("h2", {}, "Interaction 兼容桥"), el("p", { className: "empty bad" }, b.error));
  return el("section", {},
    el("h2", {}, "Interaction 兼容桥"),
    el("p", { className: "hint" }, "type=1 按钮回调的入口。未安装时按钮点了不会有反应。"),
    table(["项目", "值"], [
      ["配置开关", mark(b.enabled, "已开启", "未开启")],
      ["已安装", mark(b.installed, "已安装", "未安装")],
      ["持有者", el("code", {}, b.owner || "-")],
      ["回调存活", mark(b.callback_alive, "存活", "已失效")],
      ["代次", String(b.generation ?? 0)],
      ["需 ACK 的类型", (b.ack_types || []).join(", ")],
      ["处理的类型", (b.handled_types || []).join(", ")],
      ["去重缓存 / 处理中", `${b.seen_cache ?? 0} / ${b.inflight ?? 0}`],
    ]));
}

function renderStorage(report) {
  const s = report.storage || {};
  if (s.error) return el("section", {}, el("h2", {}, "存储"), el("p", { className: "empty bad" }, s.error));
  const labels = {
    observed_groups: "已观察群", group_overrides: "群覆盖配置",
    issued_panel_cards: "已签发面板卡", ephemeral_cards_total: "一次性卡片（含过期）",
    ephemeral_cards_live: "一次性卡片（存活）", ephemeral_sessions_live: "进行中会话",
  };
  return el("section", {},
    el("h2", {}, "存储"),
    table(["项目", "数量"],
      Object.entries(labels).map(([key, label]) => [label, String(s[key] ?? 0)])));
}

async function load() {
  const button = $("refresh");
  button.disabled = true;
  $("notice").textContent = "";
  try {
    await bridge.ready();
    const report = await bridge.apiGet("diagnostics");
    renderSummary(report);
    const host = $("report");
    host.innerHTML = "";
    host.append(
      renderActions(report), renderProviders(report),
      renderBridge(report), renderModules(report),
      renderApi(report), renderStorage(report),
    );
  } catch (error) {
    $("notice").textContent = error.message || "读取诊断信息失败";
  } finally {
    button.disabled = false;
  }
}

$("refresh").onclick = load;
load();
