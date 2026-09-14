const sourcesBox=document.getElementById("sources"),resultsBox=document.getElementById("searchResults");
const escapeHtml=v=>String(v??"").replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

async function loadSources(){
  sourcesBox.innerHTML='<div class="muted">正在读取数据源…</div>';
  try{
    const response=await fetch("/sources"),data=await response.json();
    sourcesBox.innerHTML=data.sources.map(source=>`<article class="source-card"><div class="source-head"><div><small>${escapeHtml(source.dialect)}</small><h2>${escapeHtml(source.name)}</h2><code>${escapeHtml(source.id)}</code></div><span class="state ${source.connection_configured?'ok':'bad'}">${source.connection_configured?'已配置':'未配置'}</span></div><dl><div><dt>目录状态</dt><dd>${source.catalog_ready?'已生成':'未生成'}</dd></div><div><dt>数据表</dt><dd>${source.table_count??'—'}</dd></div><div><dt>检索文档</dt><dd>${source.document_count??'—'}</dd></div><div><dt>数据库</dt><dd>${escapeHtml(source.database??'—')}</dd></div></dl><div class="actions"><button onclick="testSource('${source.id}',this)">测试连接</button><button class="primary" onclick="scanSource('${source.id}',this)">重新扫描</button></div><p class="message" id="message-${source.id}"></p></article>`).join("");
  }catch(error){sourcesBox.innerHTML=`<div class="error">${escapeHtml(error.message)}</div>`;}
}

async function sourceAction(id,button,url,body){
  const message=document.getElementById(`message-${id}`);button.disabled=true;message.textContent="正在处理…";
  try{const response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined}),data=await response.json();if(!response.ok)throw new Error(data.detail||"操作失败");message.textContent=data.tables?`扫描完成：${data.tables} 张表，${data.documents} 条文档`:`连接成功：${data.database}`;if(data.tables)setTimeout(loadSources,700);}catch(error){message.textContent=error.message;}finally{button.disabled=false;}
}
function testSource(id,button){return sourceAction(id,button,`/sources/${id}/test`);}
function scanSource(id,button){return sourceAction(id,button,`/sources/${id}/scan`,{include_value_samples:document.getElementById("samples").checked,max_sample_values:20});}

document.getElementById("searchForm").addEventListener("submit",async event=>{event.preventDefault();const query=document.getElementById("searchQuery").value.trim();if(!query)return;resultsBox.innerHTML='<div class="muted">正在检索…</div>';try{const response=await fetch("/catalog/search",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query,limit:8})}),data=await response.json();resultsBox.innerHTML=data.results.map(item=>`<article><span>${escapeHtml(item.document_type)}</span><strong>${escapeHtml(item.title)}</strong><small>得分 ${item.score}</small><p>${escapeHtml(item.text)}</p></article>`).join("")||'<div class="muted">没有候选</div>';}catch(error){resultsBox.innerHTML=`<div class="error">${escapeHtml(error.message)}</div>`;}});
document.getElementById("refresh").onclick=loadSources;
loadSources();
