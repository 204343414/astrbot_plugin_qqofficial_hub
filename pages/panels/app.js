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

// Blueprint canvas: persisted independently from card payloads. Node-to-card
// execution is the next step; this editor first establishes visual structure.
let bpSelected=null, bpConnecting=null, bpPan=null;
const bpCanvas=$("blueprint-canvas"), bpNodes=$("blueprint-nodes"), bpEdges=$("blueprint-edges"), bpMenu=$("blueprint-menu");
function bp(){return state.blueprint||(state.blueprint={version:1,viewport:{x:80,y:80,scale:1},nodes:[],edges:[]});}
function bpPos(node){const v=bp().viewport;return {x:v.x+node.x*v.scale,y:v.y+node.y*v.scale};}
function bpRender(){if(!state)return;const graph=bp(), byId=Object.fromEntries(graph.nodes.map(n=>[n.id,n]));bpNodes.innerHTML="";bpEdges.innerHTML='<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="currentColor"/></marker></defs>';
 for(const e of graph.edges){const a=byId[e.from],b=byId[e.to];if(!a||!b)continue;const pa=bpPos(a),pb=bpPos(b);const line=document.createElementNS('http://www.w3.org/2000/svg','line');line.setAttribute('x1',pa.x+150);line.setAttribute('y1',pa.y+28);line.setAttribute('x2',pb.x);line.setAttribute('y2',pb.y+28);line.setAttribute('class','blue-edge');bpEdges.append(line);}
 for(const node of graph.nodes){const p=bpPos(node),el=document.createElement('div');el.className='blue-node'+(bpSelected===node.id?' selected':'');el.dataset.type=node.type;el.style.left=p.x+'px';el.style.top=p.y+'px';el.innerHTML=`<strong>${node.title}</strong><small>${node.type}</small>`;el.onpointerdown=e=>{e.stopPropagation();bpPan={node,px:e.clientX,py:e.clientY,x:node.x,y:node.y};el.setPointerCapture(e.pointerId)};el.onclick=e=>{e.stopPropagation();if(e.shiftKey&&bpConnecting&&bpConnecting!==node.id){graph.edges.push({from:bpConnecting,to:node.id});bpConnecting=null}else if(e.shiftKey){bpConnecting=node.id}else bpSelected=node.id;bpInspector();bpRender()};el.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();bpSelected=node.id;bpInspector();bpMenuAt(e.clientX,e.clientY,true)};bpNodes.append(el);}}
function bpInspector(){const node=bp().nodes.find(n=>n.id===bpSelected), out=$("blueprint-inspector");if(!node){out.textContent='点击一个节点查看属性。';return}out.innerHTML=`<b>${node.type} 节点</b>　<label>名称 <input id="bp-title" value="${node.title.replaceAll('"','&quot;')}"></label><small>Shift 点击另一个节点可创建连线。右键该节点可删除。</small>`;$("bp-title").oninput=e=>{node.title=e.target.value;bpRender()};}
function bpMenuAt(x,y,nodeMenu=false){bpMenu.hidden=false;const r=bpCanvas.getBoundingClientRect();bpMenu.style.left=(x-r.left)+'px';bpMenu.style.top=(y-r.top)+'px';bpMenu.innerHTML='';const add=(type,label)=>{const b=document.createElement('button');b.textContent=label;b.onclick=()=>{if(nodeMenu){const i=bp().nodes.findIndex(n=>n.id===bpSelected);bp().nodes.splice(i,1);bp().edges=bp().edges.filter(e=>e.from!==bpSelected&&e.to!==bpSelected);bpSelected=null}else{const v=bp().viewport;const id='node-'+Date.now();bp().nodes.push({id,type,title:label,x:(x-r.left-v.x)/v.scale,y:(y-r.top-v.y)/v.scale,panel_id:type==='panel'?'default_panel':''})}bpMenu.hidden=true;bpInspector();bpRender()};bpMenu.append(b)};if(nodeMenu)add('delete','删除当前节点');else{add('panel','新增卡片菜单');add('command','新增指令节点');add('url','新增 URL 节点');add('action','新增 Hub 动作');add('confirm','新增确认节点');}}
bpCanvas.oncontextmenu=e=>{e.preventDefault();bpMenuAt(e.clientX,e.clientY,false)};bpCanvas.onpointerdown=e=>{if(e.target!==bpCanvas&&e.target!==bpEdges)return;bpPan={px:e.clientX,py:e.clientY,x:bp().viewport.x,y:bp().viewport.y};bpMenu.hidden=true};window.onpointermove=e=>{if(!bpPan)return;if(bpPan.node){bpPan.node.x=bpPan.x+(e.clientX-bpPan.px)/bp().viewport.scale;bpPan.node.y=bpPan.y+(e.clientY-bpPan.py)/bp().viewport.scale}else{bp().viewport.x=bpPan.x+e.clientX-bpPan.px;bp().viewport.y=bpPan.y+e.clientY-bpPan.py}bpRender()};window.onpointerup=()=>bpPan=null;bpCanvas.onwheel=e=>{e.preventDefault();const v=bp().viewport;v.scale=Math.max(.25,Math.min(2.5,v.scale*(e.deltaY<0?1.1:.9)));bpRender()};
$("blueprint-save").onclick=async()=>{try{const r=await bridge.apiPost("blueprint",{blueprint:bp()});state.blueprint=r.blueprint;setNotice('蓝图已保存。卡片执行连线将在下一阶段接入。');bpRender()}catch(e){setNotice(e.message||'蓝图保存失败',true)}};$("blueprint-fullscreen").onclick=()=>bpCanvas.requestFullscreen?.();window.addEventListener('keydown',e=>{if(e.key==='F11'&&document.activeElement?.tagName!=='INPUT'){e.preventDefault();bpCanvas.requestFullscreen?.()}});
const oldLoad=load;load=async function(){await oldLoad();bpRender();};
