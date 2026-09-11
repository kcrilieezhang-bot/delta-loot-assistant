"use strict";
const $ = id => document.getElementById(id);
const money = value => value === null || value === undefined ? "未定价" : new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 1}).format(value);
const state = {csrf: "", session: null, selected: null, definition: null, snapshot: null, drawing: false, busy: false, dirty: false, llm: {provider:"disabled"}};
let pollTimer, searchTimer;
function notice(message, error = false) { $("notice").textContent = message; $("notice").classList.toggle("error", error); }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {"X-Review-Token": state.csrf, ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "本地请求失败");
  return data;
}
function post(path, payload) { return api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}); }
function payload() { return {session_id: state.session.id, revision: state.session.revision}; }
function inScope(row) { return state.session.scope === "all" || row.scope === "unassigned" || row.scope === state.session.scope || (state.session.scope === "carried" && row.scope !== "loot"); }
const scopeNames = {all:"全部",backpack:"背包",safe_box:"安全箱",loot:"地上",carried:"身上 / 口袋 / 胸挂",unassigned:"区域待定"};
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function numberField(id) { const raw = $(id).value.trim(); return raw === "" ? null : Number(raw); }
function imageUrl(key = null) { return "/api/image?session=" + state.session.id + (key === null ? "" : "&key=" + encodeURIComponent(key)); }
function position(el, rect) {
  const [x,y,w,h] = rect;
  Object.assign(el.style, {left: `${x/state.session.width*100}%`, top: `${y/state.session.height*100}%`, width: `${w/state.session.width*100}%`, height: `${h/state.session.height*100}%`});
}
function previewRows(rows, sort = "total") {
  return rows.filter(row => !row.excluded).slice().sort((a, b) => {
    const av = a[sort], bv = b[sort];
    return Number(av == null) - Number(bv == null) ||
      (av != null && bv != null ? av - bv : 0) || (a.key || "").localeCompare(b.key || "");
  });
}
function previewSummary(rows) {
  const included = rows.filter(row => !row.excluded), priced = included.filter(row => row.total != null);
  return {total: priced.length ? priced.reduce((sum, row) => sum + row.total, 0) : null,
    priced: priced.length, unpriced: included.length - priced.length};
}
function priceCaveat(row) {
  if (row.manual_total != null || !row.definition?.needs_total) return "";
  return row.definition.category === "weapon" ? "枪体参考价 · 不含完整配件" : "目录参考价 · 耐久/次数未折算";
}
function render() {
  const s = state.session;
  if (!s) return;
  const ready = s.status === "ready";
  $("scope").disabled = !ready || state.busy; $("coverage").disabled = !ready || state.busy; $("add").disabled = !ready || state.busy;
  $("enhance").disabled = !ready || state.busy;
  $("image-empty").hidden = true; $("image-stage").hidden = false;
  if ($("full-image").dataset.session !== s.id) { $("full-image").src = imageUrl(); $("full-image").dataset.session = s.id; }
  $("scope").value = s.scope; $("coverage").checked = s.coverage_confirmed; $("accept-stale").checked = s.accept_stale;
  $("count").textContent = ready ? s.ranking.selected : "…"; $("pending").textContent = ready ? s.rows.filter(r=>!r.excluded && r.scope==="loot").length : "…";
  const preview = previewSummary(s.rows.filter(inScope));
  $("total").textContent = ready ? money(preview.total) : "—";
  $("total-note").textContent = `${preview.priced} 项有参考价 · ${preview.unpriced} 项未定价；${s.scope === "all" ? "含地上物品，不等于已携带收益" : "不含未录入物品"}`;
  $("trust").textContent = ready ? "+" + money(s.advice?.estimated_gain ?? 0) : "…";
  $("trust-note").textContent = "一换一参考方案，实际形状和空位需满足";
  $("ranking-subtitle").textContent = "按显示价格从低到高排列，发现认错再纠正。未定价放最后，不按零元计算。";
  const overlay = $("overlays"); overlay.replaceChildren();
  s.rows.forEach((row, index) => {
    const box = node("button", undefined, "box" + (row.total != null ? " good" : "") + (row.excluded ? " excluded" : "") + (row.key === state.selected ? " active" : ""));
    box.type = "button"; box.setAttribute("aria-label", `核对第 ${index+1} 项 ${row.name}，参考总价 ${money(row.total)}`); box.title = `${index+1}. ${row.name} · 总价 ${money(row.total)} · ${priceCaveat(row) || "候选参考价"}`;
    position(box, row.rect); box.append(node("span", `${index+1} · ${money(row.total)}`)); box.onclick = () => selectRow(row.key); overlay.append(box);
  });
  renderTable();
  renderAdvice();
  if ($("agent-output").dataset.revision && $("agent-output").dataset.revision !== `${s.id}:${s.revision}`) {
    $("agent-output").replaceChildren(node("p", "截图或纠错已更新，上次解释已失效，请重新检索。"));
    delete $("agent-output").dataset.revision;
  }
}
function renderTable() {
  if (!state.session) return;
  const filter = $("filter").value, sort = $("sort").value;
  let rows = state.session.rows.filter(inScope).filter(row => !row.excluded || filter === "excluded");
  if (filter === "pending") rows = rows.filter(row => !row.excluded && !row.eligible);
  if (filter === "ranked") rows = rows.filter(row => row.eligible);
  if (filter === "excluded") rows = rows.filter(row => row.excluded);
  else rows = previewRows(rows, sort);
  const body = $("rows"); body.replaceChildren();
  if (!rows.length) { const tr = node("tr"); const td = node("td", "当前列表没有物品。可切换范围，或在原图上框选补项。", "table-empty"); td.colSpan = 8; tr.append(td); body.append(tr); return; }
  for (const row of rows) {
    const tr = node("tr"); const name = node("td", `${state.session.rows.indexOf(row)+1}. ${row.name}`, "row-name");
    name.append(node("span", scopeNames[row.scope] || "区域待定", "pill dim"));
    if (row.locked) name.append(node("span", "保留", "pill dim"));
    if (row.excluded) name.append(node("span", "已排除", "pill dim"));
    if (row.total == null) name.append(node("span", "缺价", "pill dim"));
    tr.append(name);
    for (const value of [row.quantity, row.cells, money(row.unit_price), money(row.total), money(row.per_cell)]) tr.append(node("td", String(value)));
    const reason = node("td", row.price_source); reason.append(node("small", row.excluded ? "不计入排名" : priceCaveat(row))); tr.append(reason);
    const action = node("td"), edit = node("button", "纠错"); edit.type="button"; edit.onclick=()=>{selectRow(row.key); $("editor-form").scrollIntoView({behavior:"smooth",block:"start"});}; action.append(edit); tr.append(action); body.append(tr);
  }
}
function renderAdvice() {
  const target = $("advice-output"), s = state.session, advice = s?.advice;
  target.replaceChildren();
  if (!advice) { target.append(node("p", "此服务尚未加载建议模块。请使用新版服务；不要关闭尚未保存的旧会话。")); return; }
  const groups = [["地上高价值物资",advice.ground_keys],["保留的装备 / 锁定物品",advice.keep_keys],["背包低价值物资",advice.low_keys]];
  for (const [title,keys] of groups) {
    const card = node("div",undefined,"advice-card"); card.append(node("h3",title));
    for (const key of keys.slice(0,6)) {
      const row=s.rows.find(r=>r.key===key);if(!row)continue;
      const button=node("button",`${row.name} · 总价 ${money(row.total)} · ${money(row.per_cell)}/格`);
      button.type="button";button.onclick=()=>{selectRow(key);$("editor-form").scrollIntoView({behavior:"smooth",block:"start"});};card.append(button);
    }
    if (!keys.length)card.append(node("p","暂无满足条件的项目"));target.append(card);
  }
  const details=node("div",undefined,"advice-warnings");
  for(const pair of advice.swaps){
    const take=s.rows.find(r=>r.key===pair.take_key), drop=s.rows.find(r=>r.key===pair.replace_key);
    details.append(node("p",`拿入：地上 ${take.name}（${money(take.total)}） → 换出：背包 ${drop.name}（${money(drop.total)}） · 增益 +${money(pair.gain)}`,"swap-line"));
  }
  if(!advice.swaps.length)details.append(node("p","当前没有可直接比较的一换一增益组合。缺价、整枪配件不完整、锁定项不会作为换出建议。"));
  else details.prepend(node("h3",`参考替换方案 · 合计增益 +${money(advice.estimated_gain)}`));
  for(const warning of advice.warnings)details.append(node("small",warning));target.prepend(details);
}
$("agent-form").onsubmit=async event=>{
  event.preventDefault();if(!state.session||state.session.status!=="ready"){notice("请先上传图片并等待识别完成",true);return;}
  const source=payload();$("agent-send").disabled=true;$("agent-output").replaceChildren(node("p","正在检索本地依据并比较；若启用模型，还需等待生成解释…"));
  try{
    const result=await post("/api/assistant",{...source,question:$("agent-question").value,use_llm:$("agent-llm").checked,allow_cloud:$("allow-cloud").checked});
    if(state.session.id!==source.session_id||state.session.revision!==source.revision)throw new Error("物品已更新，请重新提问");
    const output=$("agent-output");output.replaceChildren();output.dataset.revision=`${result.session_id}:${result.revision}`;
    output.append(node("p",`${result.orchestrator === "langchain_lcel" ? "LangChain 工作流" : "Python 降级工作流"} · ${result.llm_used ? (result.mode === "deepseek_rag" ? "DeepSeek + RAG" : "本机 LLM + RAG") : "知识检索 + 规则工具（未调用 LLM）"}`));
    if(result.warning)output.append(node("p",result.warning,"hint"));
    if(result.explanation){output.append(node("p",result.explanation.summary));output.append(node("small","模型引用："+result.explanation.evidence_ids.join("、")));}
    if(result.explanation?.usage)output.append(node("small",`本次模型用量 ${result.explanation.usage.total_tokens ?? "未知"} tokens；以服务商实际计费为准。`));
    for(const doc of result.evidence){const block=node("div",undefined,"evidence");block.append(node("strong",doc.title),node("p",doc.text),node("small",`${doc.id} · ${doc.source} · 检索分数 ${doc.score}`));output.append(block);}
    if(!result.evidence.length)output.append(node("p","没有检索到相关依据，不能可靠回答；可换成具体物品名或估价问题。"));
    const trace=node("details");trace.append(node("summary","查看工具执行记录"));for(const step of result.trace)trace.append(node("p",`${step.tool}：${step.output}${step.ms != null ? " · "+step.ms+" ms" : ""}`));output.append(trace);
  }catch(error){$("agent-output").replaceChildren(node("p",error.message,"hint"));}finally{$("agent-send").disabled=false;}
};
function setDefinition(definition) {
  state.definition = definition; $("chosen").replaceChildren();
  if (!definition) { $("chosen").textContent = "尚未选择目录物品"; $("icon").hidden = true; $("icon-missing").hidden = false; return; }
  $("chosen").append(node("strong", definition.name), node("small", `快照单价 ${money(definition.unit_price)} · 目录 ${definition.cells} 格（请核对）`), node("small", definition.id));
  $("icon").hidden = false; $("icon-missing").hidden = true; $("icon").src = "/api/icon?id=" + encodeURIComponent(definition.id);
  $("icon").onerror = () => { $("icon").hidden = true; $("icon-missing").hidden = false; };
  $("special-hint").textContent = definition.needs_total ? "此项可能含配件、耐久或剩余次数。须填写核实过的当前整件/整堆总价，不能直接用裸枪或满耐久目录价。" : "留空时使用本地快照单价；缺价不能按 0 处理。";
  if (definition.needs_total) $("price-details").open = true;
}
async function showCandidates(ids, selectedKey) {
  const results = await Promise.all(ids.slice(0,3).map(id=>api("/api/item?id="+encodeURIComponent(id))));
  if (state.selected === selectedKey) renderSearch(results);
}
function renderSearch(items) {
  $("search-results").replaceChildren();
  for (const item of items) {
    const button = node("button"); button.type = "button";
    const label = node("span", item.name); label.append(node("small", `${money(item.unit_price)} / 单位 · ${item.cells} 格`)); button.append(label);
    button.onclick = () => { setDefinition(item); $("cells").value = item.cells; $("manual-unit").value = ""; $("manual-total").value = ""; $("confirmed").checked = false; state.dirty = true; $("search-results").replaceChildren(); };
    $("search-results").append(button);
  }
  if (!items.length) $("search-results").append(node("small", "没有匹配记录。试试更短的名称；未知项应保留待处理，不要随便选一个。"));
}
function selectRow(key, force = false) {
  if (!force && state.dirty && !window.confirm("当前编辑尚未保存，确定切换物品吗？")) return;
  const row = state.session?.rows.find(r=>r.key===key); if (!row) return;
  if(!$("item-dialog").open)$("item-dialog").showModal();
  state.selected = key; state.dirty = false; $("editor-empty").hidden = true; $("editor-form").hidden = false;
  $("selected-number").textContent = `第 ${state.session.rows.indexOf(row)+1} 项`;
  $("crop").src = imageUrl(row.key); $("quantity").value = row.quantity; $("cells").value = row.cells; $("row-scope").value = row.scope;
  $("manual-unit").value = row.manual_unit_price ?? ""; $("manual-total").value = row.manual_total ?? "";
  $("locked").checked = row.locked; $("excluded").checked = row.excluded; $("confirmed").checked = row.confirmed; $("contribute").checked = false;
  $("price-details").open = false; $("search").value = ""; $("search-results").replaceChildren(); setDefinition(row.definition);
  $("row-hint").textContent = (row.recalled ? "已恢复这张图的纠错记录。" : "") + (row.ocr ? `OCR 原文：${row.ocr}。` : "");
  render(); showCandidates(row.candidates, key).catch(error=>notice(error.message,true));
}
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer); const q = $("search").value.trim(), key = state.selected;
  if (!q) { $("search-results").replaceChildren(); return; }
  searchTimer = setTimeout(async()=>{try { const result = await api("/api/catalog?q="+encodeURIComponent(q)); if (key===state.selected && q===$("search").value.trim()) renderSearch(result.items); } catch(error) {notice(error.message,true);}},220);
});
$("editor-form").addEventListener("input", ()=>{state.dirty=true;});
async function rescanCrop() {
  const key=state.selected, sessionId=state.session.id; $("rescan").disabled=true;
  notice("正在本机按四个方向识别当前裁剪，结果仅作候选，不会自动改名或确认。");
  try{const result=await post("/api/recognize-crop",{...payload(),key});if(state.session.id===sessionId && state.selected===key){renderSearch(result.items);notice(result.items.length ? "候选与价格已列出。选中正确物品后点保存，即可加入参考排名；可稍后再确认。" : "暂未找到可靠候选。请在右侧搜索物品名，选择后保存；不需要手写数据库编号。");}}
  catch(error){notice(error.message,true);}finally{$("rescan").disabled=false;}
}
$("rescan").onclick=rescanCrop;
$("editor-form").addEventListener("submit", async event => {
  event.preventDefault(); if (state.busy) return;
  state.busy = true; $("save").disabled = true;
  try {
    const data = {...payload(), key:state.selected, definition_id:state.definition?.id || "", quantity:numberField("quantity"), cells:numberField("cells"), scope:$("row-scope").value, manual_unit_price:numberField("manual-unit"), manual_total:numberField("manual-total"), confirmed:true, excluded:$("excluded").checked, locked:$("locked").checked, contribute:$("contribute").checked};
    const result = await post("/api/correct",data); state.session=result.session; state.dirty=false; selectRow(state.selected,true);
    const row = state.session.rows.find(r=>r.key===state.selected); notice(row.excluded ? "已排除此项，不再计价。" : "已保存，价格、排序和替换建议已更新。");$("item-dialog").close();
  } catch(error) {notice(error.message,true);} finally {state.busy=false; $("save").disabled=false; render();}
});
async function updateSettings(changedScope=false) {
  if (!state.session || state.busy) return;
  state.busy=true;
  try { const data = {...payload(),scope:$("scope").value,coverage_confirmed:changedScope ? false : $("coverage").checked,accept_stale:$("accept-stale").checked}; state.session=(await post("/api/settings",data)).session; render(); }
  catch(error){notice(error.message,true);} finally {state.busy=false;render();}
}
$("scope").onchange = ()=>updateSettings(true); $("coverage").onchange = ()=>updateSettings(); $("accept-stale").onchange = ()=>updateSettings();
$("sort").onchange = renderTable; $("filter").onchange = renderTable;
async function poll() {
  try {
    const result = await api("/api/session"); state.session=result.session; render();
    if (state.session?.status==="processing") {pollTimer=setTimeout(poll,900); return;}
    $("file").disabled=false;
    if (state.session) { notice(state.session.error || `已展示 ${state.session.rows.length} 项物资。默认采用识别结果；发现错误点“纠错”，漏项用“框选补一项”。`,!!state.session.error); }
  } catch(error){$("file").disabled=false;notice(error.message,true);}
}
$("file").onchange = async()=>{
  const file=$("file").files[0]; if (!file) return;
  if (file.size>15*1024*1024) {notice("请选择不超过 15 MB 的图片",true);$("file").value="";return;}
  if (state.session && !window.confirm("新截图会替换当前临时预览；已保存的纠错记录保留。继续吗？")) {$("file").value="";return;}
  try {clearTimeout(pollTimer);$("file").disabled=true;notice("正在本机识别图片，不会调用付费接口。首次加载模型可能需要更久，请稍候…");
    await api("/api/upload?profile="+$("profile").value,{method:"POST",headers:{"Content-Type":file.type||"application/octet-stream"},body:file});
    state.selected=null;state.dirty=false;$("editor-form").hidden=true;$("editor-empty").hidden=false; await poll();
  } catch(error){notice(error.message,true);$("file").disabled=false;} finally {$("file").value="";}
};
$("enhance").onclick=async()=>{
  if(state.dirty){notice("请先保存当前纠错，再增强补扫，避免未保存内容丢失。",true);return;}
  try{$("enhance").disabled=true;await post("/api/rescan",payload());notice("正在分块放大补扫；已保存纠错和锁定项保留。通常需要数十秒。");clearTimeout(pollTimer);await poll();}
  catch(error){notice(error.message,true);$("enhance").disabled=false;}
};
$("add").onclick=()=>{if(state.dirty && !window.confirm("当前纠错未保存，确定先补另一件物品吗？"))return;state.drawing=!state.drawing;$("image-stage").classList.toggle("drawing",state.drawing);$("add").textContent=state.drawing?"取消框选":"框选补一项";if(state.drawing)$("image-stage").scrollIntoView({behavior:"smooth",block:"center"});notice(state.drawing?"在原图上拖动圈出一件物品（含名称），松开后自动尝试重识别。若替代错误框，请排除原项，避免重复计价。":"已退出框选模式。已有修改不变。");};
let startPoint=null;
function point(event){const r=$("image-stage").getBoundingClientRect();return [Math.max(0,Math.min(state.session.width,Math.round((event.clientX-r.left)/r.width*state.session.width))),Math.max(0,Math.min(state.session.height,Math.round((event.clientY-r.top)/r.height*state.session.height)))];}
function selectionRect(end){return [Math.min(startPoint[0],end[0]),Math.min(startPoint[1],end[1]),Math.abs(startPoint[0]-end[0]),Math.abs(startPoint[1]-end[1])];}
$("image-stage").onpointerdown=event=>{if(!state.drawing)return;event.preventDefault();startPoint=point(event);$("image-stage").setPointerCapture(event.pointerId);$("selection").hidden=false;position($("selection"),[...startPoint,1,1]);};
$("image-stage").onpointermove=event=>{if(startPoint)position($("selection"),selectionRect(point(event)));};
$("image-stage").onpointerup=async event=>{
  if(!startPoint)return;const rect=selectionRect(point(event));startPoint=null;$("selection").hidden=true;
  state.drawing=false;$("image-stage").classList.remove("drawing");$("add").textContent="框选补一项";
  if(rect[2]<5||rect[3]<5){notice("框选区域太小，请重新拖动",true);return;}
  try{const result=await post("/api/add",{...payload(),rect});state.session=result.session;selectRow(result.key,true);$("editor-form").scrollIntoView({behavior:"smooth",block:"start"});await rescanCrop();}catch(error){notice(error.message,true);}
};
$("image-stage").onpointercancel=()=>{startPoint=null;$("selection").hidden=true;};
window.addEventListener("beforeunload",event=>{if(state.dirty){event.preventDefault();event.returnValue="";}});
async function init(){
  try{const status=await api("/api/status");state.csrf=status.csrf;state.snapshot=status.snapshot;
    $("price-date").textContent=status.snapshot.generated_at?new Date(status.snapshot.generated_at).toLocaleString("zh-CN",{hour12:false}):"尚无价格快照";
    $("price-meta").textContent=`${status.snapshot.catalog_count} 条目录 · ${status.snapshot.price_count} 条价格 · ${status.snapshot.season}`;
    $("stale-label").hidden=!status.snapshot.stale;
    await loadLlmSettings();
    await poll();
  }catch(error){notice("本机服务未就绪："+error.message,true);}
}
function closeItem(event){
  if(state.dirty && !window.confirm("尚有未保存修改，确定关闭吗？")){event?.preventDefault();return;}
  state.dirty=false;$("item-dialog").close();
}
$("close-item").onclick=closeItem;$("item-dialog").addEventListener("cancel",closeItem);
function updateModelFields(){
  const provider=$("llm-provider").value;
  $("deepseek-model-field").hidden=provider!=="deepseek";
  $("deepseek-key-field").hidden=provider!=="deepseek";
  $("local-model-field").hidden=provider!=="ollama";
}
function updateCloudConsent(){ $("cloud-consent").hidden=state.llm.provider!=="deepseek" || !$("agent-llm").checked; }
async function loadLlmSettings(){
  state.llm=await api("/api/llm-settings");
  $("active-model").textContent=state.llm.provider==="disabled" ? "未启用模型 · 可在右上角设置" : `${state.llm.provider} / ${state.llm.model}`;
  $("key-status").textContent=state.llm.key_configured ? "DeepSeek 密钥已在本机加密保存，不会回显。" : "尚未保存 DeepSeek 密钥。";
  updateCloudConsent();
}
$("agent-llm").onchange=updateCloudConsent;
$("open-llm").onclick=async()=>{
  try{await loadLlmSettings();$("llm-provider").value=state.llm.provider;
    $("llm-deepseek-model").value=state.llm.provider==="deepseek" ? state.llm.model : "deepseek-v4-flash";
    $("llm-local-model").value=state.llm.provider==="ollama" ? state.llm.model : "";
    $("llm-key").value="";$("llm-message").textContent="";updateModelFields();$("llm-dialog").showModal();
  }catch(error){notice(error.message,true);}
};
$("llm-provider").onchange=updateModelFields;
$("close-llm").onclick=()=>{$("llm-key").value="";$("llm-dialog").close();};
$("llm-dialog").addEventListener("close",()=>{$("llm-key").value="";});
$("llm-form").onsubmit=async event=>{
  event.preventDefault();$("save-llm").disabled=true;
  try{const provider=$("llm-provider").value, model=provider==="ollama" ? $("llm-local-model").value.trim() : $("llm-deepseek-model").value;
    await post("/api/llm-settings",{provider,model,api_key:$("llm-key").value.trim()});
    $("llm-key").value="";await loadLlmSettings();$("llm-message").textContent="已保存。尚未调用模型；勾选使用大模型后，点击分析才会请求。";
  }catch(error){$("llm-message").textContent=error.message;}finally{$("save-llm").disabled=false;}
};
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();
  try{Promise.resolve(document.modelContext.registerTool({name:"read_local_review_ranking",title:"读取当前核对排名",description:"只读当前截图的核对状态与参考排名，不自动确认物品或操作游戏。",inputSchema:{type:"object",properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute(input){if(!input||typeof input!=="object"||Object.keys(input).length)throw new Error("不接受参数");return state.session?{status:state.session.status,scope:state.session.scope,ranking:state.session.ranking,items:state.session.rows.filter(inScope).map(r=>({name:r.name,total:r.total,per_cell:r.per_cell,eligible:r.eligible,reasons:r.reasons}))}:{status:"no_image"};}},{signal:lifecycle.signal})).catch(()=>{});}catch{}
  window.addEventListener("pagehide",()=>lifecycle.abort(),{once:true});
}
init();
