const $ = (id) => document.getElementById(id);
const form = $("queryForm"), question = $("question"), submitBtn = $("submitBtn");
const screens = ["welcome", "loading", "result", "error"];

function show(id){ screens.forEach(x => $(x).classList.toggle("hidden", x !== id)); }
function escapeHtml(v){ return String(v ?? "").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function historyItems(){ try{return JSON.parse(localStorage.getItem("queryHistory") || "[]");}catch{return [];} }
function renderHistory(){ const box=$("history"); box.innerHTML=""; historyItems().forEach(text=>{const b=document.createElement("button");b.textContent=text;b.onclick=()=>{question.value=text;question.focus()};box.appendChild(b)}); }
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

function renderChart(rows){
  const chart=$("chart"); if(!rows.length){chart.innerHTML='<div class="empty">没有可视化数据</div>';return;}
  const cols=Object.keys(rows[0]),growth=cols.find(c=>c.includes("增长率"));
  if(rows.length===1&&growth){
    const valueCols=cols.filter(c=>c!==growth&&typeof rows[0][c]==="number"),max=Math.max(...valueCols.map(c=>Number(rows[0][c])||0),1);
    chart.innerHTML=valueCols.map(c=>`<div class="bar-row"><span>${escapeHtml(c)}</span><div class="bar-track"><div class="bar" style="width:${((Number(rows[0][c])||0)/max*100).toFixed(1)}%"></div></div><span class="bar-value">${Number(rows[0][c]).toLocaleString()}</span></div>`).join("")+`<div class="growth-card"><span>${escapeHtml(growth)}</span><strong>${rows[0][growth]===null?'无法计算':`${Number(rows[0][growth]).toFixed(2)}%`}</strong></div>`;return;
  }
  const numeric=cols.find(c=>rows.some(r=>typeof r[c]==="number")), label=cols.find(c=>c!==numeric);
  if(!numeric||!label){chart.innerHTML='<div class="empty">当前结果不适合生成基础图表</div>';return;}
  const values=rows.slice(0,12), max=Math.max(...values.map(r=>Number(r[numeric])||0),1);
  chart.innerHTML=values.map(r=>`<div class="bar-row"><span title="${escapeHtml(r[label])}">${escapeHtml(r[label])}</span><div class="bar-track"><div class="bar" style="width:${((Number(r[numeric])||0)/max*100).toFixed(1)}%"></div></div><span class="bar-value">${Number(r[numeric]).toLocaleString()}</span></div>`).join("");
}

function renderResult(d,text){
  $("resultTitle").textContent=text;$("rowCount").textContent=`${d.count} 行结果`;$("sqlCode").textContent=d.sql;
  const notice=$("resolutionNotice"),changes=d.resolutions||[];
  if(changes.length){notice.textContent="已自动匹配："+changes.map(x=>`“${x.requested}” → “${x.resolved}”`).join("；");notice.classList.remove("hidden");}else{notice.classList.add("hidden");notice.textContent="";}
  renderTable(d.rows);renderChart(d.rows);saveHistory(text);show("result");
}

function renderClarification(detail,originalQuestion){
  $("errorText").textContent=detail.message;
  const old=$("candidateList");if(old)old.remove();
  const list=document.createElement("div");list.id="candidateList";list.className="candidate-list";
  detail.candidates.forEach(candidate=>{const b=document.createElement("button");b.textContent=candidate.value;b.onclick=()=>runResolved(detail,candidate.value,originalQuestion);list.appendChild(b)});
  $("error").appendChild(list);show("error");
}

async function runResolved(detail,value,originalQuestion){
  show("loading");submitBtn.disabled=true;
  try{
    const r=await fetch("/query/resolve",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query_spec:detail.query_spec,filter_index:detail.filter_index,value})});
    const d=await r.json();if(r.status===409&&d.detail?.type==="needs_clarification"){renderClarification(d.detail,originalQuestion);return;}if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:"查询失败");
    renderResult(d,originalQuestion);
  }catch(e){$("errorText").textContent=e.message;show("error");}
  finally{submitBtn.disabled=false;}
}

async function runQuery(text){
  show("loading");submitBtn.disabled=true;
  try{
    const r=await fetch("/query",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:text})});
    const d=await r.json();if(r.status===409&&d.detail?.type==="needs_clarification"){renderClarification(d.detail,text);return;}if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:"查询失败");
    renderResult(d,text);
  }catch(e){$("errorText").textContent=e.message;show("error");}
  finally{submitBtn.disabled=false;}
}

form.addEventListener("submit",e=>{e.preventDefault();const text=question.value.trim();if(text)runQuery(text)});
question.addEventListener("keydown",e=>{if(e.ctrlKey&&e.key==="Enter")form.requestSubmit()});
document.querySelectorAll(".example").forEach(b=>b.onclick=()=>{question.value=b.textContent.trim();form.requestSubmit()});
document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tab,.panel").forEach(x=>x.classList.remove("active"));b.classList.add("active");$(b.dataset.tab).classList.add("active")});
$("clearHistory").onclick=()=>{localStorage.removeItem("queryHistory");renderHistory()};
$("copySql").onclick=async()=>{await navigator.clipboard.writeText($("sqlCode").textContent);$("copySql").textContent="已复制";setTimeout(()=>$("copySql").textContent="复制 SQL",1200)};
renderHistory();checkHealth();question.focus();
