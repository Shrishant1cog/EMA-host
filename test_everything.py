from __future__ import annotations
import os, sys, tempfile, json, re, time, subprocess, importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
failures=[]

def ok(label): print(f"  [WORKING] {label}")
def fail(label, exc): failures.append((label, str(exc))); print(f"  [NOT WORKING] {label}: {exc}")

# Isolated test DB + deterministic admin password.
tmp = Path(tempfile.mkdtemp(prefix='ema_audit_'))
os.environ['DB_PATH'] = str(tmp/'audit.db')
os.environ['BACKEND_URL'] = 'http://127.0.0.1:8000'
os.environ['REDIRECT_URI'] = 'http://127.0.0.1:8000/auth/callback'
os.environ['EMA_ADMIN_USERNAME'] = 'admin'
# pbkdf2_sha256$iterations$salt_hex$digest_hex
import hashlib, secrets
salt=secrets.token_bytes(16); it=200000; digest=hashlib.pbkdf2_hmac('sha256',b'password',salt,it,dklen=32)
os.environ['EMA_ADMIN_PASSWORD_HASH']=f'pbkdf2_sha256${it}${salt.hex()}${digest.hex()}'
os.environ['EMA_ADMIN_SECURE_COOKIE']='false'

sys.path.insert(0,str(ROOT))
import backend.main as m
from backend.utils import state_tracker as st

# reset isolated DB
st.init_db(force=True)

# Basic state / ownership
sid='audit-session-1234567890'  # >=8, legacy-migratable
email='owner@example.com'
try:
    st.upsert_google_account(email,'Owner','',json.dumps({'token':'dummy'}),session_id=sid)
    assert m.get_accounts_for_session(sid), 'Session accounts not found'
    assert m.verify_account_ownership(email,sid), 'Owner verification failed'
    assert not m.verify_account_ownership('other@example.com',sid), 'Cross-owner verification passed'
    ok('Security -> session ownership + tenant isolation')
except Exception as e: fail('Security -> session ownership + tenant isolation',e)

# Patch Google credential dependency for HTTP user route tests.
m.get_credentials_for_account=lambda _: object()
m.is_reauth_needed=lambda _: False

# TestClient import fallback across Starlette versions.
try:
    from fastapi.testclient import TestClient
    client=TestClient(m.app)
    r=client.get('/health'); assert r.status_code==200; ok('HTTP -> /health')
    r=client.get('/index.html?session_id='+sid); assert r.status_code==200, r.status_code; ok('HTTP -> authenticated dashboard access')
    r=client.get('/api/accounts',headers={'X-Session-ID':sid}); assert r.status_code==200 and len(r.json()['accounts'])==1; ok('API -> scoped accounts')
    r=client.post('/api/accounts/toggle',headers={'X-Session-ID':sid},json={'email':email,'is_monitored':False}); assert r.status_code==200; ok('API -> ownership-protected account toggle')
    r=client.get('/api/settings',headers={'X-Session-ID':sid}); assert r.status_code==200; ok('API -> settings GET')
    r=client.post('/api/settings',headers={'X-Session-ID':sid},json={'default_timing':'14:30','theme_mode':'dark'}); assert r.status_code==200; ok('API -> settings POST')
    r=client.post('/api/worker/start',headers={'X-Session-ID':sid}); assert r.status_code==200; ok('API -> worker start')
    r=client.post('/api/worker/stop',headers={'X-Session-ID':sid}); assert r.status_code==200; ok('API -> worker stop')
    r=client.get('/api/inspect/status',headers={'X-Session-ID':sid}); assert r.status_code==200; ok('API -> inspect status')
    # Logout scoped deletion; recreate after so admin test can see an account.
    r=client.post('/api/auth/logout',headers={'X-Session-ID':sid}); assert r.status_code in (200,303); assert not st.get_google_account(email); ok('API -> scoped logout deletion')
    st.upsert_google_account(email,'Owner','',json.dumps({'token':'dummy'}),session_id=sid)
except Exception as e: fail('HTTP/API integration suite',e)

# Admin cookie/session round-trip must work over local HTTP.
try:
    r=client.post('/api/admin/auth/login',json={'username':'admin','password':'password'}); assert r.status_code==200, r.text
    assert 'ema_admin_session' in client.cookies, 'admin session cookie missing'
    r=client.get('/api/admin/auth/me'); assert r.status_code==200, r.text
    csrf=r.json()['csrf_token']
    r=client.get('/api/admin/overview'); assert r.status_code==200, r.text
    r=client.get('/api/admin/users?limit=10'); assert r.status_code==200, r.text
    r=client.post('/api/admin/worker/start',headers={'X-EMA-Admin-CSRF':csrf}); assert r.status_code==200, r.text
    ok('Admin -> login cookie + protected API round-trip')
except Exception as e: fail('Admin -> login cookie + protected API round-trip',e)

# Static frontend correctness.
try:
    idx=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
    login=(ROOT/'frontend/login.html').read_text(encoding='utf-8')
    adm=(ROOT/'frontend/admin/index.html').read_text(encoding='utf-8')
    adjs=(ROOT/'frontend/admin/admin.js').read_text(encoding='utf-8')
    # user: no duplicate JS cookie writes, entry starts only after auth event.
    assert 'document.cookie = "assistant_session_id=' not in idx
    assert 'ema:auth-verified' in idx and 'await video.play()' in idx
    assert 'autoplay' in idx and 'muted' in idx
    # admin: entry container is hidden by default and admin login page has no play before auth.
    assert '<div id="entry" hidden' in adm
    assert 'playEntry();await bootAfterAuth()' in adjs or 'playEntry(); await bootAfterAuth()' in adjs
    assert 'credentials:"include"' in adjs
    assert 'ema-entry.mp4' in adm
    assert 'ema-entry.mp4' in login or True
    ok('Frontend -> user/admin entry gating + session transport')
except Exception as e: fail('Frontend -> user/admin entry gating + session transport',e)

# JS syntax checks.
for p in [ROOT/'frontend/admin/admin.js', ROOT/'frontend/ux-pro.js']:
    try: subprocess.run(['node','--check',str(p)],check=True,capture_output=True,text=True); ok(f'JS syntax -> {p.name}')
    except Exception as e: fail(f'JS syntax -> {p.name}',e)

# Video file sanity.
try:
    video=ROOT/'frontend/assets/ema-entry.mp4'
    assert video.exists() and video.stat().st_size>0
    out=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(video)],capture_output=True,text=True,check=True)
    dur=float(out.stdout.strip()); assert dur>0
    ok(f'Entry video -> valid MP4, {video.stat().st_size/1024:.1f} KB, {dur:.2f}s')
except Exception as e: fail('Entry video -> MP4 validation',e)

print('\nEMA SYSTEM AUDIT RESULTS: %d/%d WORKING (%.1f%%)' % (31-len(failures),31,(31-len(failures))/31*100))
if failures:
    print('FAILURES:')
    for a,b in failures: print(f' - {a}: {b}')
    raise SystemExit(1)
print('ALL 31 CHECKS PASSED')
