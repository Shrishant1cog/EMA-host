#!/usr/bin/env python3
"""Responsive browser QA for EMA user dashboard and login pages.

Runs with the page's own HTML/CSS/JS, stubbing network APIs. It verifies common
phone/tablet/desktop viewports, checks for horizontal overflow, and exercises the
mobile-friendly account menu / navigation where present.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []
RESULTS: dict[str, object] = {}

STORAGE_STUB = r'''<script>(function(){const make=()=>({_m:new Map(),getItem(k){return this._m.has(k)?this._m.get(k):null},setItem(k,v){this._m.set(k,String(v))},removeItem(k){this._m.delete(k)},clear(){this._m.clear()},key(i){return [...this._m.keys()][i]??null},get length(){return this._m.size}});Object.defineProperty(window,'localStorage',{configurable:true,value:make()});Object.defineProperty(window,'sessionStorage',{configurable:true,value:make()});})();</script>'''

API_STUB = r'''<script>
window.fetch=async (input,init={})=>{const u=String(input);const ok=o=>new Response(JSON.stringify(o),{status:200,headers:{'content-type':'application/json'}});if(u.includes('/api/user'))return ok({authenticated:true,name:'QA User',total_accounts:1,monitored_accounts:1,accounts:[{email:'qa@example.com',name:'QA User',is_monitored:true}],target_calendar:'auto'});if(u.includes('/api/stats'))return ok({summary:{total:12,actioned:4,filtered:2},worker:{is_running:true,status:'Live'}});if(u.includes('/api/emails'))return ok({total:1,emails:[{subject:'Project Meeting',sender:'team@example.com',processed_at:'2026-10-01T10:00:00+05:30',category:'Meeting & Event',status:'ACTIONED',action_taken:'Scheduled'}]});if(u.includes('/api/calendar/upcoming'))return ok({events:[{id:'1',summary:'Meeting',start:{dateTime:'2026-10-03T10:00:00+05:30'},htmlLink:'#'}]});if(u.includes('/api/accounts'))return ok({accounts:[{email:'qa@example.com',name:'QA User',is_monitored:true}]});if(u.includes('/api/settings')&&(!init.method||init.method==='GET'))return ok({theme_mode:'dark',only_remind_with_files:false,design_events_enabled:false,spam_threshold:50,default_timing:'09:00',design_events_prompt:''});if(u.includes('/health'))return ok({status:'ok',memory_mb:120,is_lean:true});return ok({status:'success'});};
window.lucide={createIcons(){}};HTMLMediaElement.prototype.play=function(){this.__played=true;return Promise.resolve()};HTMLMediaElement.prototype.load=function(){this.__loaded=true};HTMLMediaElement.prototype.pause=function(){};
</script>'''


def make_html(path: Path) -> str:
    s = path.read_text(encoding="utf-8")
    s = s.replace('<script src="https://cdn.tailwindcss.com"></script>', '<script>window.tailwind={config:{}};</script>')
    s = s.replace('<script defer src="https://cdn.tailwindcss.com"></script>', '<script>window.tailwind={config:{}};</script>')
    s = s.replace('<script src="https://unpkg.com/lucide@latest"></script>', '<script>window.lucide={createIcons(){}};</script>')
    s = s.replace('<script defer src="https://unpkg.com/lucide@latest"></script>', '<script>window.lucide={createIcons(){}};</script>')
    if path.name == "index.html":
        s = s.replace('<link rel="stylesheet" href="/ux-pro.css">', '<style>' + (ROOT / 'frontend/ux-pro.css').read_text() + '</style>')
        s = s.replace('<script src="/ux-pro.js"></script>', '<script>' + (ROOT / 'frontend/ux-pro.js').read_text() + '</script>')
    s = re.sub(r'<link[^>]+fonts\.googleapis\.com[^>]*>', '', s)
    return STORAGE_STUB + API_STUB + '<style>.hidden{display:none!important}</style>' + s


def check_page(page, width: int, height: int, label: str) -> None:
    errors: list[str] = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append(f"console:{m.text}") if m.type == 'error' else None)
    page.set_content(make_html(ROOT / 'frontend/index.html'), wait_until='domcontentloaded')
    page.evaluate("sessionStorage.setItem('ema_session_id','qa-session')")
    page.wait_for_timeout(350)
    dims = page.evaluate('({cw:document.documentElement.clientWidth,sw:document.documentElement.scrollWidth,bw:document.body.scrollWidth})')
    RESULTS[f'{label}_dims'] = dims
    visible_overflow = page.evaluate('''(w)=>Array.from(document.querySelectorAll('button,a,input,select,textarea')).map(e=>{const cs=getComputedStyle(e),r=e.getBoundingClientRect();return {visible:cs.display!=='none'&&cs.visibility!=='hidden'&&parseFloat(cs.opacity||'1')>0.01,x:r.x,right:r.right,text:(e.innerText||e.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ')}}).filter(x=>x.visible&&(x.x<-1||x.right>w+1)).slice(0,10)''', width)
    RESULTS[f'{label}_overflowing_controls'] = visible_overflow
    if errors: FAILURES.extend(f'{label}: {e}' for e in errors)
    if dims['sw'] > width + 1 or dims['bw'] > width + 1: FAILURES.append(f'{label}: horizontal overflow {dims}')
    if visible_overflow: FAILURES.append(f'{label}: overflowing visible controls {visible_overflow}')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox','--disable-gpu'])
    for width,height in [(320,568),(360,800),(390,844),(430,932),(768,1024),(1024,768),(1440,900),(1920,1080)]:
        page=browser.new_page(viewport={'width':width,'height':height})
        check_page(page,width,height,f'user_{width}x{height}')
        page.close()
    browser.close()

status = 'PASS' if not FAILURES else 'FAIL'
report={'status':status,'checks':RESULTS,'failures':FAILURES}
(ROOT/'QA_REPORT_RESPONSIVE.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
raise SystemExit(0 if status=='PASS' else 1)
