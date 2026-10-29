"use strict";
const $ = id => document.getElementById(id);
let key = sessionStorage.getItem("proxypoolKey") || "";
let entries = [], expanded = null, expandedPage = 1, timer = null, latestStats = null;
$("apiKey").value = key;
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function notice(text, error=false) { $("notice").textContent=text; $("notice").className=error?"error":""; }
async function api(path, method="GET", body) {
  const response = await fetch(path,{method,headers:{Authorization:"Bearer "+key,...(body?{"Content-Type":"application/json"}:{})},body:body?JSON.stringify(body):undefined});
  const data = await response.json();
  if (!response.ok) throw new Error(response.status===401?"API Key 不正确":(data.error||data.detail||"请求失败"));
  return data;
}
function button(text, action, cls="secondary") { const el=node("button",text,cls); el.type="button"; el.onclick=()=>Promise.resolve().then(action).catch(e=>notice(e.message,true)); return el; }
function cell(row, text) { const el=node("td",text); row.append(el); return el; }
function date(value) { return value?new Date(value).toLocaleString():"—"; }
function empty(body,columns,text) { const row=node("tr"); const el=cell(row,text);el.colSpan=columns;body.append(row); }
function schedule() { clearTimeout(timer); const seconds=Math.max(5,Math.min(600,Number($("refreshSeconds").value)||15)); if (key) timer=setTimeout(refresh,seconds*1000); }
async function refresh() {
  if (!key) return;
  try {
    const [stats,sources,registered] = await Promise.all([api("/v1/stats"),api("/v1/sources"),api("/v1/source-entries")]);
    latestStats=stats; entries=registered.items;
    $("poolCount").textContent=`${stats.proxy_count.toLocaleString()} / ${stats.pool_limit.toLocaleString()}`;
    $("sourceCount").textContent=stats.source_count;$("targetCount").textContent=stats.targets.filter(x=>x.enabled).length;
    $("scoreHeading").textContent=`高分代理（≥${stats.candidate_min_score}）`;
    renderSources(sources.items);renderTargets(stats.targets);
    $("capture").disabled=!stats.publishing.configured||stats.publishing.busy;
    $("test").disabled=!stats.publishing.configured||stats.publishing.busy;
    $("publishing").textContent=stats.publishing.last_error?"最近发布失败："+stats.publishing.last_error:stats.publishing.configured?(stats.publishing.enabled?"已启用固定周期发布":"自动发布关闭，可手动触发") :"Crawlab 尚未配置；保存配置不会自动启动采集。";
    $("updated").textContent="更新于 "+new Date().toLocaleTimeString();
    if (expanded!==null && stats.targets.some(t=>t.target_id===expanded)) await loadProxies(expanded,expandedPage);
    else expanded=null;
  } catch(error) { notice(error.message,true); }
  schedule();
}
function toggle(row, checked, action) { const input=node("input"); input.type="checkbox";input.checked=Boolean(checked);input.setAttribute("aria-label","启用 "+row.name);input.onchange=async()=>{input.disabled=true;try{await action(input.checked);await refresh();}catch(e){input.checked=!input.checked;notice(e.message,true);}finally{input.disabled=false;}}; return input; }
function sourceData(row, enabled=row.enabled) {return {name:row.name,url:row.url,spider_key:row.spider_key,enabled:Boolean(enabled)};}
function targetData(row, enabled=row.enabled) {return {name:row.name,test_url:row.test_url,enabled:Boolean(enabled)};}
function renderSources(items) {
  const body=$("sourcesBody");body.replaceChildren();
  if (!items.length) return empty(body,6,"尚未添加来源；可从已登记脚本中选择。");
  for (const item of items) {
    const row=node("tr"); const name=cell(row,item.name);name.append(node("small",item.spider_key));
    cell(row).append(toggle(item,item.enabled,enabled=>api(`/v1/sources/${item.source_id}`,"PUT",sourceData(item,enabled))));
    const waiting=item.last_published_at&&(!item.last_result_at||item.last_published_at>item.last_result_at);
    const state=waiting?"未收到本次结果":!item.last_result_at?"尚无结果":item.outcome==="failed"?"失败":item.output_count===0?"输出为0":"正常";
    const result=cell(row,state);result.className=state==="正常"?"ok":state==="失败"?"bad":"warn";result.append(node("small",date(item.last_result_at)));
    if(item.error_code)result.append(node("small",item.error_code));
    cell(row,item.output_count===null?"—":`${item.output_count} / ${item.inserted_count}`);
    cell(row,item.duration_ms===null?"—":(item.duration_ms/1000).toFixed(1)+"s");
    const actions=cell(row);actions.append(button("修改",()=>editSource(item)),button("删除",async()=>{await api(`/v1/sources/${item.source_id}`,"DELETE");await refresh();},"danger"));
    if(item.latest_task_id&&latestStats.crawlab_ui_url){const link=node("a","Crawlab");link.href=latestStats.crawlab_ui_url;link.target="_blank";link.rel="noopener noreferrer";actions.append(link);actions.append(node("small",item.latest_task_id));}body.append(row);
  }
}
function renderTargets(items) {
  const body=$("targetsBody");body.replaceChildren();
  if (!items.length) return empty(body,5,"添加要测试的网址后，代理才能积累对应评价。");
  for(const item of items){
    const row=node("tr");const name=cell(row,item.name);name.append(node("small",item.test_url));
    cell(row).append(toggle(item,item.enabled,enabled=>api(`/v1/targets/${item.target_id}`,"PUT",targetData(item,enabled))));
    cell(row,item.quality_count);cell(row,item.high_score_count);
    cell(row).append(button(expanded===item.target_id?"收起代理":"高分代理",async()=>{if(expanded===item.target_id){expanded=null;renderTargets(items);}else{expanded=item.target_id;expandedPage=1;renderTargets(items);await loadProxies(expanded,1);}}),button("修改",()=>editTarget(item)),button("删除",async()=>{await api(`/v1/targets/${item.target_id}`,"DELETE");if(expanded===item.target_id)expanded=null;await refresh();},"danger"));
    body.append(row);
    if(expanded===item.target_id){const detail=node("tr");const container=cell(detail);container.colSpan=5;container.className="proxyDetail";container.id="proxyDetail";container.textContent="加载中…";body.append(detail);}
  }
}
async function loadProxies(id,page){
  const data=await api(`/v1/proxies?target_id=${id}&min_score=${latestStats.candidate_min_score}&page=${page}&size=20`);
  if(expanded!==id||!$("proxyDetail"))return;
  expandedPage=page;const container=$("proxyDetail");container.replaceChildren(node("h3","高分代理"));
  if(!data.items.length){container.append(node("p","当前没有达到分数门槛的代理。"));return;}
  const table=node("table");const header=node("tr");for(const title of ["代理地址","分数","成功 / 样本","成功耗时","最近反馈"])header.append(node("th",title));table.append(header);
  for(const item of data.items){const row=node("tr");cell(row,item.proxy_url);cell(row,item.score.toFixed(2));cell(row,`${item.success_count} / ${item.sample_count}`);cell(row,item.successful_latency_ewma_ms===null?"—":Math.round(item.successful_latency_ewma_ms)+"ms");cell(row,date(item.last_feedback_at));table.append(row);}container.append(table);
  const pages=node("div",undefined,"pagination");const prev=button("上一页",()=>loadProxies(id,page-1));prev.disabled=page===1;const next=button("下一页",()=>loadProxies(id,page+1));next.disabled=page*data.size>=data.total;pages.append(prev,node("span",`第 ${page} 页 · 共 ${data.total} 条`),next);container.append(pages);
}
function editTarget(item={}){const form=$("targetForm");form.reset();form.elements.id.value=item.target_id||"";form.elements.name.value=item.name||"";form.elements.test_url.value=item.test_url||"";form.elements.enabled.checked=item.enabled===undefined?true:Boolean(item.enabled);$("targetDialog").showModal();}
function editSource(item={}){const form=$("sourceForm");form.reset();form.elements.id.value=item.source_id||"";form.elements.name.value=item.name||"";form.elements.url.value=item.url||"";form.elements.enabled.checked=Boolean(item.enabled);const select=form.elements.spider_key;select.replaceChildren();for(const entry of entries){const option=node("option",entry.name);option.value=entry.key;select.append(option);}if(item.spider_key)select.value=item.spider_key;$("sourceDialog").showModal();}
$("targetForm").onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;try{const id=form.elements.id.value;await api("/v1/targets"+(id?"/"+id:""),id?"PUT":"POST",{name:form.elements.name.value,test_url:form.elements.test_url.value,enabled:form.elements.enabled.checked});$("targetDialog").close();notice("测试网址已保存");await refresh();}catch(e){notice(e.message,true);}};
$("sourceForm").onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;try{const id=form.elements.id.value;await api("/v1/sources"+(id?"/"+id:""),id?"PUT":"POST",{name:form.elements.name.value,url:form.elements.url.value,spider_key:form.elements.spider_key.value,enabled:form.elements.enabled.checked});$("sourceDialog").close();notice("来源已保存");await refresh();}catch(e){notice(e.message,true);}};
$("sourceForm").elements.spider_key.onchange=event=>{const entry=entries.find(e=>e.key===event.target.value);if(entry)$("sourceForm").elements.url.value=entry.example_url;};
$("connectForm").onsubmit=event=>{event.preventDefault();key=$("apiKey").value;sessionStorage.setItem("proxypoolKey",key);notice("正在连接…");refresh();};
$("disconnect").onclick=()=>{key="";sessionStorage.removeItem("proxypoolKey");$("apiKey").value="";clearTimeout(timer);notice("已退出当前会话");};
$("addTarget").onclick=()=>editTarget();$("addSource").onclick=()=>editSource();$("refresh").onclick=refresh;$("refreshSeconds").onchange=schedule;
$("capture").onclick=async()=>{try{await api("/v1/actions/capture","POST");notice("已请求发布抓取任务，结果由 Worker 完成后更新。");await refresh();}catch(e){notice(e.message,true);}};
$("test").onclick=async()=>{try{await api("/v1/actions/test","POST");notice("已请求发布测试任务，结果由 Worker 完成后更新。");await refresh();}catch(e){notice(e.message,true);}};
document.querySelectorAll("[data-close]").forEach(button=>button.onclick=()=>$(button.dataset.close).close());
if(key)refresh();
