// Browser DOM smoke-test for server add, update-password and delete workflows.
// Run: npm install --no-save jsdom@24.1.3 && node tests/ui_smoke.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root,'app/static/index.html'),'utf8');
const source = fs.readFileSync(path.join(root,'app/static/app.js'),'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push(String(e.message)));

const dom = new JSDOM(html, {
  url:'https://127.0.0.1:6600/',
  runScripts:'outside-only',
  pretendToBeVisual:true,
  virtualConsole:vc,
});
const {window:w} = dom;
const doc=w.document;
w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','')};
w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open')};
w.requestAnimationFrame=(f)=>{f();return 1;};
w.navigator.clipboard={writeText:async()=>{}};
let authorized=false, confirmations=0, deletes=0, registrations=0, passwords=0;
let servers=[
  {id:0,name:'HUB',host:'127.0.0.1',port:0,group:'مرکزی',online:true,cpu:21,
   memory_percent:30,disk_percent:20,rx_bps:1000,tx_bps:1000,history:[]},
  {id:1,name:'Iran 1',host:'192.0.2.11',port:22,group:'ایران',ssh_user:'root',
   online:true,cpu:25,memory_percent:45,disk_percent:50,rx_bps:8000,tx_bps:7000,history:[]},
  {id:2,name:'Iran 2',host:'192.0.2.12',port:22,group:'ایران',ssh_user:'root',
   online:false,error:'off',history:[]},
];
const response=(status,data)=>({status,ok:status>=200&&status<300,json:async()=>data});
w.confirm=()=>{confirmations++;return true};
w.fetch=async(url,opt={})=>{
  const name=new URL(url,w.location.href).pathname,method=opt.method||'GET';
  const payload=opt.body?JSON.parse(opt.body):{};
  if(name==='/api/me')return authorized?response(200,{username:'admin',csrf:'test-csrf'}):response(401,{detail:'نیاز به ورود داری'});
  if(name==='/api/login'){
    authorized=true;return response(200,{csrf:'test-csrf',username:'admin'});
  }
  if(!authorized)return response(401,{detail:'نیاز به ورود'});
  if(name==='/api/state'){
    const online=servers.filter(s=>s.online);
    return response(200,{servers:servers.map(s=>({...s})),
      summary:{total:servers.length,online:online.length,offline:servers.length-online.length,
        rx_bps:online.reduce((v,s)=>v+(s.rx_bps||0),0),
        tx_bps:online.reduce((v,s)=>v+(s.tx_bps||0),0)},poll_seconds:3,now:Date.now()/1000});
  }
  if(method==='POST'&&name==='/api/servers'){
    registrations++;
    assert(!payload.trust_fingerprint, 'One-step onboarding must not ask for a fingerprint');
    const next=Math.max(...servers.map(s=>s.id))+1;
    servers.push({id:next,name:payload.name,host:payload.host,port:payload.port,ssh_user:payload.ssh_user,
      group:payload.group_name,online:true,cpu:0,memory_percent:0,disk_percent:0,rx_bps:0,tx_bps:0,history:[]});
    return response(201,{id:next,ok:true});
  }
  if(method==='PUT'&&name.endsWith('/ssh-password')){
    passwords++;return response(200,{ok:true});
  }
  if(method==='DELETE'&&name.startsWith('/api/servers/')){
    deletes++;servers=servers.filter(s=>s.id!==Number(name.split('/').pop()));
    return response(200,{ok:true});
  }
  return response(404,{detail:'Not Found'});
};
const click=selector=>{const el=doc.querySelector(selector);assert(el,'Missing element '+selector);el.click()};
const wait=async(test,msg)=>{
  for(let i=0;i<80;i++){
    if(test())return;
    await new Promise(resolve=>setTimeout(resolve,30));
  }
  throw new Error('Timed out: '+msg);
};
async function run(){
  try{
    w.eval(source);
    assert(!doc.querySelector('#login').classList.contains('hidden'),'Login should be shown');
    assert(!errors.length,'Initial JS errors: '+errors);
    await new Promise(resolve=>setTimeout(resolve,80));
    doc.querySelector('#login-pass').value='test-password';
    doc.querySelector('#login-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
    await wait(()=>!doc.querySelector('#application').classList.contains('hidden'),'login dashboard: '+errors.join(', ')+'; html='+doc.querySelector('#login-error').textContent);
    assert(!errors.length,'Dashboard errors: '+errors);
    assert.equal(doc.querySelectorAll('#server-cards .server-card').length,3);
    assert.equal(doc.querySelectorAll('.nav-mobile-copy').length,4,
                 'Mobile dock should provide labels for all four sections');
    click('[data-page="network"]');
    assert(doc.querySelector('#page-network').classList.contains('active'),'Network tab should open');
    click('[data-page="settings"]');
    assert(doc.querySelector('#page-settings').classList.contains('active'),'Help tab should open');
    click('[data-page="servers"]');
    assert(doc.querySelector('#page-servers').classList.contains('active'),'Servers tab should open');
    click('[data-page="overview"]');
    assert(doc.querySelector('#page-overview').classList.contains('active'),'Overview tab should open');

    click('.add-server-btn');
    assert(doc.querySelector('#add-modal').hasAttribute('open'),'Add modal must open');
    const form=doc.querySelector('#server-form');
    const passField=form.elements.namedItem('ssh_password');
    assert(passField.required,'Root password is mandatory in the Add Server form');
    assert.equal(doc.querySelector('#ssh-password-label').textContent,'رمز عبور root سرور');
    form.elements.namedItem('ssh_user').value='operator';
    form.elements.namedItem('ssh_user').dispatchEvent(new w.Event('input',{bubbles:true}));
    assert(doc.querySelector('#ssh-password-label').textContent.includes('operator'),'Non-root SSH user label should update');
    form.elements.namedItem('ssh_user').value='root';
    form.elements.namedItem('ssh_user').dispatchEvent(new w.Event('input',{bubbles:true}));
    assert.equal(doc.querySelector('#ssh-password-label').textContent,'رمز عبور root سرور');
    form.elements.namedItem('name').value='Iran 3';
    form.elements.namedItem('host').value='192.0.2.13';
    form.elements.namedItem('ssh_user').value='root';
    form.elements.namedItem('ssh_password').value='testing-password';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
    await wait(()=>registrations===1&&!doc.querySelector('#add-modal').hasAttribute('open'),'add server via password');
    assert.equal(confirmations,0,'No fingerprint confirmation in one-step onboarding');
    await wait(()=>doc.querySelectorAll('#server-cards .server-card').length===4,'New server rendered');
    assert.equal(form.elements.namedItem('ssh_password').value,'','Password cleared after save');

    click('#server-cards [data-password="1"]');
    assert(doc.querySelector('#add-modal').hasAttribute('open'),'SSH password editing modal must open');
    form.elements.namedItem('ssh_password').value='new-ssh-password';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
    await wait(()=>passwords===1&&!doc.querySelector('#add-modal').hasAttribute('open'),'update ssh password');

    const starting=confirmations;
    click('#server-cards [data-delete="1"]');
    await wait(()=>deletes===1,'delete via card');
    assert.equal(confirmations-starting,1,'Delete must have one confirmation');
    await wait(()=>doc.querySelectorAll('#server-cards .server-card').length===3,'Card removed');
    assert.equal(doc.querySelector('#server-cards [data-delete="1"]'),null,'Deleted card must not reappear');

    click('#server-table [data-delete="2"]');
    await wait(()=>deletes===2,'delete via table');
    await wait(()=>doc.querySelector('#server-table [data-delete="2"]')===null,'Table row removed');
    assert(!errors.length,'Browser runtime errors: '+errors);
    console.log('PASS: login, render, add server with SSH password without extra prompts, update SSH password, delete from card/table once, refresh');
  }finally{
    dom.window.close();
  }
}
run().catch(err=>{console.error(err);process.exitCode=1});