(() => {
  "use strict";
  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
  const state={users:[],errors:[],logs:[],poll:null,currentSection:"overview"};
  // Critical local-dev fix: use the exact origin serving /admin so localhost and 127.0.0.1 cookies are never split.
  const API=(window.__EMA_BACKEND_URL__ || ((location.hostname === "localhost" || location.hostname === "127.0.0.1") ? "http://127.0.0.1:8000" : ""));
  const ADMIN_SESSION_KEY="ema_admin_session";
  const getAdminSession=()=>{try{return sessionStorage.getItem(ADMIN_SESSION_KEY)||""}catch{return""}};
  const clearAdminSession=()=>{try{sessionStorage.removeItem(ADMIN_SESSION_KEY)}catch{}};
  let csrfToken="";
  const pageMeta={
    overview:["Overview","Operations overview","Live control plane for the EMA service."],
    users:["Users & accounts","Users & accounts","Connected identities, monitoring state and processing history."],
    activity:["Activity","Processing activity","Cross-account action metadata from EMA state storage."],
    errors:["Errors","Errors & warnings","Subsystem classification for current warnings and failures."],
    logs:["Logs","System logs","Recent lines from the rotating EMA log."]
  };
  const commands=[
    ["overview","Open overview","Return to the operational dashboard.","layout-dashboard","1"],
    ["users","Open users","View connected accounts.","users","2"],
    ["activity","Open activity","Inspect recent processing actions.","route","3"],
    ["errors","Open errors","Review warnings and failures.","triangle-alert","4"],
    ["logs","Open logs","Inspect the recent system log tail.","terminal-square","5"],
    ["refresh","Refresh telemetry","Reload all live admin data.","refresh-cw","R"],
    ["worker-start","Start worker","Start background processing.","play","W"],
    ["worker-stop","Stop worker","Pause background processing.","square","S"],
    ["inspect-start","Start inspection","Scan older unprocessed emails.","scan-search","I"]
  ];
  const esc=v=>{const d=document.createElement("div");d.textContent=v??"";return d.innerHTML};
  const fmtNum=n=>Number(n||0).toLocaleString("en-IN");
  const fmtDate=v=>{if(!v)return "—";const d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString([], {dateStyle:"medium",timeStyle:"short"})};
  const setText=(s,v)=>{const e=$(s);if(e)e.textContent=v??"—"};
  function animateCount(el,to){if(!el)return;const from=Number(el.dataset.n||0),end=Number(to||0),dur=520,t0=performance.now();function step(t){const p=Math.min(1,(t-t0)/dur),e=1-Math.pow(1-p,3),v=Math.round(from+(end-from)*e);el.textContent=fmtNum(v);el.dataset.n=String(v);if(p<1)requestAnimationFrame(step)}requestAnimationFrame(step)}
  async function api(path,opts={}){const headers=new Headers(opts.headers||{});headers.set("Accept","application/json");const adminSession=getAdminSession();if(adminSession&&!headers.has("X-EMA-Admin-Session"))headers.set("X-EMA-Admin-Session",adminSession);const method=(opts.method||"GET").toUpperCase();if(method!=="GET"&&csrfToken)headers.set("X-EMA-Admin-CSRF",csrfToken);if(opts.body&&!headers.has("Content-Type"))headers.set("Content-Type","application/json");const res=await fetch(API+path,{...opts,headers,credentials:"include",cache:"no-store"});let body=null;try{body=await res.json()}catch{}if(res.status===401){clearAdminSession();csrfToken="";hideShell();showLogin("Admin session expired. Please sign in again.")}if(!res.ok){const err=new Error(body?.detail||body?.message||`${res.status} ${res.statusText}`);err.status=res.status;throw err}return body}
  function toast(msg,kind="info"){const t=$("#toast");t.hidden=false;t.className=`toast ${kind}`;t.textContent=msg;clearTimeout(toast.t);toast.t=setTimeout(()=>t.hidden=true,3200)}
  function setView(name){if(!pageMeta[name])name="overview";state.currentSection=name;$$('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${name}`));$$('.admin-nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===name));$$('.mobile-tabs button').forEach(b=>b.classList.toggle('active',b.dataset.view===name));const meta=pageMeta[name];setText('#view-label',meta[0]);setText('#page-title',meta[1]);setText('#page-subtitle',meta[2]);if(location.hash!==`#${name}`)history.replaceState(null,'',`#${name}`);if(name==='users')loadUsers();if(name==='activity')loadActivity();if(name==='errors')loadErrors();if(name==='logs')loadLogs();requestIcons()}
  let iconTimer=0;
  let iconReadyListener=false;
  function requestIcons(){
    if(!document.querySelector('i[data-lucide]')) return;
    if(!window.lucide?.createIcons){
      if(!iconReadyListener){
        iconReadyListener=true;
        addEventListener('load', requestIcons, {once:true});
      }
      return;
    }
    if(iconTimer) return;
    iconTimer=setTimeout(()=>{ iconTimer=0; if(document.querySelector('i[data-lucide]')) { try{lucide.createIcons()}catch{} } },100);
  }
  $$('.admin-nav button,.mobile-tabs button').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
  addEventListener('hashchange',()=>setView(location.hash.slice(1)||'overview'));

  function showLogin(message=""){const s=$("#admin-login");if(s){s.hidden=false}s?.classList.add('show');const err=$("#admin-login-error");if(err){err.textContent=message;err.hidden=!message}setTimeout(()=>$("#admin-username")?.focus(),80)}
  function hideLogin(){const s=$("#admin-login");if(s){s.classList.remove('show');s.hidden=true}const err=$("#admin-login-error");if(err){err.textContent="";err.hidden=true}}
  function showShell(){const s=$("#admin-shell");if(s)s.hidden=false}
  function hideShell(){const s=$("#admin-shell");if(s)s.hidden=true}

  function closeEntry(){const e=$("#entry"),v=$("#entry-video");if(!e)return;e.classList.add('is-hidden');e.setAttribute('aria-hidden','true');setTimeout(()=>{e.hidden=true},650);try{v.pause()}catch{} }
  async function playEntry(){
    const e=$("#entry"),v=$("#entry-video");
    if(!e||!v)return;
    e.hidden=false; e.classList.remove('is-hidden'); e.setAttribute('aria-hidden','false');
    v.muted=true; v.setAttribute('muted',''); v.setAttribute('playsinline',''); v.currentTime=0;
    try {
      if(!v.src){ v.src=v.dataset.src || '/assets/ema-entry.mp4'; v.poster='/assets/ema-entry-poster.jpg'; }
      v.load();
      if(v.readyState<3){
        await new Promise(resolve=>{
          const done=()=>{v.removeEventListener('canplay',done);v.removeEventListener('error',done);resolve();};
          v.addEventListener('canplay',done,{once:true});
          v.addEventListener('error',done,{once:true});
        });
      }
      await v.play();
    } catch (_) {
      setTimeout(()=>{ if(!v.paused) return; closeEntry(); },1200);
    }
    requestIcons();
  }
  $("#entry-video")?.addEventListener('timeupdate',e=>{const v=e.currentTarget,p=$(".entry-progress span");if(p&&v.duration)p.style.width=`${Math.min(100,v.currentTime/v.duration*100)}%`});
  $("#entry-video")?.addEventListener('ended',closeEntry,{once:true});
  $("#entry-video")?.addEventListener('error',()=>setTimeout(closeEntry,800),{once:true});

  async function verifyAdmin(){try{const me=await api('/api/admin/auth/me');csrfToken=me.csrf_token||'';setText('#admin-email',me.username||'Administrator');setText('#admin-role','ADMIN ACCESS');const av=$("#admin-avatar");if(av)av.textContent=(me.username||'A').slice(0,1).toUpperCase();hideLogin();showShell();playEntry();return true}catch(e){hideShell();showLogin(e.status===503?e.message:'');return false}}
  async function adminLogin(ev){ev.preventDefault();const button=$("#admin-login-submit"),username=$("#admin-username")?.value.trim()||'',password=$("#admin-password")?.value||'',err=$("#admin-login-error");if(!username||!password){if(err){err.textContent='Enter both username and password.';err.hidden=false}return}button.disabled=true;if(err){err.textContent='Authenticating…';err.hidden=false}try{const res=await fetch(API+'/api/admin/auth/login',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({username,password})});const data=await res.json().catch(()=>({}));if(!res.ok)throw Object.assign(new Error(data.detail||'Admin login failed.'),{status:res.status});if(data.session_token){try{sessionStorage.setItem(ADMIN_SESSION_KEY,data.session_token)}catch{}}csrfToken=data.csrf_token||'';$("#admin-password").value='';hideLogin();showShell();playEntry();await bootAfterAuth();toast('Admin session established.','good')}catch(e){if(err){err.textContent=e.status===429?'Too many attempts. Try again later.':e.message;err.hidden=false}}finally{button.disabled=false}}
  $("#admin-login-form")?.addEventListener('submit',adminLogin);
  $("#toggle-admin-password")?.addEventListener('click',()=>{const input=$("#admin-password"),icon=$("#toggle-admin-password i");if(!input)return;input.type=input.type==='password'?'text':'password';if(icon)icon.setAttribute('data-lucide',input.type==='password'?'eye':'eye-off');requestIcons()});

  function renderUsers(){const q=$("#user-search")?.value.trim().toLowerCase()||'',f=$("#user-filter")?.value||'all';const rows=state.users.filter(u=>(!q||`${u.email} ${u.name}`.toLowerCase().includes(q))&&(f==='all'||(f==='monitoring'&&u.is_monitored)||(f==='paused'&&!u.is_monitored)||(f==='reauth'&&u.needs_reauth)));const body=$("#users-body");if(!rows.length){body.innerHTML='<tr><td colspan="7"><div class="empty">No matching accounts.</div></td></tr>';return}body.innerHTML=rows.map((u,i)=>`<tr style="animation-delay:${Math.min(i*28,210)}ms"><td><span class="user-email mono" title="${esc(u.email)}">${esc(u.email)}</span><div style="font-size:8px;color:#65718a;margin-top:4px">${esc(u.name||'No display name')}</div></td><td><span class="badge ${u.is_monitored?'good':'warn'}">${u.is_monitored?'MONITORING':'PAUSED'}</span></td><td><span class="badge ${u.needs_reauth?'warn':'good'}">${u.needs_reauth?'REAUTH':'HEALTHY'}</span></td><td>${fmtNum(u.stats?.processed)}</td><td>${fmtNum(u.stats?.actioned)}</td><td>${fmtNum(u.stats?.filtered)}</td><td><button class="ghost-btn" data-user="${encodeURIComponent(u.email)}">Open</button></td></tr>`).join('');$$('#users-body [data-user]').forEach(b=>b.onclick=()=>openUser(decodeURIComponent(b.dataset.user)))}
  async function loadUsers(){try{const d=await api('/api/admin/users?limit=500');state.users=d.users||[];renderUsers();setText('#users-count-note',`${fmtNum(d.total)} connected account${Number(d.total||0)===1?'':'s'}`)}catch(e){toast(e.message,'bad')}}
  $("#user-search")?.addEventListener('input',renderUsers);$("#user-filter")?.addEventListener('change',renderUsers);
  function detail(k,v,mono=false){return `<div class="detail"><small>${esc(k)}</small><strong class="${mono?'mono':''}">${esc(v??'—')}</strong></div>`}
  async function openUser(email){const m=$("#user-modal");m.classList.add('show');m.setAttribute('aria-hidden','false');setText('#modal-title',email);$("#modal-body").innerHTML='<div class="empty">Loading account details…</div>';try{const d=await api(`/api/admin/users/${encodeURIComponent(email)}`),a=d.account||{};$("#modal-body").innerHTML=`<div class="detail-grid">${detail('Email',a.email,true)}${detail('Name',a.name)}${detail('Connected',fmtDate(a.created_at))}${detail('Session',a.session_id?'Present':'—',true)}${detail('Monitoring',a.is_monitored?'Enabled':'Paused')}${detail('Reauth',a.needs_reauth?'Required':'Healthy')}${detail('Processed',fmtNum(d.stats?.processed))}${detail('Calendar actions',fmtNum(d.stats?.actioned))}${detail('Filtered',fmtNum(d.stats?.filtered))}${detail('Total',fmtNum(d.stats?.total))}${detail('Spam threshold',d.settings?.spam_threshold??'—')}${detail('Default timing',d.settings?.default_timing??'—')}</div><div class="section"><div class="panel-header"><div><span class="eyebrow">Processing history</span><h3>Recent activity</h3></div></div><div class="log-list">${(d.recent_actions||[]).map(x=>`<div class="log-row"><div class="log-time">${esc((x.processed_at||x.timestamp||'').replace('T',' ').slice(0,19))}</div><div><span class="badge ${x.status==='FAILED'?'bad':x.status==='ACTIONED'?'good':'info'}">${esc(x.status||'—')}</span></div><div class="log-msg"><strong>${esc(x.subject||'No subject')}</strong><br>${esc(x.summary||x.action_taken||'')}</div></div>`).join('')||'<div class="empty">No processing records.</div>'}</div></div><div class="section actions"><button class="${a.is_monitored?'danger-btn':'primary-btn'}" id="modal-toggle">${a.is_monitored?'Pause monitoring':'Enable monitoring'}</button>${a.needs_reauth?'<button class="ghost-btn" id="modal-clear-reauth">Clear reauth</button>':''}<button class="danger-btn" id="modal-delete">Disconnect account</button></div>`;$("#modal-toggle").onclick=async()=>{await userAction('toggle',a.email,{is_monitored:!a.is_monitored});closeUser()};$("#modal-delete").onclick=async()=>{if(confirm(`Disconnect ${a.email}? This removes its EMA connection/token record.`)){await userAction('delete',a.email);closeUser()}};if($("#modal-clear-reauth"))$("#modal-clear-reauth").onclick=async()=>{await userAction('clear-reauth',a.email);await openUser(a.email)}}catch(e){$("#modal-body").innerHTML=`<div class="empty">${esc(e.message)}</div>`}}
  function closeUser(){const m=$("#user-modal");m.classList.remove('show');m.setAttribute('aria-hidden','true')}
  $("#modal-close")?.addEventListener('click',closeUser);$("#user-modal")?.addEventListener('click',e=>{if(e.target.id==='user-modal')closeUser()});
  async function userAction(kind,email,extra={}){try{const path=kind==='toggle'?'/api/admin/users/toggle-monitoring':kind==='delete'?'/api/admin/users/delete':'/api/admin/users/clear-reauth';await api(path,{method:'POST',body:JSON.stringify({email,...extra})});toast(kind==='delete'?'Account disconnected':'Admin action completed','good');await loadUsers();await loadOverview()}catch(e){toast(e.message,'bad')}}

  async function loadAIStatus(){try{const d=await api('/api/admin/ai/status');setText('#ai-primary',d.primary_model||'—');setText('#ai-verifier',d.verifier?.enabled?(d.verifier.model||'Enabled'):'Disabled');setText('#ai-verifier-sub',d.verifier?.enabled?`Reasoning: ${d.verifier?.reasoning_effort||'—'}`:'Verification disabled');const fallbacks=Array.isArray(d.fallback_models)?d.fallback_models:[];setText('#ai-fallbacks',fmtNum(fallbacks.length));const b=$("#ai-status-badge");if(b){b.textContent=d.verifier?.enabled?'ONLINE':'PRIMARY ONLY';b.className=`status-badge ${d.verifier?.enabled?'good':'warn'}`}}catch(e){const b=$("#ai-status-badge");if(b){b.textContent='UNAVAILABLE';b.className='status-badge bad'}}}
  async function loadOverview(){try{const d=await api('/api/admin/overview');animateCount($("#users-total"),d.users);animateCount($("#active-total"),d.active_users);animateCount($("#monitoring-total"),d.monitoring);animateCount($("#reauth-total"),d.reauth_required);animateCount($("#processed-total"),d.processing?.processed);animateCount($("#error-total"),d.errors?.error_count);setText('#ram-value',`${d.health?.memory_mb??0} MB`);setText('#ram-state',d.health?.is_lean?'Lean target':'Above target');setText('#worker-value',d.worker?.is_running?'LIVE':'IDLE');setText('#worker-sub',d.worker?.last_sync?`Last sync ${fmtDate(d.worker.last_sync)}`:'No sync yet');setText('#inspect-value',d.inspect?.is_running?'RUNNING':'READY');setText('#inspect-sub',d.inspect?.status_message||'');setText('#server-uptime',d.health?.uptime||'—');setText('#server-time',fmtDate(d.health?.server_time));setText('#users-count-note',`${fmtNum(d.accounts)} connected account${Number(d.accounts||0)===1?'':'s'}`);const healthy=d.health?.status==='ok';setText('#hero-health-title',healthy?'Service healthy':'Attention required');setText('#hero-health-copy',healthy?`${d.health?.memory_mb??0} MB process RSS · worker ${d.worker?.is_running?'active':'idle'}`:'Health telemetry unavailable');const dot=$("#hero-health-dot");if(dot)dot.style.background=healthy?'#34d399':'#fb7185'}catch(e){setText('#hero-health-title','Telemetry unavailable');setText('#hero-health-copy',e.message)}}
  async function loadErrors(){try{const d=await api('/api/admin/errors?limit=120');state.errors=d.errors||[];$("#error-grid").innerHTML=(d.categories||[]).map(c=>`<div class="glass-panel error-card"><span class="eyebrow">${esc(c.name)}</span><div class="count">${fmtNum(c.count)}</div><div class="hint">${esc(c.description)}</div></div>`).join('');$("#error-log").innerHTML=state.errors.length?state.errors.map(x=>`<div class="log-row"><div class="log-time">${esc(x.time)}</div><div><span class="badge ${x.level==='ERROR'?'bad':'warn'}">${esc(x.level)}</span></div><div class="log-msg"><strong>${esc(x.category)}</strong><br>${esc(x.message)}</div></div>`).join(''):'<div class="empty">No warnings or errors in the current ledger.</div>'}catch(e){toast(e.message,'bad')}}
  async function loadLogs(){try{const d=await api('/api/admin/logs?limit=180');state.logs=d.lines||[];$("#full-log").innerHTML=state.logs.length?state.logs.map(x=>`<div class="log-row"><div class="log-time">${esc(x.time)}</div><div><span class="badge ${x.level==='ERROR'?'bad':x.level==='WARNING'?'warn':x.level==='SUCCESS'?'good':'info'}">${esc(x.level)}</span></div><div class="log-msg">${esc(x.message)}</div></div>`).join(''):'<div class="empty">No log lines available.</div>'}catch(e){toast(e.message,'bad')}}
  async function loadActivity(){try{const d=await api('/api/admin/activity?limit=100');$("#activity-log").innerHTML=(d.activity||[]).map(x=>`<div class="log-row"><div class="log-time">${esc((x.processed_at||'').replace('T',' ').slice(0,19))}</div><div><span class="badge ${x.status==='FAILED'?'bad':x.status==='ACTIONED'?'good':'info'}">${esc(x.status||'—')}</span></div><div class="log-msg"><strong>${esc(x.email)}</strong><br>${esc(x.subject||'No subject')}<br><span style="color:#6b778d">${esc(x.category||'')} · ${esc(x.summary||'')}</span></div></div>`).join('')||'<div class="empty">No activity available.</div>'}catch(e){toast(e.message,'bad')}}
  async function control(endpoint,label){try{await api(endpoint,{method:'POST'});toast(label,'good');await loadOverview()}catch(e){toast(e.message,'bad')}}
  $("#worker-start").onclick=()=>control('/api/admin/worker/start','Worker start requested');$("#worker-stop").onclick=()=>control('/api/admin/worker/stop','Worker stop requested');$("#trim").onclick=()=>control('/api/admin/memory/trim','Memory cleanup requested');
  $("#inspect-start").onclick=async()=>{const n=Math.max(10,Math.min(40,Number($("#inspect-count").value||20)));try{await api('/api/admin/inspect/start',{method:'POST',body:JSON.stringify({count:n})});toast(`Inspection started for up to ${n} emails`,'good');await loadOverview()}catch(e){toast(e.message,'bad')}};$("#inspect-stop").onclick=()=>control('/api/admin/inspect/stop','Inspection stop requested');
  async function refreshAll(){await Promise.all([loadOverview(),loadAIStatus(),loadUsers(),loadErrors(),loadActivity()]);if(state.currentSection==='logs')await loadLogs();requestIcons()}
  $("#refresh").onclick=refreshAll;$("#refresh-errors").onclick=loadErrors;
  $("#export-users").onclick=()=>{const payload=JSON.stringify({exported_at:new Date().toISOString(),users:state.users.map(u=>({email:u.email,name:u.name,is_monitored:u.is_monitored,needs_reauth:u.needs_reauth,stats:u.stats}))},null,2);const blob=new Blob([payload],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='ema-admin-users.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
  $("#signout").onclick=async()=>{try{await api('/api/admin/auth/logout',{method:'POST'})}catch{}clearAdminSession();csrfToken='';if(state.poll)clearInterval(state.poll);closeEntry();hideShell();showLogin('Signed out.');toast('Admin session ended.','info')};

  function openCommands(){const modal=$("#command-modal");modal.hidden=false;renderCommands('');setTimeout(()=>$("#command-search-input")?.focus(),50)}
  function closeCommands(){const m=$("#command-modal");if(m)m.hidden=true}
  function renderCommands(query){const list=$("#command-list"),q=(query||'').trim().toLowerCase();const data=commands.filter(c=>`${c[1]} ${c[2]}`.toLowerCase().includes(q));list.innerHTML=data.map((c,i)=>`<button class="command-item" data-command="${c[0]}" aria-current="${i===0?'true':'false'}"><i data-lucide="${c[3]}"></i><span><strong>${esc(c[1])}</strong><small>${esc(c[2])}</small></span><kbd>${esc(c[4])}</kbd></button>`).join('');$$('.command-item',list).forEach(b=>b.onclick=()=>runCommand(b.dataset.command));requestIcons()}
  function runCommand(cmd){closeCommands();if(pageMeta[cmd])return setView(cmd);if(cmd==='refresh')return refreshAll();if(cmd==='worker-start')return control('/api/admin/worker/start','Worker start requested');if(cmd==='worker-stop')return control('/api/admin/worker/stop','Worker stop requested');if(cmd==='inspect-start')return $("#inspect-start").click()}
  $("#admin-command")?.addEventListener('click',openCommands);$("#command-search-input")?.addEventListener('input',e=>renderCommands(e.target.value));$("#command-modal")?.addEventListener('click',e=>{if(e.target.id==='command-modal')closeCommands()});
  addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openCommands()}if(e.key==='Escape')closeCommands();const n=Number(e.key);if(n>=1&&n<=5&&!e.ctrlKey&&!e.metaKey&&document.activeElement?.tagName!=='INPUT')setView(['overview','users','activity','errors','logs'][n-1])});

  async function bootAfterAuth(){const view=location.hash.slice(1)||'overview';setView(view);await refreshAll();if(state.poll)clearInterval(state.poll);state.poll=setInterval(async()=>{if(document.hidden)return;await loadOverview();if(state.currentSection==='overview')await loadAIStatus();if(state.currentSection==='users')await loadUsers();if(state.currentSection==='errors')await loadErrors();if(state.currentSection==='activity')await loadActivity();if(state.currentSection==='logs')await loadLogs()},15000)}
  async function boot(){document.documentElement.dataset.device=innerWidth<700?'mobile':innerWidth<1100?'tablet':'desktop';addEventListener('resize',()=>{clearTimeout(boot.r);boot.r=setTimeout(()=>document.documentElement.dataset.device=innerWidth<700?'mobile':innerWidth<1100?'tablet':'desktop',120)},{passive:true});requestIcons();const ok=await verifyAdmin();if(ok)await bootAfterAuth()}
  window.EMAAdmin={switchView:setView,openUser};
  document.addEventListener('DOMContentLoaded',boot);
})();
