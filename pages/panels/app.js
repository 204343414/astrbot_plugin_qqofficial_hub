const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);
let state = null;
let selected = null;
let dragging = null;

function currentPanel() {
  const scope = $("scope").value;
  const origin = $("group").value;
  if (scope === "group" && origin && state.group_overrides[origin]) return state.group_overrides[origin];
  return state.templates.default_panel;
}
function clonePanel(panel) { return JSON.parse(JSON.stringify(panel)); }
function editablePanel() {
  const scope = $("scope").value, origin = $("group").value;
  if (scope === "group" && origin && !state.group_overrides[origin]) state.group_overrides[origin] = clonePanel(state.templates.default_panel);
  return currentPanel();
}
function setNotice(text, error=false) { const el=$("notice"); el.textContent=text; el.className=error?"error":"ok"; }
function selectedButton() { return selected ? editablePanel().rows[selected.row]?.[selected.col] : null; }
function render() {
  const panel = editablePanel();
  $("name").value=panel.name; $("markdown").value=panel.markdown;
  $("preview-title").textContent=panel.name; $("preview-markdown").textContent=panel.markdown;
  const canvas=$("canvas"); canvas.innerHTML="";
  for(let r=0;r<5;r++) { const row=document.createElement("div"); row.className="row"; row.dataset.row=r;
    for(let c=0;c<5;c++) { const cell=document.createElement("div"); cell.className="cell"; cell.dataset.row=r; cell.dataset.col=c;
      cell.addEventListener("dragover", e=>e.preventDefault()); cell.addEventListener("drop", drop);
      const btn=panel.rows[r]?.[c];
      if(btn) { const b=document.createElement("button"); b.className=`card-button style-${btn.style}`; b.textContent=btn.label; b.draggable=true;
        if(selected?.row===r&&selected?.col===c)b.classList.add("selected");
        b.onclick=()=>{selected={row:r,col:c};renderForm();render();}; b.ondragstart=()=>dragging={row:r,col:c}; cell.append(b); }
      row.append(cell);
    } canvas.append(row);
  }
  renderForm();
}
function compactRows(panel) { panel.rows=panel.rows.map(row=>row||[]); while(panel.rows.length&&panel.rows.at(-1).length===0)panel.rows.pop(); }
function drop(e) { e.preventDefault(); if(!dragging)return; const tr=Number(e.currentTarget.dataset.row), tc=Number(e.currentTarget.dataset.col), panel=editablePanel();
  const btn=panel.rows[dragging.row]?.[dragging.col]; if(!btn)return;
  panel.rows[dragging.row].splice(dragging.col,1); while(panel.rows.length<=tr)panel.rows.push([]);
  if(panel.rows[tr].length>=5){setNotice("目标行已满（最多 5 个按钮）",true);return;}
  panel.rows[tr].splice(Math.min(tc,panel.rows[tr].length),0,btn); compactRows(panel); selected={row:tr,col:Math.min(tc,panel.rows[tr].length-1)}; dragging=null; render();
}
function renderForm() { const btn=selectedButton(), form=$("form"); form.hidden=!btn; if(!btn)return;
  $("label").value=btn.label; $("visited-label").value=btn.visited_label; $("style").value=btn.style; $("action-type").value=btn.action_type; $("data").value=btn.data; $("permission").value=btn.permission; $("users").value=(btn.specified_users||[]).join("\n"); $("users-wrap").hidden=btn.permission!=="specified_users";
}
function editSelected() { const btn=selectedButton(); if(!btn)return; btn.label=$("label").value; btn.visited_label=$("visited-label").value; btn.style=Number($("style").value); btn.action_type=Number($("action-type").value); btn.data=$("data").value; btn.permission=$("permission").value; btn.specified_users=$("users").value.split("\n").map(x=>x.trim()).filter(Boolean); render(); }
function ensureRow(panel) { if(!panel.rows.length||panel.rows.at(-1).length>=5){if(panel.rows.length>=5)throw Error("已经达到 5 行上限");panel.rows.push([]);} return panel.rows.length-1; }
async function load() { await bridge.ready(); state=await bridge.apiGet("bootstrap"); const g=$("group"); g.innerHTML=""; for(const item of Object.values(state.observed_groups)){const op=document.createElement("option");op.value=item.origin;op.textContent=item.origin;g.append(op);} $("group-wrap").hidden=!g.options.length; if(!g.options.length)$("scope").value="global"; render(); }
async function save() { try { const scope=$("scope").value, origin=$("group").value, panel=editablePanel(); const result=await bridge.apiPost("panel",{scope,origin,panel}); if(scope==="global")state.templates.default_panel=result.panel;else state.group_overrides[origin]=result.panel; setNotice("已原子保存。卡片版本已更新；未来旧后台卡会自动失效。"); render(); } catch(e) {setNotice(e.message||"保存失败",true);} }
["name","markdown"].forEach(id=>$(id).addEventListener("input",()=>{const p=editablePanel();p[id]=$(id).value;render();}));
["label","visited-label","style","action-type","data","permission","users"].forEach(id=>$(id).addEventListener("input",editSelected));
$("scope").onchange=()=>{selected=null;$("group-wrap").hidden=$("scope").value!=="group";render();}; $("group").onchange=()=>{selected=null;render();};
$("add").onclick=()=>{try{const p=editablePanel(),r=ensureRow(p);p.rows[r].push({id:`button-${Date.now()}`,label:"新按钮",visited_label:"新按钮",style:0,action_type:1,data:"hub.test",permission:"everyone",specified_users:[]});selected={row:r,col:p.rows[r].length-1};render();}catch(e){setNotice(e.message,true);}};
$("delete").onclick=()=>{if(!selected)return;const p=editablePanel();p.rows[selected.row].splice(selected.col,1);compactRows(p);selected=null;render();}; $("save").onclick=save;
load().catch(e=>setNotice(`加载失败：${e.message}`,true));
