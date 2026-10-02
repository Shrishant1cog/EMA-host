#!/usr/bin/env python3
"""Interaction and auth-gating QA using deterministic network stubs."""
from __future__ import annotations
import json,re
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
FAIL=[]; RES={}

def common_html(path:Path, injected:str)->str:
    s=path.read_text(encoding='utf-8')
    s=s.replace('<script src="https://cdn.tailwindcss.com"></script>','<script>window.tailwind={config:{}};</script>')
    s=s.replace('<script defer src="https://cdn.tailwindcss.com"></script>','<script>window.tailwind={config:{}};</script>')
    s=s.replace('<script src="https://unpkg.com/lucide@latest"></script>','<script>window.lucide={createIcons(){}};</script>')
    s=s.replace('<script defer src="https://unpkg.com/lucide@latest"></script>','<script>window.lucide={createIcons(){}};</script>')
    s=re.sub(r'<link[^>]+fonts\.googleapis\.com[^>]*>','',s)
    if path.name=='index.html':
        s=s.replace('<link rel="stylesheet" href="/ux-pro.css">','<style>'+ (ROOT/'frontend/ux-pro.css').read_text()+'</style>')
        s=s.replace('<script src="/ux-pro.js"></script>','<script>'+ (ROOT/'frontend/ux-pro.js').read_text()+'</script>')
    if path.parts[-2:] == ('admin','index.html'):
        s=s.replace('<link rel="stylesheet" href="/admin/admin.css">','<style>'+ (ROOT/'frontend/admin/admin.css').read_text()+'</style>')
        s=s.replace('<script src="/admin/admin.js"></script>','<script>'+ (ROOT/'frontend/admin/admin.js').read_text()+'</script>')
    return '<style>.hidden{display:none!important}</style>'+injected+s

user_stub='''<script>
(function(){const make=()=>({_m:new Map(),getItem(k){return this._m.get(k)||null},setItem(k,v){this._m.set(k,String(v))},removeItem(k){this._m.delete(k)},clear(){this._m.clear()}});Object.defineProperty(window,'localStorage',{configurable:true,value:make()});Object.defineProperty(window,'sessionStorage',{configurable:true,value:make()});})();
const j=o=>new Response(JSON.stringify(o),{status:200,headers:{'content-type':'application/json'}});
window.fetch=async(u,i={})=>{u=String(u);if(u.includes('/api/user'))return j({authenticated:true,name:'QA User',total_accounts:1,monitored_accounts:1,accounts:[{email:'qa@example.com',name:'QA User',is_monitored:true}]});if(u.includes('/api/stats'))return j({summary:{total:1,actioned:1,filtered:0},worker:{is_running:false,status:'Idle'}});if(u.includes('/api/accounts'))return j({accounts:[{email:'qa@example.com',name:'QA User',is_monitored:true}]});if(u.includes('/api/emails'))return j({total:1,emails:[]});if(u.includes('/api/calendar/upcoming'))return j({events:[]});if(u.includes('/api/settings')&&(!i.method||i.method==='GET'))return j({theme_mode:'dark',only_remind_with_files:false,design_events_enabled:false,spam_threshold:50,default_timing:'09:00',design_events_prompt:''});if(u.includes('/health'))return j({status:'ok',memory_mb:100});return j({status:'success'});};
window.lucide={createIcons(){}};HTMLMediaElement.prototype.play=function(){this.__played=true;return Promise.resolve()};HTMLMediaElement.prototype.load=function(){};HTMLMediaElement.prototype.pause=function(){};
</script>'''

admin_stub='''<script>
(function(){const make=()=>({_m:new Map(),getItem(k){return this._m.get(k)||null},setItem(k,v){this._m.set(k,String(v))},removeItem(k){this._m.delete(k)}});Object.defineProperty(window,'sessionStorage',{configurable:true,value:make()});})();
let logged=false; const j=o=>new Response(JSON.stringify(o),{status:logged?200:401,headers:{'content-type':'application/json'}});
window.fetch=async(u,i={})=>{u=String(u);if(u.includes('/api/admin/auth/me')) return logged?j({username:'admin',csrf_token:'csrf'}):new Response(JSON.stringify({detail:'no session'}),{status:401,headers:{'content-type':'application/json'}});if(u.includes('/api/admin/auth/login')){logged=true;return new Response(JSON.stringify({authenticated:true,username:'admin',csrf_token:'csrf',session_token:'qa-token'}),{status:200,headers:{'content-type':'application/json'}})};return j({users:0,users_list:[],errors:[],activity:[]});};
window.lucide={createIcons(){}};HTMLMediaElement.prototype.play=function(){this.__played=true;return Promise.resolve()};HTMLMediaElement.prototype.load=function(){};HTMLMediaElement.prototype.pause=function(){};
</script>'''

with sync_playwright() as p:
    b=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-gpu'])
    # user mobile nav and dropdown
    for w,h in [(360,800),(768,1024),(1440,900)]:
        pg=b.new_page(viewport={'width':w,'height':h}); errs=[];pg.on('pageerror',lambda e:errs.append(str(e)));pg.on('console',lambda m:errs.append(m.text) if m.type=='error' else None)
        pg.set_content(common_html(ROOT/'frontend/index.html',user_stub),wait_until='domcontentloaded');pg.evaluate("sessionStorage.setItem('ema_session_id','qa')");pg.wait_for_timeout(250)
        if w<768:
            pg.evaluate('toggleMobileNav()'); pg.wait_for_timeout(30)
            visible=pg.evaluate("getComputedStyle(document.getElementById('mobile-drawer')).display!=='none' && !document.getElementById('mobile-drawer').classList.contains('hidden')")
            if not visible: FAIL.append(f'mobile {w}: drawer did not open')
            pg.evaluate('toggleMobileNav(false)');
        pg.evaluate("toggleUserMenu({preventDefault(){},stopPropagation(){}})"); pg.wait_for_timeout(30)
        dd=pg.locator('#user-dropdown-menu');
        if not dd.is_visible(): FAIL.append(f'viewport {w}: user dropdown did not open')
        rect=pg.evaluate("(()=>{const r=document.getElementById('user-dropdown-menu').getBoundingClientRect();return {l:r.left,r:r.right,w:r.width}})()")
        if rect['l'] < -1 or rect['r'] > w+1: FAIL.append(f'viewport {w}: dropdown overflow {rect}')
        if errs: FAIL.extend([f'viewport {w}: {e}' for e in errs])
        RES[f'user_{w}']={'dropdown':rect,'errors':errs}
        pg.close()
    # login mobile structural and button handler presence
    pg=b.new_page(viewport={'width':360,'height':800});errs=[];pg.on('pageerror',lambda e:errs.append(str(e)));pg.on('console',lambda m:errs.append(m.text) if m.type=='error' else None)
    pg.set_content(common_html(ROOT/'frontend/login.html', '<style>.hidden{display:none!important}</style>'),wait_until='domcontentloaded');pg.wait_for_timeout(150)
    rect=pg.locator('#btn-google-login').bounding_box();RES['login_mobile_button']=rect
    if not rect or rect['x']<0 or rect['x']+rect['width']>360: FAIL.append('login mobile button overflows')
    if errs: FAIL.extend([f'login: {e}' for e in errs])
    pg.close()
    # admin desktop/mobile login flow
    for w,h in [(360,800),(1440,900)]:
        pg=b.new_page(viewport={'width':w,'height':h});errs=[];pg.on('pageerror',lambda e:errs.append(str(e)));pg.on('console',lambda m:errs.append(m.text) if m.type=='error' else None)
        pg.set_content(common_html(ROOT/'frontend/admin/index.html',admin_stub),wait_until='domcontentloaded');pg.wait_for_timeout(80)
        pg.locator('#admin-username').fill('admin');pg.locator('#admin-password').fill('password');pg.locator('#admin-login-form').dispatch_event('submit');pg.wait_for_timeout(150)
        # entry should be displayed only after successful auth
        shown=pg.evaluate("(()=>{const e=document.getElementById('entry');return e && !e.hidden && !e.classList.contains('is-hidden')})()")
        if not shown: FAIL.append(f'admin {w}: entry video did not start after login')
        if errs: FAIL.extend([f'admin {w}: {e}' for e in errs])
        RES[f'admin_{w}']={'entry_shown':shown,'errors':errs}
        pg.close()
    b.close()
status='PASS' if not FAIL else 'FAIL'; print(json.dumps({'status':status,'results':RES,'failures':FAIL},indent=2)); (ROOT/'QA_REPORT_INTERACTION.json').write_text(json.dumps({'status':status,'results':RES,'failures':FAIL},indent=2)); raise SystemExit(0 if status=='PASS' else 1)
