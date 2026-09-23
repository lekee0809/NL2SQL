const $ = (id) => document.getElementById(id);
const form = $("queryForm"), question = $("question"), submitBtn = $("submitBtn");
const screens = ["welcome", "loading", "result", "error"];
let sessionId = sessionStorage.getItem("nl2sqlSessionId");
let pendingClarification = false;
let busy = false;
let turnCount = 0;
let currentClarification = null;

function show(id){ screens.forEach(x => $(x).classList.toggle("hidden", x !== id)); }
function escapeHtml(v){ return String(v ?? "").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function historyItems(){ try{return JSON.parse(localStorage.getItem("queryHistory") || "[]");}catch{return [];} }
function renderHistory(){ const box=$("history"); box.innerHTML=""; historyItems().forEach(text=>{const b=document.createElement("button");b.textContent=text;b.onclick=()=>{if(busy)return;if(sessionId)clearSession();question.value=text;question.focus()};box.appendChild(b)}); }
function saveHistory(text){ const next=[text,...historyItems().filter(x=>x!==text)].slice(0,8); localStorage.setItem("queryHistory",JSON.stringify(next));renderHistory(); }

async function checkHealth(){
  try{const r=await fetch("/health");const d=await r.json();const ok=d.api==="ok"&&d.database?.ok;$("statusDot").className=`dot ${ok?'ok':'bad'}`;$("statusText").textContent=ok?`analytics 已连接`:"数据库未连接";}
  catch{$("statusDot").className="dot bad";$("statusText").textContent="服务不可用";}
}

function renderTable(rows){
  if(!rows.length){$("tableHead").innerHTML="";$("tableBody").innerHTML='<tr><td class="empty">查询成功，但没有符合条件的数据</td></tr>';return;}
  const cols=Object.keys(rows[0]);
  $("tableHead").innerHTML=`<tr>${cols.map(c=>`<th>${escapeHtml(c)}</th>`).join("")}</tr>`;
  $("tableBody").innerHTML=rows.map(r=>`<tr>${cols.map(c=>`<td>${escapeHtml(r[c])}</td>`).join("")}</tr>`).join("");
}

let chartData = null;
const modeLabels = {kpi:"指标卡",line:"趋势折线",bar:"分类比较",share:"占比"};
const timeDimensions = new Set(["order_date","order_month","order_quarter","order_year"]);
const palette = ["#176b5b","#4caa8a","#90c9b4","#e5a84b","#8c9fae","#bd8f78","#787aa5","#c6baa2"];
function numberValue(value){if(value===null||value==="")return null;const n=Number(value);return Number.isFinite(n)?n:null;}
function numberText(value){return value===null?"—":Number(value).toLocaleString("zh-CN",{maximumFractionDigits:2});}
function labelText(value){return value===null||value===""?"未填写":String(value);}

function prepareChart(result){
  const rows=result.rows||[];
  if(!rows.length)return null;
  const columns=Object.keys(rows[0]);
  const spec=result.query_spec||{};
  const dimensionCount=Math.min((spec.dimensions||[]).length,columns.length);
  const dimensionKeys=columns.slice(0,dimensionCount);
  const metricKeys=columns.slice(dimensionCount).filter(key=>
    rows.some(row=>numberValue(row[key])!==null)||Boolean((spec.metrics||[]).length)&&rows.every(row=>row[key]===null)
  );
  if(!metricKeys.length)return null;
  const isTime=dimensionCount===1&&timeDimensions.has(spec.dimensions[0]);
  const isComparison=Boolean(spec.comparison)&&rows.length===1;
  const canShare=dimensionCount===1&&!isTime&&rows.length>1&&rows.length<=8;
  const modes=dimensionCount===0&&rows.length===1?["kpi"]:isTime?["line","bar"]:["bar"];
  if(canShare)modes.push("share");
  return {rows,dimensionKeys,metricKeys,modes,defaultMode:isComparison?"kpi":modes[0],isTime};
}

function chartPoints(data,metric){
  const points=data.rows.map(row=>({
    label:data.dimensionKeys.map(key=>labelText(row[key])).join(" · ")||metric,
    value:numberValue(row[metric]),
  }));
  if(data.isTime)points.sort((a,b)=>a.label.localeCompare(b.label,"zh-CN",{numeric:true}));
  return points;
}

function renderKpi(data){
  const row=data.rows[0];
  return `<div class="kpi-grid">${data.metricKeys.map(key=>{
    const value=numberValue(row[key]),isRate=key.includes("增长率");
    return `<div class="kpi-card"><span>${escapeHtml(key)}</span><strong>${numberText(value)}${isRate&&value!==null?"%":""}</strong></div>`;
  }).join("")}</div>`;
}

function renderBars(points){
  const values=points.slice(0,12),finite=values.filter(point=>point.value!==null);
  if(!finite.length)return '<div class="empty">所选指标没有数值</div>';
  const min=Math.min(0,...finite.map(point=>point.value)),max=Math.max(0,...finite.map(point=>point.value));
  const span=max-min||1,zero=(-min/span)*100;
  return `<div class="viz-bars">${values.map(point=>{
    const start=point.value===null?zero:((Math.min(0,point.value)-min)/span)*100;
    const width=point.value===null?0:Math.abs(point.value)/span*100;
    return `<div class="viz-bar-row"><span class="viz-label" title="${escapeHtml(point.label)}">${escapeHtml(point.label)}</span><div class="viz-track"><i class="viz-zero" style="left:${zero.toFixed(2)}%"></i><i class="viz-fill ${point.value<0?"negative":""}" style="left:${start.toFixed(2)}%;width:${width.toFixed(2)}%"></i></div><strong>${numberText(point.value)}</strong></div>`;
  }).join("")}</div>${points.length>12?`<p class="viz-hint">图表仅显示前 12 行；完整结果请查看数据表格。</p>`:""}`;
}

function renderLine(points){
  const values=points.filter(point=>point.value!==null);
  if(!values.length)return '<div class="empty">所选指标没有数值</div>';
  const min=Math.min(...values.map(point=>point.value)),max=Math.max(...values.map(point=>point.value));
  const spread=max-min||1,step=points.length>1?100/(points.length-1):0;
  const segments=[];let segment=[];
  points.forEach((point,index)=>{
    if(point.value===null){if(segment.length)segments.push(segment);segment=[];return;}
    segment.push({x:index*step,y:90-(point.value-min)/spread*75,label:point.label,value:point.value});
  });
  if(segment.length)segments.push(segment);
  const lines=segments.map(part=>`<polyline points="${part.map(p=>`${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ")}"/>`).join("");
  const dots=segments.flat().map(p=>`<circle cx="${p.x.toFixed(2)}" cy="${p.y.toFixed(2)}" r="1.4"><title>${escapeHtml(p.label)}：${numberText(p.value)}</title></circle>`).join("");
  const labels=points.length<=8?points:points.filter((_,index)=>index===0||index===points.length-1||index%Math.ceil(points.length/6)===0);
  return `<div class="viz-line-wrap"><div class="viz-scale"><span>${numberText(max)}</span><span>${numberText(min)}</span></div><svg class="viz-line" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="趋势折线图">${lines}${dots}</svg></div><div class="viz-axis">${labels.map(p=>`<span title="${escapeHtml(p.label)}">${escapeHtml(p.label)}</span>`).join("")}</div>${points.some(p=>p.value===null)?'<p class="viz-hint">缺失值已断开显示，未按零处理。</p>':""}`;
}

function renderShare(points){
  const values=points.filter(point=>point.value!==null);
  if(values.some(point=>point.value<0))return '<div class="empty">包含负数，不适合计算占比。请切换到分类比较。</div>';
  const total=values.reduce((sum,point)=>sum+point.value,0);
  if(total<=0)return '<div class="empty">总和为零，无法计算占比。请切换到分类比较。</div>';
  let offset=0;
  const stops=values.map((point,index)=>{const end=offset+point.value/total*100;const stop=`${palette[index%palette.length]} ${offset.toFixed(2)}% ${end.toFixed(2)}%`;offset=end;return stop;});
  return `<div class="viz-share"><div class="viz-donut" style="background:conic-gradient(${stops.join(",")})"><div><strong>${numberText(total)}</strong><span>合计</span></div></div><div class="viz-legend">${values.map((point,index)=>`<div><i style="background:${palette[index%palette.length]}"></i><span title="${escapeHtml(point.label)}">${escapeHtml(point.label)}</span><strong>${(point.value/total*100).toFixed(1)}%</strong><small>${numberText(point.value)}</small></div>`).join("")}</div></div>`;
}

function renderSelectedChart(){
  if(!chartData)return;
  const metric=$("metricSelect").value,mode=$("chartMode").value;
  const points=chartPoints(chartData,metric);
  $("chart").innerHTML=mode==="kpi"?renderKpi(chartData):mode==="line"?renderLine(points):mode==="share"?renderShare(points):renderBars(points);
}

function renderChart(result){
  chartData=prepareChart(result);
  $("chartControls").classList.toggle("hidden",!chartData);
  if(!chartData){$("chart").innerHTML='<div class="empty">没有可视化数据，请查看数据表格。</div>';return false;}
  const metricSelect=$("metricSelect"),modeSelect=$("chartMode");
  metricSelect.replaceChildren();modeSelect.replaceChildren();
  chartData.metricKeys.forEach(key=>{const option=document.createElement("option");option.value=key;option.textContent=key;metricSelect.appendChild(option);});
  chartData.modes.forEach(mode=>{const option=document.createElement("option");option.value=mode;option.textContent=modeLabels[mode];modeSelect.appendChild(option);});
  metricSelect.value=chartData.metricKeys[0];modeSelect.value=chartData.defaultMode;
  metricSelect.parentElement.classList.toggle("hidden",chartData.metricKeys.length<2||chartData.defaultMode==="kpi");
  modeSelect.parentElement.classList.toggle("hidden",chartData.modes.length<2);
  renderSelectedChart();return true;
}

function updateSession(id, count){
  sessionId = id || null;
  turnCount = Number(count) || 0;
  if(sessionId) sessionStorage.setItem("nl2sqlSessionId",sessionId);
  else sessionStorage.removeItem("nl2sqlSessionId");
  $("sessionBar").classList.toggle("hidden",!sessionId);
  $("sessionStatus").textContent=pendingClarification?`第 ${turnCount} 轮 · 请先选择候选值`:`第 ${turnCount} 轮 · 可以继续追问`;
  submitBtn.querySelector("span").textContent=sessionId?"继续查询":"开始查询";
  question.placeholder=sessionId?"例如：换成华南地区，其他条件不变":"例如：2025年华东地区销售额最高的三个商品";
  question.disabled=pendingClarification;
  submitBtn.disabled=busy||pendingClarification;
}

function addTurn(text, status){
  const item=document.createElement("div");item.className="turn-item";
  const q=document.createElement("strong");q.textContent=text;
  const s=document.createElement("span");s.textContent=status;
  item.append(q,s);$("turns").appendChild(item);$("turns").classList.remove("hidden");
}

function setBusy(value){busy=value;updateSession(sessionId,turnCount);}

function renderResult(d,text,clarifiedValue=null){
  pendingClarification=false;
  currentClarification=null;
  updateSession(d.session_id,d.turn_count);
  $("resultTitle").textContent=text;$("rowCount").textContent=`${d.count} 行结果`;$("sqlCode").textContent=d.sql;
  const notice=$("resolutionNotice"),changes=d.resolutions||[];
  if(changes.length){notice.textContent="已自动匹配："+changes.map(x=>`“${x.requested}” → “${x.resolved}”`).join("；");notice.classList.remove("hidden");}else{notice.classList.add("hidden");notice.textContent="";}
  const edits=(d.changes||[]).filter(x=>x!=="已创建多轮查询");
  $("changeNotice").textContent=edits.length?`本轮调整：${edits.join("；")}`:"";
  $("changeNotice").classList.toggle("hidden",!edits.length);
  const call=d.model_call;
  const source=d.patch_source==="local"?"本地理解 · 未调用模型":d.patch_source==="clarification"?"候选值确认 · 未调用模型":call?.cache_hit?"模型缓存 · 0 token":call?`模型调用 · ${call.total_tokens||0} token`:"";
  $("queryMeta").textContent=[`第 ${d.turn_count} 轮`,source].filter(Boolean).join(" · ");
  addTurn(clarifiedValue?`确认候选值：${clarifiedValue}`:text,`${d.count} 行结果`);
  question.value="";
  const candidateList=$("candidateList");if(candidateList)candidateList.remove();
  renderTable(d.rows);const hasChart=renderChart(d);activateTab(hasChart?"chartPanel":"tablePanel");saveHistory(text);show("result");
}

function activateTab(id){document.querySelectorAll(".tab,.panel").forEach(x=>x.classList.remove("active"));document.querySelectorAll(".tab").forEach(x=>{if(x.dataset.tab===id)x.classList.add("active")});$(id).classList.add("active");}

function renderClarification(detail,originalQuestion,recordTurn=true){
  pendingClarification=true;
  currentClarification={detail,originalQuestion};
  updateSession(detail.session_id,detail.turn_count);
  $("errorText").textContent=detail.message;
  const old=$("candidateList");if(old)old.remove();
  const list=document.createElement("div");list.id="candidateList";list.className="candidate-list";
  (detail.candidates||[]).forEach(candidate=>{const b=document.createElement("button");b.textContent=candidate.entity_id?`${candidate.value} · #${candidate.entity_id}`:candidate.value;b.onclick=()=>runResolved(candidate.value,originalQuestion,candidate.entity_id||null);list.appendChild(b)});
  if(recordTurn)addTurn(originalQuestion,"待确认候选值");
  $("error").appendChild(list);show("error");
}

function showError(message){const old=$("candidateList");if(old)old.remove();$("errorText").textContent=message;show("error");}

function clearSession(){
  pendingClarification=false;currentClarification=null;updateSession(null,0);
  $("turns").replaceChildren();$("turns").classList.add("hidden");
  const old=$("candidateList");if(old)old.remove();
  question.value="";show("welcome");question.focus();
}

async function runResolved(value,originalQuestion,entityId=null){
  if(busy||!sessionId)return;
  show("loading");setBusy(true);
  try{
    const r=await fetch(`/sessions/${encodeURIComponent(sessionId)}/resolve`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({value,entity_id:entityId})});
    const d=await r.json();if(r.status===409&&d.detail?.type==="needs_clarification"){renderClarification(d.detail,originalQuestion);return;}
    if(r.status===404){clearSession();showError("会话已过期，请重新开始查询。");return;}
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:"候选值确认失败");
    renderResult(d,originalQuestion,value);
  }catch(e){
    if(currentClarification){renderClarification(currentClarification.detail,currentClarification.originalQuestion,false);$("errorText").textContent=e.message;}
    else showError(e.message);
  }
  finally{setBusy(false);}
}

async function runQuery(text){
  if(busy||pendingClarification)return;
  const continuing=Boolean(sessionId);
  show("loading");setBusy(true);
  try{
    const path=continuing?`/sessions/${encodeURIComponent(sessionId)}/query`:"/sessions";
    const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:text})});
    const d=await r.json();if(r.status===409&&d.detail?.type==="needs_clarification"){renderClarification(d.detail,text);return;}
    if(r.status===404&&continuing){clearSession();showError("会话已过期，刚才的问题未执行。请重新输入完整问题。");return;}
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:"查询失败");
    renderResult(d,text);
  }catch(e){showError(e.message);}
  finally{setBusy(false);}
}

async function restoreSession(){
  if(!sessionId)return;
  setBusy(true);
  try{
    const r=await fetch(`/sessions/${encodeURIComponent(sessionId)}`);
    if(!r.ok){clearSession();showError(r.status===404?"上次会话已过期，请重新开始查询。":"无法恢复上次会话，请重新开始查询。");return;}
    const d=await r.json();pendingClarification=Boolean(d.pending_clarification);
    updateSession(d.session_id,d.turn_count);
    if(pendingClarification)renderClarification({...d.pending_clarification,session_id:d.session_id,turn_count:d.turn_count},"上次查询");
    else{addTurn("已恢复上次会话","可继续追问，历史结果不会在刷新后保留");show("welcome");}
  }catch{clearSession();showError("无法恢复上次会话，请重新开始查询。");}
  finally{setBusy(false);}
}

form.addEventListener("submit",e=>{e.preventDefault();const text=question.value.trim();if(text)runQuery(text)});
question.addEventListener("keydown",e=>{if(e.ctrlKey&&e.key==="Enter")form.requestSubmit()});
document.querySelectorAll(".example").forEach(b=>b.onclick=()=>{if(busy)return;if(sessionId)clearSession();question.value=b.textContent.trim();form.requestSubmit()});
document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>activateTab(b.dataset.tab));
$("metricSelect").onchange=renderSelectedChart;
$("chartMode").onchange=renderSelectedChart;
$("clearHistory").onclick=()=>{localStorage.removeItem("queryHistory");renderHistory()};
$("copySql").onclick=async()=>{await navigator.clipboard.writeText($("sqlCode").textContent);$("copySql").textContent="已复制";setTimeout(()=>$("copySql").textContent="复制 SQL",1200)};
$("newSessionBtn").onclick=async()=>{if(busy)return;const old=sessionId;clearSession();if(old)try{await fetch(`/sessions/${encodeURIComponent(old)}`,{method:"DELETE"});}catch{/* Server expiry will clean it up. */}};
renderHistory();checkHealth();restoreSession();question.focus();
