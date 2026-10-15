(() => {
'use strict';
const $=id=>document.getElementById(id), base=new URL('.',location.href);
const pt=/\/(qbpt|pt)\//.test(base.pathname);
let session='',rows=[],prefs={},stats={},filter='all',busy=false,selectedHash='',detailTab='overview',settingsTab='downloads',toastTimer,lastTasksHtml='',lastFiltersHtml='';
$('heading').textContent=pt?'PT 下载器':'公共下载器';document.title=$('heading').textContent;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const size=n=>{n=Number(n)||0;const u=['B','KiB','MiB','GiB','TiB'];let i=0;while(Math.abs(n)>=1024&&i<4){n/=1024;i++}return (i?n.toFixed(n>=10?1:2).replace(/\.0+$|(?<=\.[0-9])0$/g,''):n.toFixed(0))+' '+u[i]};
const speed=n=>size(n)+'/s';
const stopped=t=>/^(stopped|paused)/.test(t.state),checking=t=>/checking|moving/.test(t.state),problem=t=>['error','missingFiles','unknown'].includes(t.state),seeding=t=>t.progress===1&&!stopped(t)&&!checking(t)&&!problem(t);
const filters=[['all','全部',()=>true],['downloading','下载中',t=>t.progress<1&&!stopped(t)&&!checking(t)&&!problem(t)],['seeding','做种',seeding],['stopped','已停止',stopped],['checking','校验/移动',checking],['error','异常',problem]];
function displayName(t){const name=t.name.match(/^([\u3400-\u9fff·]+)/)?.[1],episode=/\.(mkv|mp4|avi|ts)$/i.test(t.content_path||'')?(t.content_path||'').match(/S\d+E(\d+)/i)?.[1]:null;return name&&episode?name+' · 第'+Number(episode)+'集':t.name}
function state(t){const names={uploading:'正在上传',stalledUP:'做种中',stoppedUP:'已停止',stoppedDL:'已停止',pausedUP:'已停止',pausedDL:'已停止',downloading:'下载中',stalledDL:'等待下载',metaDL:'获取磁力信息',forcedMetaDL:'获取磁力信息',queuedUP:'等待做种',queuedDL:'排队下载',checkingUP:'校验中',checkingDL:'校验中',checkingResumeData:'读取任务信息',moving:'移动文件中',error:'任务错误',missingFiles:'文件缺失',forcedUP:'强制做种',forcedDL:'强制下载',allocating:'分配空间'};return names[t.state]||'其他状态'}
function notify(message,error=false){$('message').textContent=message;$('message').hidden=false;$('message').style.background=error?'#92400e':'#17243a';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('message').hidden=true,error?10000:4000)}
async function nativeSession(method='GET',data){const r=await fetch(new URL('native/session',base),{method,cache:'no-store',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});if(!r.ok)throw Error('登录未通过，请检查账号密码');return r.json()}
function showLogin(message=''){session='';$('manage').hidden=true;$('login').hidden=false;$('settings').hidden=true;$('refresh').hidden=true;$('login-error').textContent=message;for(const d of document.querySelectorAll('dialog[open]'))d.close()}
function showManage(){ $('login').hidden=true;$('manage').hidden=false;$('settings').hidden=false;$('refresh').hidden=false;}
async function api(path,data){const sid=session;const multipart=data instanceof FormData;const r=await fetch(new URL('api/v2/'+path,base),{cache:'no-store',method:data?'POST':'GET',headers:{'X-Application-Session':sid,...(data&&!multipart?{'Content-Type':'application/x-www-form-urlencoded'}:{})},body:data?(multipart?data:new URLSearchParams(data)):undefined});const text=await r.text();if(!r.ok){if([401,403].includes(r.status)&&sid===session)showLogin('下载器登录已失效，请重新登录。');throw Error('操作失败（'+r.status+'），请刷新后重试')}try{return JSON.parse(text)}catch{return text}}
function render(){
 $('summary').innerHTML=[['下载速度',speed(stats.dl_info_speed)],['上传速度',speed(stats.up_info_speed)],['上传上限',prefs.up_limit>0?speed(prefs.up_limit):'不限速'],['可用空间',size(stats.free_space_on_disk??0)]].map(([k,v])=>`<div class="metric"><span>${k}</span><strong>${esc(v)}</strong></div>`).join('');
 const filterHtml=filters.map(([id,label,match])=>`<button type="button" data-filter="${id}" class="${filter===id?'active':''}" aria-pressed="${filter===id}">${label}<small>${rows.filter(match).length}</small></button>`).join('');
 if(filterHtml!==lastFiltersHtml){$('filters').innerHTML=filterHtml;lastFiltersHtml=filterHtml;}
 const match=filters.find(f=>f[0]===filter)[2],query=$('query').value.trim().toLowerCase();
 const shown=rows.filter(t=>match(t)&&t.name.toLowerCase().includes(query)).sort((a,b)=>Number(seeding(b)||b.progress<1&&!stopped(b))-Number(seeding(a)||a.progress<1&&!stopped(a))||b.added_on-a.added_on);
 const tasksHtml=shown.length?shown.map(t=>`<article class="task"><div class="task-title" title="${esc(t.name)}">${esc(displayName(t))}</div><div class="state-line"><span class="state ${seeding(t)?'seed':problem(t)?'error':t.progress<1&&!stopped(t)?'down':''}">${state(t)}</span><span>${(t.progress*100).toFixed(1).replace('.0','')}% · ${size(t.total_size||t.size)}</span></div><progress max="1" value="${t.progress}" aria-label="下载完成度"></progress><div class="task-data"><div><span>已上传</span><strong>${size(t.uploaded)}</strong></div><div><span>分享率</span><strong>${Number(t.ratio).toFixed(2)}</strong></div><div><span>上传</span><strong>${speed(t.upspeed)}</strong></div><div><span>${t.progress<1?'下载':'连接'}</span><strong>${t.progress<1?speed(t.dlspeed):(t.num_leechs||0)+' / '+(t.num_incomplete||0)}</strong></div></div><div class="task-foot"><span class="path" title="${esc(t.save_path)}">${esc(t.save_path)}</span><button class="button soft" data-detail="${t.hash}">查看详情</button></div></article>`).join(''):'<p class="empty">当前筛选下没有任务</p>';
 if(tasksHtml!==lastTasksHtml){$('tasks').innerHTML=tasksHtml;lastTasksHtml=tasksHtml;}
 $('updated').textContent='刚刚更新 · '+shown.length+' / '+rows.length+' 个任务';
}
async function refresh(){if(busy||!session)return;busy=true;try{const sid=session;const result=await Promise.all([api('torrents/info'),api('transfer/info'),api('app/preferences'),api('sync/maindata')]);if(sid!==session)return;[rows,stats,prefs]=result;stats={...stats,...result[3].server_state};render();}catch(e){notify(e.message,true);$('updated').textContent='更新失败，请刷新重试'}finally{busy=false}}
function pairs(values){return '<dl class="pairs">'+values.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')+'</dl>'}
function safeTracker(url){try{const u=new URL(url);return u.hostname+u.pathname}catch{return url}}
const detailNames=[['overview','概览'],['files','文件'],['trackers','Tracker'],['peers','连接']];
async function loadDetail(){
 const hash=selectedHash,tab=detailTab,t=rows.find(t=>t.hash===hash);if(!t)return;
 $('detail-title').textContent=t.name;$('detail-tabs').innerHTML=detailNames.map(([id,name])=>`<button type="button" data-tab="${id}" class="${id===tab?'active':''}">${name}</button>`).join('');$('detail-body').innerHTML='<p class="muted">正在读取…</p>';
 try{
  let html='';
  if(tab==='overview'){
   const p=await api('torrents/properties?hash='+hash);
   const eta=t.progress===1?'已完成':t.eta>=8640000?'暂无预计时间':t.eta>0?Math.ceil(t.eta/60)+' 分钟':'已完成';
   html=pairs([['状态',state(t)],['完成度',(t.progress*100).toFixed(1)+'%'],['文件体积',size(t.total_size||t.size)],['已下载',size(p.total_downloaded)],['已上传',size(p.total_uploaded)],['当前速度','下载 '+speed(t.dlspeed)+' · 上传 '+speed(t.upspeed)],['分享率',Number(t.ratio).toFixed(3)],['预计剩余',eta],['保存位置',t.save_path],['内容位置',t.content_path],['分类',t.category||'未分类'],['标签',t.tags||'无'],['做种时间',Math.floor((p.seeding_time||0)/3600)+' 小时'],['连接数',(t.num_seeds||0)+' 个做种者 · '+(t.num_leechs||0)+' 个下载者']]);
   html+=`<div class="actions"><button type="button" class="button primary" data-action="${stopped(t)?'start':'stop'}">${stopped(t)?'继续任务':'停止任务'}</button><button type="button" class="button" data-action="recheck">重新校验</button><button type="button" class="button" data-action="reannounce">更新 Tracker</button></div><details><summary>调整任务</summary><label class="field">保存位置<input id="task-path" value="${esc(t.save_path)}"></label><button type="button" class="button" data-action="setLocation">保存并移动文件</button><label class="field">分类<input id="task-category" value="${esc(t.category)}" list="categories"></label><button type="button" class="button" data-action="setCategory">保存分类</button><p class="muted">移动和重新校验期间可能暂时停止上传。</p></details>`;
  }else if(tab==='files'){
   const files=await api('torrents/files?hash='+hash);html=files.length?files.map(f=>`<div class="file"><strong>${esc(f.name)}</strong><p class="muted">${size(f.size)} · 完成 ${(f.progress*100).toFixed(1)}%</p><select aria-label="${esc(f.name)} 的下载优先级" data-file="${f.index}">${[[0,'不下载'],[1,'正常优先级'],[6,'高优先级'],[7,'最高优先级']].map(([v,n])=>`<option value="${v}" ${v===f.priority?'selected':''}>${n}</option>`).join('')}</select></div>`).join(''):'<p class="empty">正在获取文件信息</p>';
  }else if(tab==='trackers'){
   const ts=await api('torrents/trackers?hash='+hash);const names=['未联系','暂无状态','正常工作','正在更新','连接失败'];html=ts.map(x=>`<div class="tracker"><strong>${esc(safeTracker(x.url))}</strong><p>${esc(x.msg==='This torrent is private'?'已禁用（私有种子）':names[x.status]||'未知状态')} · 做种 ${x.num_seeds<0?'未知':x.num_seeds} · 下载 ${x.num_leeches<0?'未知':x.num_leeches}</p>${x.msg&&x.msg!=='This torrent is private'?'<p class="muted">'+esc(x.msg)+'</p>':''}</div>`).join('')||'<p class="empty">没有 Tracker</p>';
  }else{
   const peers=await api('sync/torrentPeers?hash='+hash);const list=Object.values(peers.peers||{});html=list.length?list.map(p=>`<div class="peer"><strong>${esc(p.ip)}:${p.port}</strong><p class="muted">${esc(p.client||'未知客户端')} · 完成 ${((p.progress||0)*100).toFixed(1)}%</p><p>下载 ${speed(p.dl_speed)} · 上传 ${speed(p.up_speed)}</p></div>`).join(''):'<p class="empty">当前没有已连接的节点。做种任务会继续等待下载者。</p>';
  }
  if(hash===selectedHash&&tab===detailTab)$('detail-body').innerHTML=html;
 }catch(e){if(hash===selectedHash)$('detail-body').textContent=e.message}
}
$('tasks').onclick=e=>{const b=e.target.closest('[data-detail]');if(!b)return;selectedHash=b.dataset.detail;detailTab='overview';$('detail').showModal();loadDetail()};
$('detail-tabs').onclick=e=>{const b=e.target.closest('[data-tab]');if(b){detailTab=b.dataset.tab;loadDetail()}};
$('detail-body').onclick=async e=>{
 const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action,data={hashes:selectedHash};
 if(action==='recheck'&&!confirm('重新校验此任务？校验期间会暂时停止上传。'))return;
 if(action==='setLocation'){data.location=$('task-path').value;if(!data.location.startsWith('/media/')){notify('保存位置必须在 /media/ 下',true);return}if(!confirm('将此任务文件移动到新的保存位置？'))return}
 if(action==='setCategory')data.category=$('task-category').value;
 b.disabled=true;try{await api('torrents/'+action,data);notify('操作已提交');await refresh();await loadDetail()}catch(e){notify(e.message,true)}finally{b.disabled=false}
};
$('detail-body').onchange=async e=>{if(!e.target.dataset.file)return;const input=e.target;input.disabled=true;try{await api('torrents/filePrio',{hash:selectedHash,id:input.dataset.file,priority:input.value});notify('文件优先级已保存')}catch(e){notify(e.message,true);loadDetail()}finally{input.disabled=false}};
const field=(key,label,type='text',factor=1)=>`<label class="field">${label}<input name="${key}" type="${type}" ${type==='number'?'min="0"':''} data-factor="${factor}" value="${esc(type==='number'?Math.max(0,prefs[key]||0)/factor:prefs[key]||'')}"></label>`;
const check=(key,label)=>`<label class="check"><input name="${key}" type="checkbox" ${prefs[key]?'checked':''}>${label}</label>`;
function renderSettings(){
 $('settings-tabs').innerHTML=[['downloads','下载目录'],['speed','速度与做种'],['connection','连接']].map(([id,label])=>`<button type="button" data-setting="${id}" class="${id===settingsTab?'active':''}">${label}</button>`).join('');$('settings-status').textContent='';
 let html='';
 if(settingsTab==='downloads')html='<p class="notice">这里设置新任务的默认目录。已有任务的保存位置请在任务详情里调整。</p>'+field('save_path','默认保存位置')+check('temp_path_enabled','使用独立的未完成目录')+field('temp_path','未完成目录')+check('incomplete_files_ext','未完成文件添加 .!qB 后缀')+check('preallocate_all','为文件预分配空间');
 if(settingsTab==='speed')html='<p class="notice">上传总上限保持 1 MiB/s。PT 完成后持续做种；公共任务遵循现有的完成后停止规则。</p>'+pairs([['上传总上限',speed(prefs.up_limit)],['本次已上传',size(stats.up_info_data)],['本次已下载',size(stats.dl_info_data)],['做种分享率上限',prefs.max_ratio_enabled?String(prefs.max_ratio):'不限']])+field('dl_limit','下载总上限（KiB/s，0 为不限）','number',1024)+field('max_uploads','同时上传的连接上限（0 为不限）','number')+field('max_uploads_per_torrent','单任务上传连接上限（0 为不限）','number');
 if(settingsTab==='connection')html=pairs([['连接方式',pt?'直连 · 不使用代理':'公共下载使用已配置代理'],['代理类型',prefs.proxy_type==='None'?'无':prefs.proxy_type],['监听端口',prefs.listen_port],['DHT',prefs.dht?'已开启':'已关闭'],['节点交换',prefs.pex?'已开启':'已关闭'],['局域网发现',prefs.lsd?'已开启':'已关闭']])+field('max_connec','全局连接上限（0 为不限）','number')+field('max_connec_per_torrent','单任务连接上限（0 为不限）','number')+'<p class="notice">此页保留现有代理和 PT 网络策略，不更改账号、路由或公网设置。</p>';
 $('settings-body').innerHTML=html;
}
$('settings').onclick=async()=>{await refresh();settingsTab='downloads';renderSettings();$('settings-dialog').showModal()};
$('settings-tabs').onclick=e=>{const b=e.target.closest('[data-setting]');if(b){settingsTab=b.dataset.setting;renderSettings()}};
$('settings-form').onsubmit=async e=>{e.preventDefault();const changed={};for(const input of $('settings-body').querySelectorAll('input[name]')){let value=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value)*Number(input.dataset.factor):input.value;if(input.type==='number'&&value===0&&input.name.startsWith('max_'))value=-1;if(input.type==='number'&&!Number.isFinite(value)){notify('请输入有效数字',true);return}if(['save_path','temp_path'].includes(input.name)&&!value.startsWith('/media/')){notify('下载目录必须在 /media/ 下',true);return}if(value!==prefs[input.name])changed[input.name]=value}if(!Object.keys(changed).length){$('settings-status').textContent='设置没有变化';return}const btn=e.submitter;btn.disabled=true;try{await api('app/setPreferences',{json:JSON.stringify(changed)});await refresh();renderSettings();$('settings-status').textContent='设置已保存'}catch(e){$('settings-status').textContent=e.message}finally{btn.disabled=false}};
$('logout').onclick=async()=>{try{await nativeSession('DELETE');try{await api('auth/logout',{})}catch{}showLogin();notify('已退出，自动登录已关闭')}catch(e){notify('退出失败，请重试',true)}};
$('add').onclick=()=>{$('add-path').value=prefs.save_path;$('add-note').textContent=pt?'PT 任务仅添加站点提供的种子。公开磁力资源请使用公共下载器。':'磁力和公开种子使用此下载器的现有代理，完成后遵循停止规则。';$('add-dialog').showModal()};
$('add-form').onsubmit=async e=>{e.preventDefault();const urls=$('add-urls').value.trim(),files=$('file-upload').files,path=$('add-path').value;if(!urls&&!files.length){notify('请填写链接或选择种子文件',true);return}if(urls.split(/\n/).filter(u=>u.trim()).some(u=>!/^magnet:\?|^https?:\/\//i.test(u.trim()))){notify('仅支持磁力链接或 HTTP 种子网址',true);return}if(!path.startsWith('/media/')){notify('下载目录必须在 /media/ 下',true);return}if(stats.free_space_on_disk<10*1024**3){notify('可用空间不足 10 GiB，请先释放空间',true);return}const form=new FormData();if(urls)form.set('urls',urls);for(const f of files)form.append('torrents',f);form.set('savepath',path);form.set('stopped',String($('add-stopped').checked));e.submitter.disabled=true;try{const reply=await api('torrents/add',form);if(typeof reply==='string'&&!['','Ok.'].includes(reply.trim()))throw Error('下载器未接受任务，请检查链接');if(reply&&typeof reply==='object'&&!(reply.success_count||reply.pending_count))throw Error('下载器未接受任务，请检查链接');$('add-dialog').close();$('add-urls').value='';$('file-upload').value='';notify(reply?.failure_count?'已接受 '+((reply.success_count||0)+(reply.pending_count||0))+' 项，失败 '+reply.failure_count+' 项，请检查链接':'任务已提交，正在刷新列表',!!reply?.failure_count);await refresh()}catch(e){notify(e.message,true)}finally{e.submitter.disabled=false}};
$('filters').onclick=e=>{const b=e.target.closest('[data-filter]');if(b){filter=b.dataset.filter;render()}};$('query').oninput=render;$('refresh').onclick=refresh;
for(const b of document.querySelectorAll('[data-close]'))b.onclick=()=>$(b.dataset.close).close();
$('login').onsubmit=async e=>{e.preventDefault();e.submitter.disabled=true;try{const j=await nativeSession('POST',{username:$('username').value,password:$('password').value,remember:$('remember').checked});session=j.session;$('password').value='';showManage();await refresh()}catch(e){$('login-error').textContent=e.message}finally{e.submitter.disabled=false}};
(async()=>{try{const j=await nativeSession();session=j.session;showManage();await refresh()}catch{showLogin()}})();
setInterval(()=>{if(!document.hidden&&session&&!document.querySelector('dialog[open]'))refresh()},5000);
})();
