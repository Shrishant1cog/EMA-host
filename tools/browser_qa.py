#!/usr/bin/env python3
"""Browser-level offline QA for EMA frontend logic.

Network dependencies are stubbed so this test exercises the actual page JavaScript,
DOM events, auth gating, tab routing, entry-video gating, and admin login flow
without contacting Google/Groq.
"""
from __future__ import annotations

import json
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []
RESULTS: dict[str, object] = {}


def browser_html(path: Path, extra_js: str = '') -> str:
    html = path.read_text(encoding='utf-8')
    # Remove remote-only assets for deterministic offline DOM execution.
    html = html.replace('<script defer src="https://cdn.tailwindcss.com"></script>', '<script>window.tailwind={config:{}}</script>')
    html = html.replace('<script src="https://cdn.tailwindcss.com"></script>', '<script>window.tailwind={config:{}}</script>')
    html = html.replace('<script defer src="https://unpkg.com/lucide@latest"></script>', '<script>window.lucide={createIcons(){}};</script>')
    html = html.replace('<script src="https://unpkg.com/lucide@latest"></script>', '<script>window.lucide={createIcons(){}};</script>')
    html = html.replace('<script src="/ux-pro.js"></script>', '<script>' + (ROOT/'frontend/ux-pro.js').read_text(encoding='utf-8') + '</script>')
    html = html.replace('<script src="/admin/admin.js"></script>', '<script>' + (ROOT/'frontend/admin/admin.js').read_text(encoding='utf-8') + '</script>')
    html = html.replace('<link rel="stylesheet" href="/admin/admin.css">', '<style>' + (ROOT/'frontend/admin/admin.css').read_text(encoding='utf-8') + '</style>')
    html = html.replace('<link rel="stylesheet" href="/ux-pro.css">', '<style>' + (ROOT/'frontend/ux-pro.css').read_text(encoding='utf-8') + '</style>')
    # Do not request remote Google Fonts in set_content.
    import re
    html = re.sub(r'<link[^>]+fonts\.googleapis\.com[^>]*>', '', html)
    html = '<script>Object.defineProperty(window,\"localStorage\",{configurable:true,value:{_m:new Map(),getItem(k){return this._m.has(k)?this._m.get(k):null},setItem(k,v){this._m.set(k,String(v))},removeItem(k){this._m.delete(k)},clear(){this._m.clear()}}});Object.defineProperty(window,\"sessionStorage\",{configurable:true,value:{_m:new Map(),getItem(k){return this._m.get(k)||null},setItem(k,v){this._m.set(k,String(v))},removeItem(k){this._m.delete(k)},clear(){this._m.clear()}}});</script><style>.hidden{display:none!important}.opacity-0{opacity:0}.pointer-events-none{pointer-events:none}</style>' + extra_js + html
    return html

USER_STUB = r'''
<script>
(() => {
  const calls = [];
  window.__qaCalls = calls;
  const json = (obj, status=200) => new Response(JSON.stringify(obj), {status, headers:{'content-type':'application/json'}});
  const okUser = {authenticated:true,name:'QA User',total_accounts:1,monitored_accounts:1,accounts:[{email:'qa@example.com',name:'QA User',is_monitored:true}],target_calendar:'auto'};
  window.fetch = async (input, init={}) => {
    const url = String(input); calls.push({url, method:(init.method||'GET').toUpperCase()});
    if (url.includes('/api/user')) return json(okUser);
    if (url.includes('/health')) return json({status:'ok',memory_mb:110,is_lean:true});
    if (url.includes('/api/stats')) return json({summary:{total:12,actioned:4,filtered:2},worker:{is_running:false}});
    if (url.includes('/api/emails')) return json({total:2,emails:[{subject:'Project Meeting',sender:'team@example.com',processed_at:'2026-10-01T10:00:00+05:30',category:'Meeting & Event',status:'ACTIONED',action_taken:'Scheduled'}]});
    if (url.includes('/api/calendar/upcoming')) return json({events:[{id:'qa1',summary:'QA Meeting',start:{dateTime:'2026-10-03T10:00:00+05:30'},htmlLink:'https://calendar.google.com',location:''}]});
    if (url.includes('/api/accounts')) return json({accounts:okUser.accounts});
    if (url.includes('/api/settings') && (init.method||'GET').toUpperCase()==='GET') return json({only_remind_with_files:false,design_events_enabled:false,spam_threshold:50,default_timing:'09:00',design_events_prompt:'',theme_mode:'dark'});
    if (url.includes('/api/settings')) return json({status:'success'});
    if (url.includes('/api/inspect/status')) return json({is_running:false,current_count:0,target_count:10,status_message:'Ready'});
    if (url.includes('/api/inspect/')) return json({status:'success'});
    if (url.includes('/auth/logout')) return json({status:'success'});
    return json({status:'success'});
  };
  const mediaPlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function(){ this.__qaPlayed=true; this.dispatchEvent(new Event('playing')); return Promise.resolve(); };
  HTMLMediaElement.prototype.load = function(){ this.__qaLoaded=true; };
  HTMLMediaElement.prototype.pause = function(){ this.__qaPaused=true; };
})();
</script>
'''

ADMIN_STUB = r'''
<script>
(() => {
  const calls = [];
  window.__qaAdminCalls = calls;
  let loggedIn = false;
  const json = (obj, status=200) => new Response(JSON.stringify(obj), {status, headers:{'content-type':'application/json'}});
  window.fetch = async (input, init={}) => {
    const url=String(input), method=(init.method||'GET').toUpperCase();
    calls.push({url,method});
    if(url.includes('/api/admin/auth/me')) return loggedIn ? json({username:'QA Admin',csrf_token:'qa-csrf'}) : json({detail:'Admin session required.'},401);
    if(url.includes('/api/admin/auth/login')) { loggedIn=true; return json({authenticated:true,username:'QA Admin',csrf_token:'qa-csrf'}); }
    if(url.includes('/api/admin/overview')) return json({users:1,active_users:1,monitoring:1,reauth_required:0,processing:{processed:12},errors:{error_count:0},accounts:1,health:{status:'ok',memory_mb:110,is_lean:true,uptime:'1m',server_time:'2026-10-01T10:00:00+05:30'},worker:{is_running:false},inspect:{is_running:false}});
    if(url.includes('/api/admin/ai/status')) return json({primary_model:'qa-model',fallback_models:[],verifier:{enabled:true,model:'qa-verifier',reasoning_effort:'medium'}});
    if(url.includes('/api/admin/users?')) return json({total:1,users:[{email:'qa@example.com',name:'QA User',is_monitored:true,needs_reauth:false,stats:{processed:12,actioned:4,filtered:2}}]});
    if(url.includes('/api/admin/errors')) return json({errors:[],categories:[]});
    if(url.includes('/api/admin/activity')) return json({activity:[]});
    if(url.includes('/api/admin/logs')) return json({lines:[]});
    if(url.includes('/api/admin/auth/logout')) { loggedIn=false; return json({status:'success'}); }
    return json({status:'success'});
  };
  HTMLMediaElement.prototype.play = function(){ this.__qaPlayed=true; return Promise.resolve(); };
  HTMLMediaElement.prototype.load = function(){ this.__qaLoaded=true; };
  HTMLMediaElement.prototype.pause = function(){ this.__qaPaused=true; };
})();
</script>
'''


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox','--disable-gpu'])
        # User dashboard
        page = browser.new_page(viewport={'width':1440,'height':900})
        errors=[]; page.on('pageerror', lambda e: errors.append(str(e))); page.on('console', lambda m: errors.append(f'console:{m.type}:{m.text}') if m.type=='error' else None)
        page.set_content(browser_html(ROOT/'frontend/index.html', USER_STUB), wait_until='domcontentloaded')
        page.wait_for_timeout(450)
        RESULTS['user_pageerrors_initial']=errors.copy()
        if errors: FAILURES.extend([f'user page error: {e}' for e in errors])
        if not page.evaluate('window.__emaAuthVerified === true'): FAILURES.append('user auth promise did not resolve true')
        if not page.evaluate("document.body.classList.contains('auth-ready')"): FAILURES.append('user body did not enter auth-ready state')
        # The media must not be eagerly sourced in markup; it should be sourced after auth.
        pre = page.evaluate("({src:document.getElementById('ema-entry-video-media').getAttribute('src'), dataSrc:document.getElementById('ema-entry-video-media').dataset.src, played:!!document.getElementById('ema-entry-video-media').__qaPlayed})")
        RESULTS['user_entry_video_state']=pre
        if not pre['dataSrc'].endswith('ema-entry.mp4'): FAILURES.append('user entry video data-src missing')
        if not pre['played']: FAILURES.append('user entry video did not start after auth in browser harness')
        # Exercise all user tabs and quick palette.
        for tab in ['accounts','calendar','drive','emails','settings','dashboard','emails','calendar']:
            page.evaluate(f"switchTab('tab-{tab}')")
            page.wait_for_timeout(100)
        page.keyboard.press('Control+K'); page.wait_for_timeout(80)
        if not page.locator('#quick-palette-backdrop').is_visible(): FAILURES.append('quick palette did not open')
        page.locator('#quick-palette-search').fill('calendar'); page.wait_for_timeout(50); page.keyboard.press('Escape')
        if page.evaluate("!document.getElementById('quick-palette-backdrop').classList.contains('hidden')"): FAILURES.append('quick palette did not close')
        calls=page.evaluate('window.__qaCalls')
        # Within the cache window, switching back to the same tab should not repeatedly reload it.
        email_calls=[c for c in calls if '/api/emails?' in c['url']]
        RESULTS['user_fetch_call_count']=len(calls); RESULTS['user_email_call_count']=len(email_calls)
        if len(email_calls)>2: FAILURES.append(f'user email tab made {len(email_calls)} calls; cache/race guard failed')
        # Persistent-login contract: session ID is stored in localStorage, not sessionStorage.
        idx_source = (ROOT/'frontend/index.html').read_text(encoding='utf-8')
        login_source = (ROOT/'frontend/login.html').read_text(encoding='utf-8')
        RESULTS['persistent_session_contract'] = {
            'index_uses_localStorage': "localStorage.getItem(SESSION_KEY)" in idx_source and "localStorage.setItem(SESSION_KEY" in idx_source,
            'login_uses_localStorage': "localStorage.getItem(SESSION_KEY)" in login_source,
            'index_uses_sessionStorage_for_session': 'sessionStorage.getItem(SESSION_KEY)' in idx_source or 'sessionStorage.setItem(SESSION_KEY' in idx_source,
            'login_uses_sessionStorage_for_session': 'sessionStorage.getItem(SESSION_KEY)' in login_source,
        }
        if not RESULTS['persistent_session_contract']['index_uses_localStorage']: FAILURES.append('dashboard session ID is not persisted in localStorage')
        if not RESULTS['persistent_session_contract']['login_uses_localStorage']: FAILURES.append('login page does not read persisted session ID from localStorage')
        if RESULTS['persistent_session_contract']['index_uses_sessionStorage_for_session'] or RESULTS['persistent_session_contract']['login_uses_sessionStorage_for_session']:
            FAILURES.append('session ID still uses sessionStorage')

        # User unauthenticated static behavior: script contract + no video source before auth.
        unauth_page = browser.new_page()
        unauth_page.set_content(browser_html(ROOT/'frontend/index.html', r'''<script>
        window.fetch=async()=>new Response(JSON.stringify({authenticated:false}),{status:401,headers:{'content-type':'application/json'}});
        HTMLMediaElement.prototype.play=function(){this.__qaPlayed=true;return Promise.resolve()};
        HTMLMediaElement.prototype.load=function(){};
        HTMLMediaElement.prototype.pause=function(){};
        </script>'''), wait_until='domcontentloaded')
        unauth_state=unauth_page.evaluate("({src:document.getElementById('ema-entry-video-media')?.getAttribute('src')||null, dataSrc:document.getElementById('ema-entry-video-media')?.dataset.src||null})")
        RESULTS['user_unauth_video_markup']=unauth_state
        if unauth_state['src'] is not None: FAILURES.append('unauthenticated page eagerly has video src')
        unauth_page.close()

        # Login page
        page = browser.new_page(viewport={'width':1200,'height':800})
        errors=[]; page.on('pageerror', lambda e: errors.append(str(e)))
        page.set_content(browser_html(ROOT/'frontend/login.html', r'''<script>
        window.fetch=async()=>new Response(JSON.stringify({authenticated:false}),{status:401,headers:{'content-type':'application/json'}});
        </script>'''), wait_until='domcontentloaded')
        page.wait_for_timeout(150)
        RESULTS['login_pageerrors']=errors.copy()
        if errors: FAILURES.extend([f'login page error: {e}' for e in errors])
        if not page.locator('#btn-google-login').is_enabled(): FAILURES.append('login button unexpectedly disabled')
        page.close()

        # Admin login + authenticated entry video
        page = browser.new_page(viewport={'width':1400,'height':850})
        errors=[]; page.on('pageerror', lambda e: errors.append(str(e))); page.on('console', lambda m: errors.append(f'console:{m.type}:{m.text}') if m.type=='error' else None)
        page.set_content(browser_html(ROOT/'frontend/admin/index.html', ADMIN_STUB), wait_until='domcontentloaded')
        page.wait_for_timeout(200)
        if not page.locator('#admin-login').is_visible(): FAILURES.append('admin login screen not visible before auth')
        page.locator('#admin-username').fill('QA Admin'); page.locator('#admin-password').fill('not-a-real-password'); page.locator('#admin-login-form').evaluate("f=>f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
        page.wait_for_timeout(450)
        RESULTS['admin_pageerrors']=errors.copy()
        if errors: FAILURES.extend([f'admin page error: {e}' for e in errors])
        if page.locator('#admin-login').is_visible(): FAILURES.append('admin login screen still visible after successful mock login')
        entry=page.locator('#entry')
        entry_dom=page.evaluate("({hidden:document.getElementById('entry').hidden,classes:document.getElementById('entry').className})")
        RESULTS['admin_entry_dom_state']=entry_dom
        if entry_dom['hidden'] or 'is-hidden' in entry_dom['classes']: FAILURES.append('admin entry video did not become visible after auth')
        video_state=page.evaluate("({src:document.getElementById('entry-video')?.src||'',played:!!document.getElementById('entry-video')?.__qaPlayed})")
        RESULTS['admin_entry_video_state']=video_state
        if not video_state['src'].endswith('/assets/ema-entry.mp4'): FAILURES.append('admin entry video source incorrect')
        if not video_state['played']: FAILURES.append('admin entry video did not play after auth')
        # Admin navigation + refresh.
        for view in ['users','activity','errors','logs','overview']:
            page.evaluate(f"window.EMAAdmin.switchView('{view}')")
            page.wait_for_timeout(80)
        page.close()

        browser.close()

    RESULTS['status']='PASS' if not FAILURES else 'FAIL'
    RESULTS['failures']=FAILURES
    print(json.dumps(RESULTS, indent=2))
    (ROOT/'QA_REPORT_BROWSER.json').write_text(json.dumps(RESULTS, indent=2), encoding='utf-8')
    return 0 if not FAILURES else 1

if __name__ == '__main__':
    raise SystemExit(run())
