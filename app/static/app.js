/* DarkWatch: single-file offline-friendly dashboard. No external JS requests. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  let csrf = '', page = 'overview', filter = 'all', state = null, isPolling = false;
  let aggHistory = [], currentTerm = null, currentSocket = null, chartResizeScheduled = false;
  let selectedIfaces={}; try{selectedIfaces=JSON.parse(localStorage.getItem('dw_ifaces')||'{}')}catch(e){}
  const names = {overview:'داشبورد', servers:'سرورها', network:'ترافیک زنده',settings:'راهنمای اتصال'};
  const esc = x => String(x == null ? '' : x).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function $text(selector, value){ const e=$(selector); if(e) e.textContent=value; }
  function popup(message){const e=$('#toast'); e.textContent=message;e.classList.add('show');clearTimeout(e.timer);e.timer=setTimeout(()=>e.classList.remove('show'),3500)}
  function bitsPerSecond(bps){ if(bps>=1e9) return [(bps/1e9).toFixed(2),'Gbps']; if(bps>=1e6)return [(bps/1e6).toFixed(2),'Mbps'];if(bps>=1000)return [(bps/1e3).toFixed(1),'Kbps'];return [Math.round(bps||0).toString(),'bps'] }
  const bitFmt = n => bitsPerSecond(n).join(' ');
  function byteFmt(n){if(n==null)return'—'; const units=['B','KB','MB','GB','TB','PB']; const div=[1,1024,1024**2,1024**3,1024**4,1024**5]; for(let i=div.length-1;i>=0;i--)if(n>=div[i])return (n/div[i]).toFixed(i>1?1:0)+' '+units[i]; return n+' B'}
  const pct = n => n == null?'—':n.toFixed(1)+'%';
  function uptime(s){if(s==null)return'—';const d=Math.floor(s/86400),h=Math.floor(s%86400/3600);return `${d}d ${h}h uptime`}
  function showLogin(){ $('#login').classList.remove('hidden');$('#application').classList.add('hidden'); }
  function showApp(){ $('#login').classList.add('hidden');$('#application').classList.remove('hidden'); }
  async function api(url, options={}){
    const headers = {...options.headers};
    if(options.body){headers['Content-Type']='application/json'; headers['X-CSRF-Token']=csrf;}
    if(options.method==='DELETE') headers['X-CSRF-Token']=csrf;
    const response=await fetch(url,{credentials:'same-origin',...options,headers});
    let data=null;try{data=await response.json()}catch(e){}
    if(response.status===401){showLogin();throw new Error(data?.detail||'نیاز به ورود مجدد')}
    if(!response.ok){ let detail=data?.detail; if(Array.isArray(detail)) detail='اطلاعات ورودی نامعتبر است';throw new Error(detail||`HTTP ${response.status}`) }
    return data;
  }
  async function bootstrap(){
    try{const data=await api('/api/me');csrf=data.csrf;showApp();await poll();}
    catch(e){showLogin()}
  }
  $('#login-form').addEventListener('submit',async e=>{
    e.preventDefault();$text('#login-error','');
    const button=e.target.querySelector('button');button.disabled=true;
    try{
      const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({username:$('#login-user').value,password:$('#login-pass').value})});
      const d=await response.json();if(!response.ok)throw new Error(d.detail||'ورود ناموفق');
      csrf=d.csrf;$('#login-pass').value='';showApp();await poll();
    }catch(err){$text('#login-error',err.message)}finally{button.disabled=false}
  });
  $('#logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'})}catch(e){}csrf='';showLogin()});
  function goto(next){page=next;$$('.page').forEach(e=>e.classList.toggle('active',e.id===`page-${next}`));$$('.nav-item').forEach(e=>e.classList.toggle('active',e.dataset.page===next));$text('#breadcrumb',names[next]);if(state)render(state);requestAnimationFrame(drawCharts)}
  $$('.nav-item').forEach(b=>b.addEventListener('click',()=>goto(b.dataset.page)));
  $$('.filter').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;$$('.filter').forEach(c=>c.classList.toggle('active',b===c));if(state)renderCards(state.servers)}));
  $('#display-mode').addEventListener('click',()=>{document.body.classList.toggle('display-mode');setTimeout(drawCharts,80)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('display-mode'))document.body.classList.remove('display-mode')});
  $$('.add-server-btn').forEach(b=>b.addEventListener('click',()=>{ $('#server-form').reset();$('#form-error').textContent='';$('#add-modal').showModal()}));
  $$('.modal-close').forEach(b=>b.addEventListener('click',()=>$('#add-modal').close()));
  $('#server-form').addEventListener('submit',async e=>{
    e.preventDefault();const form=e.target;const d=Object.fromEntries(new FormData(form));d.port=Number(d.port);$text('#form-error','');
    try{await api('/api/servers',{method:'POST',body:JSON.stringify(d)});$('#add-modal').close();popup('سرور ثبت شد؛ اتصال SSH در حال بررسی است');await poll()}
    catch(err){$text('#form-error',err.message)}
  });
  $('#search-server').addEventListener('input',()=>{if(state)renderTable(state.servers)});
  $$('[data-copy]').forEach(e=>e.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(e.dataset.copy);popup('دستور کپی شد')}catch(err){popup('مرورگر اجازه کپی ندارد')}}));
  function detailsButtons(s, local=false){return `<div class="card-buttons">${local?'':`<button class="sm-btn ssh-button" data-ssh="${s.id}">⌘ SSH</button><button class="sm-btn remove" data-delete="${s.id}" title="حذف از فهرست (بدون تغییر در سرور)">✕</button>`}</div>`}
  function statBar(label,value,color){const v=Math.min(100,Math.max(0,value||0));return `<div><div class="stat-label">${label}</div><div class="stat-value">${pct(value)}</div><div class="stat-track"><div class="${color}" style="width:${v}%"></div></div></div>`}
  function chosenIface(s){return s.interfaces?.[selectedIfaces[s.id]] ? selectedIfaces[s.id] : s.primary_interface}
  function effectiveNet(s){return s.interfaces?.[chosenIface(s)] || {rx_bps:s.rx_bps||0,tx_bps:s.tx_bps||0,rx_bytes:s.rx_total||0,tx_bytes:s.tx_total||0}}
  function card(s){
    const online=!!s.online, net=effectiveNet(s), iface=chosenIface(s);
    const ifaceSelect=Object.keys(s.interfaces||{}).length>1?`<select aria-label="انتخاب کارت شبکه" class="iface-select" data-iface="${s.id}">${Object.keys(s.interfaces).map(name=>`<option value="${esc(name)}" ${name===iface?'selected':''}>${esc(name)}</option>`).join('')}</select>`:`<span>${esc(iface||'—')}</span>`;
    return `<article class="server-card ${online?'':'offline'}">
      <div class="server-top"><div class="server-identity"><div class="server-avatar">▣</div><div style="min-width:0"><div class="server-title" title="${esc(s.name)}">${esc(s.name)}</div><div class="server-ip">${esc(s.host)}</div></div></div><span class="online-chip ${online?'':'down'}">● ${online?'ONLINE':'OFFLINE'}</span></div>
      ${online?`<div class="server-divider"></div><div class="server-stats">${statBar('CPU',s.cpu,'')}${statBar('RAM',s.memory_percent,'purple')}${statBar('DISK',s.disk_percent,'green')}</div>
      <div class="network-values"><div class="network-val"><small>↓ RX / DOWNLOAD</small><strong class="cyan">${bitFmt(net.rx_bps)}</strong></div><div class="network-val"><small>↑ TX / UPLOAD</small><strong class="violet">${bitFmt(net.tx_bps)}</strong></div></div>
      <div class="iface-toolbar">INTERFACE ${ifaceSelect}<span>RX ${byteFmt(net.rx_bytes)} · TX ${byteFmt(net.tx_bytes)}</span></div>
      <canvas class="server-spark" data-spark="${s.id}"></canvas>`:`<div class="server-error"><span>⌁</span>${esc(s.error||'در حال اتصال به سرور...')}</div>`}
      <div class="card-footer"><span class="uptime">${online?uptime(s.uptime):esc(s.group||'DISCONNECTED')}</span>${detailsButtons(s,s.id===0)}</div></article>`;
  }
  function renderCards(servers){
    const items=servers.filter(s=>filter==='all'||(filter==='online'?s.online:!s.online));
    $('#server-cards').innerHTML=items.map(card).join('')||'<div class="muted">سروری در این وضعیت پیدا نشد.</div>';
    attachCardActions();
    for(const s of items){let el=$(`[data-spark="${s.id}"]`); if(el) drawMini(el,sparkFor(s))}
  }
  function sparkFor(s){const iface=chosenIface(s);return (s.history||[]).map(p=>p.ifaces?.[iface]?.rx??p.rx??0)}
  function attachCardActions(){
    $$('.iface-select').forEach(e=>e.addEventListener('change',()=>{selectedIfaces[e.dataset.iface]=e.value;localStorage.setItem('dw_ifaces',JSON.stringify(selectedIfaces));if(state)render(state)}));
    $$('.ssh-button').forEach(b=>b.addEventListener('click',()=>openTerminal(Number(b.dataset.ssh))));
    $$('[data-delete]').forEach(b=>b.addEventListener('click',async()=>{
      const sid=Number(b.dataset.delete),server=(state?.servers||[]).find(s=>s.id===sid);
      if(!window.confirm(`«${server?.name||'سرور'}» از پنل حذف شود؟ خود سرور یا فایل‌های آن تغییری نمی‌کند.`))return;
      try{await api('/api/servers/'+sid,{method:'DELETE'});popup('سرور از پنل حذف شد');await poll()}catch(err){popup(err.message)}
    }));
  }
  function renderTable(servers){
    const search=$('#search-server').value.trim().toLowerCase();
    const items=servers.filter(s=>[s.name,s.host,s.group].some(x=>String(x||'').toLowerCase().includes(search)));
    $text('#table-count',`${items.length} سرور`);
    $('#server-table').innerHTML=items.map(s=>`<tr><td><span class="table-name">${esc(s.name)}</span><div class="table-ip">${esc(s.host)}${s.id===0?'':' · '+esc(s.group)}</div></td><td><span class="table-status ${s.online?'':'down'}">● ${s.online?'آنلاین':'آفلاین'}</span></td>
      <td class="mono">${pct(s.cpu)}</td><td class="mono">${pct(s.memory_percent)}</td><td class="mono"><span class="cyan">${bitFmt(effectiveNet(s).rx_bps)}</span> / <span class="violet">${bitFmt(effectiveNet(s).tx_bps)}</span></td><td><div class="table-actions">${detailsButtons(s,s.id===0)}</div></td></tr>`).join('');
    attachCardActions();
  }
  function renderTraffic(servers){
    $('#traffic-rows').innerHTML=servers.map(s=>`<div class="traffic-row"><span>${esc(s.name)}<br><small class="muted">${esc(chosenIface(s)||'—')}</small></span><span class="mono cyan">↓ ${s.online?bitFmt(effectiveNet(s).rx_bps):'—'}</span><span class="mono violet">↑ ${s.online?bitFmt(effectiveNet(s).tx_bps):'—'}</span><span class="muted">${s.online?'● ONLINE':'○ OFFLINE'}</span></div>`).join('');
  }
  function render(state){
    const ss=state.servers,sm=state.summary;
    $text('#metric-total',sm.total);$text('#metric-online',sm.online+' آنلاین');$text('#server-count',sm.total);$text('#metric-offline',sm.offline+' آفلاین');
    $text('#metric-health',sm.total?Math.round(sm.online*100/sm.total):0);
    const [rx,rxUnit]=bitsPerSecond(sm.rx_bps),[tx,txUnit]=bitsPerSecond(sm.tx_bps);
    $text('#metric-rx',rx);$text('#metric-tx',tx);$text('#metric-rx-unit',rxUnit);$text('#metric-tx-unit',txUnit);
    $text('#net-rx',bitFmt(sm.rx_bps));$text('#net-tx',bitFmt(sm.tx_bps));$text('#net-online',`${sm.online} / ${sm.total}`);
    $text('#online-indicator',`${sm.online} / ${sm.total} ONLINE`);$text('#refresh-interval',state.poll_seconds);
    $text('#sync-indicator',new Date(state.now*1000).toLocaleTimeString('en-GB'));
    renderCards(ss);renderTable(ss);renderTraffic(ss);
    $('#activity-list').innerHTML=ss.slice(0,7).map(s=>`<div class="activity-item"><div class="activity-icon ${s.online?'':'down'}">${s.online?'✓':'!'}</div><div><b>${esc(s.name)}</b><small>${s.online?'اتصال برقرار است · '+(s.cpu==null?'—':pct(s.cpu))+' CPU':'اتصال برقرار نیست'}</small></div></div>`).join('');
    aggHistory.push({rx:sm.rx_bps,tx:sm.tx_bps});if(aggHistory.length>120)aggHistory.shift();drawCharts();
  }
  function setupCanvas(canvas){
    if(!canvas)return null;
    const rect=canvas.getBoundingClientRect();if(rect.width<10)return null;
    const dpr=Math.min(devicePixelRatio||1,2);
    canvas.width=Math.floor(rect.width*dpr);canvas.height=Math.floor(rect.height*dpr);
    const c=canvas.getContext('2d');c.scale(dpr,dpr);return {c,w:rect.width,h:rect.height};
  }
  function plot(canvas,points,mini=false){
    const setup=setupCanvas(canvas);if(!setup)return;const {c,w,h}=setup;
    const pad=mini?2:16,usableW=w-2*pad,usableH=h-(mini?4:35);
    if(!mini){
      c.strokeStyle='#213744';c.lineWidth=1;c.setLineDash([3,6]);
      for(let i=0;i<=4;i++){let y=pad+i*(usableH/4);c.beginPath();c.moveTo(0,y);c.lineTo(w,y);c.stroke()}
      c.setLineDash([]);
    }
    const max=Math.max(1,...points.flatMap(p=>[p.rx||0,p.tx||0]))*1.13;
    for(let series of (mini?['rx']:['rx','tx'])){
      const values=points.map(x=>Number(x[series])||0);
      const color=series==='rx'?'#39e5d4':'#9a7af5';
      c.beginPath();c.lineWidth=mini?1.7:2;c.strokeStyle=color;c.lineJoin='round';c.lineCap='round';
      for(let i=0;i<values.length;i++){
        const x=pad+(values.length===1?0:i/(Math.max(1,values.length-1)))*usableW;
        const y=pad+usableH*(1-values[i]/max);
        if(!i)c.moveTo(x,y);else c.lineTo(x,y);
      }
      c.stroke();
      if(!mini&&values.length>1){
        c.lineTo(pad+usableW,pad+usableH);c.lineTo(pad,pad+usableH);c.closePath();
        let gradient=c.createLinearGradient(0,0,0,h);gradient.addColorStop(0,series==='rx'?'#39e5d420':'#9a7af51c');gradient.addColorStop(1,'#07121a00');c.fillStyle=gradient;c.fill();
      }
    }
    if(!mini){c.textAlign='left';c.fillStyle='#728c99';c.font='10px monospace';c.fillText(bitFmt(max),10,h-7)}
  }
  function drawMini(canvas,history){plot(canvas,(history||[]).map(rx=>({rx:rx})),true)}
  function drawCharts(){plot($('#overview-chart'),aggHistory);plot($('#network-chart'),aggHistory)}
  window.addEventListener('resize',()=>{if(chartResizeScheduled)return;chartResizeScheduled=true;requestAnimationFrame(()=>{chartResizeScheduled=false;drawCharts();if(state)for(const s of state.servers){const e=$(`[data-spark="${s.id}"]`);if(e)drawMini(e,sparkFor(s))}if(currentTerm?.fit)currentTerm.fit()})});
  async function poll(){
    if(isPolling||$('#application').classList.contains('hidden'))return;
    isPolling=true;
    try{const result=await api('/api/state');state=result;render(result)}catch(err){$text('#sync-indicator','DISCONNECTED')}finally{isPolling=false}
  }
  setInterval(poll,3000);
  setInterval(()=>$text('#current-clock',new Date().toLocaleTimeString('en-GB',{hour12:false})),1000);
  function openTerminal(id){
    const s=(state?.servers||[]).find(s=>s.id===id);if(!s||id===0)return;
    $('#terminal-title').textContent=`${s.name}  ·  ${s.host}`;
    const dialog=$('#terminal-modal');dialog.showModal();
    const holder=$('#terminal-container');holder.innerHTML='';
    if(typeof Terminal==='undefined') {holder.textContent='ماژول ترمینال پیدا نشد';return}
    if(window.fit && Terminal.applyAddon) Terminal.applyAddon(window.fit);
    const term=new Terminal({cursorBlink:true,fontSize:13,fontFamily:'Consolas,Monaco,monospace',theme:{background:'#060e16',foreground:'#d5e7ed',cursor:'#39e5d4',selection:'#39e5d444'},scrollback:3000});
    currentTerm=term;term.open(holder);if(term.fit)term.fit();term.focus();
    const wsProto=location.protocol==='https:'?'wss:':'ws:';
    const sock=new WebSocket(`${wsProto}//${location.host}/ws/ssh/${id}`);currentSocket=sock;
    term.write('\r\n\x1b[36mDARKWATCH // SECURE SSH SESSION\x1b[0m\r\nConnecting...\r\n');
    sock.onopen=()=>{sendResize();};
    sock.onmessage=e=>{let d;try{d=JSON.parse(e.data)}catch(err){return}if(d.type==='output')term.write(d.data);else if(d.type==='error')term.writeln('\r\n'+d.message)};
    sock.onclose=()=>term.write('\r\n\x1b[33m[Session disconnected]\x1b[0m\r\n');
    sock.onerror=()=>term.write('\r\n\x1b[31m[SSH connection error]\x1b[0m\r\n');
    term.on('data',data=>{if(sock.readyState===WebSocket.OPEN)sock.send(JSON.stringify({type:'input',data}))});
    term.on('resize',size=>{if(sock.readyState===WebSocket.OPEN)sock.send(JSON.stringify({type:'resize',cols:size.cols,rows:size.rows}))});
    function sendResize(){if(sock.readyState===WebSocket.OPEN)sock.send(JSON.stringify({type:'resize',cols:term.cols,rows:term.rows}))}
  }
  function closeTerminal(){if(currentSocket){currentSocket.close();currentSocket=null}if(currentTerm){currentTerm.destroy();currentTerm=null}$('#terminal-modal').close()}
  $('#terminal-close').addEventListener('click',closeTerminal);
  $('#terminal-modal').addEventListener('cancel',e=>{e.preventDefault();closeTerminal()});
  bootstrap();
})();
