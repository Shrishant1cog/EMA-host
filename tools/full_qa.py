#!/usr/bin/env python3
"""Offline QA for the EMA website package.

This checks source integrity without requiring Google/Groq network access.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]

class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.refs = []
        self.scripts = []
        self.video_attrs = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'id' in a: self.ids.append((a['id'], tag))
        if tag in ('script','link','img','video','source'):
            if a.get('src'): self.refs.append(('src', a['src'], tag))
            if a.get('href'): self.refs.append(('href', a['href'], tag))
        if tag == 'script' and a.get('src'): self.scripts.append(a['src'])
        if tag == 'video': self.video_attrs.append(a)


def run(cmd: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode == 0, p.stdout.strip()

failures: list[str] = []
checks: dict[str, object] = {}

# Python syntax
py_files = sorted({p for d in [ROOT/'backend', ROOT/'extractors', ROOT/'tools'] for p in d.rglob('*.py') if '__pycache__' not in p.parts})
py_ok = True
for p in py_files:
    ok, out = run([sys.executable, '-m', 'py_compile', str(p)])
    if not ok:
        py_ok = False; failures.append(f'Python compile failed: {p}: {out[-500:]}')
checks['python_compile'] = py_ok

# JS syntax
js_ok = True
for p in sorted((ROOT/'frontend').rglob('*.js')):
    ok, out = run(['node', '--check', str(p)])
    if not ok:
        js_ok = False; failures.append(f'JS syntax failed: {p}: {out[-500:]}')
checks['javascript_syntax'] = js_ok

# Frontend source-level regression guards
main_js = (ROOT/'frontend/index.html').read_text(encoding='utf-8')
handler_refs = set(re.findall(r'onclick=\"([A-Za-z_$][\w$]*)\s*\(', main_js, flags=re.I))
defined_handlers = set(re.findall(r'(?:function\s+|window\.)([A-Za-z_$][\w$]*)\s*(?:=|\()', main_js))
missing_handlers = sorted(h for h in handler_refs if h not in defined_handlers and h not in {'openExternal'})
checks['inline_click_handlers_defined'] = not missing_handlers
if missing_handlers: failures.append(f'index.html: undefined inline click handlers: {missing_handlers[:20]}')
if 'setInterval(() => {' in main_js and "loadTabData('tab-dashboard', true)" in main_js:
    # This exact pattern used to refresh the full dashboard (including calendar) every 30s.
    failures.append('index.html: dashboard polling still performs full dashboard reload')
checks['dashboard_poll_does_not_reload_full_dashboard'] = "loadTabData('tab-dashboard', true);" not in main_js

# Frontend structural checks
for name in ['index.html','login.html']:
    p = ROOT/'frontend'/name
    parser = AssetParser(); parser.feed(p.read_text(encoding='utf-8'))
    counts = {}
    for i, _ in parser.ids: counts[i] = counts.get(i,0)+1
    dupes = sorted(k for k,v in counts.items() if v > 1)
    checks[f'{name}_duplicate_ids'] = not dupes
    if dupes: failures.append(f'{name}: duplicate ids: {dupes}')
    for kind, ref, tag in parser.refs:
        if ref.startswith(('http://','https://','//','data:','mailto:','#','javascript:')): continue
        # Absolute / paths map to frontend root.
        clean = ref.split('?',1)[0].lstrip('/')
        if not clean: continue
        target = ROOT/'frontend'/clean
        if not target.exists():
            failures.append(f'{name}: missing local {tag} {ref}')
    # User UI must not contain terminal logs navigation.
    if name == 'index.html':
        text = p.read_text(encoding='utf-8').lower()
        if re.search(r'>\s*terminal logs\s*<|id=["\']tab-terminal|btn-tab-terminal', text):
            failures.append('index.html: terminal logs UI still present')
        checks['terminal_logs_removed'] = True

# Entry video contract
idx = (ROOT/'frontend/index.html').read_text(encoding='utf-8')
admin = (ROOT/'frontend/admin/index.html').read_text(encoding='utf-8')
mp4 = ROOT/'frontend/assets/ema-entry.mp4'
poster = ROOT/'frontend/assets/ema-entry-poster.jpg'
checks['entry_video_present'] = mp4.is_file() and mp4.stat().st_size > 0
checks['entry_poster_present'] = poster.is_file() and poster.stat().st_size > 0
if not mp4.is_file(): failures.append('Entry MP4 missing')
if not poster.is_file(): failures.append('Entry poster missing')
if 'data-src="assets/ema-entry.mp4"' not in idx: failures.append('User entry video is not lazy-auth sourced')
if '<source src="assets/ema-entry.mp4"' in idx: failures.append('User entry video still eagerly declares a source')
if 'preload="none"' not in idx: failures.append('User entry video preload is not none')
if 'ema:auth-verified' not in idx: failures.append('User entry video is not auth-gated')
if 'data-src="/assets/ema-entry.mp4"' not in admin: failures.append('Admin entry video is not lazy sourced')
entry_gate_ok = (
    'data-src="assets/ema-entry.mp4"' in idx
    and '<source src="assets/ema-entry.mp4"' not in idx
    and 'preload="none"' in idx
    and 'ema:auth-verified' in idx
    and 'data-src="/assets/ema-entry.mp4"' in admin
)
checks['entry_video_auth_gating'] = entry_gate_ok
if not entry_gate_ok: failures.append('Entry video auth-gating contract is incomplete')

# Performance regression guards
ux = (ROOT/'frontend/ux-pro.js').read_text(encoding='utf-8')
uxcss = (ROOT/'frontend/ux-pro.css').read_text(encoding='utf-8')
checks['no_perpetual_pointer_raf'] = 'requestAnimationFrame(tick)' not in ux and 'requestAnimationFrame' not in ux and 'ema-pointer-glow' not in ux
checks['no_body_mutation_observer'] = 'MutationObserver' not in ux
checks['no_repeated_kpi_observer'] = "watchKpis" not in ux
login_text = (ROOT/'frontend/login.html').read_text(encoding='utf-8')
admin_css = (ROOT/'frontend/admin/admin.css').read_text(encoding='utf-8')
checks['no_perpetual_login_animation'] = 'infinite' not in login_text
checks['no_perpetual_admin_pulse'] = 'livePulse' not in admin_css and 'infinite' not in admin_css
if 'infinite' in login_text: failures.append('login.html: perpetual animation remains')
if 'livePulse' in admin_css or 'infinite' in admin_css: failures.append('admin.css: perpetual pulse animation remains')
if 'requestAnimationFrame(tick)' in ux: failures.append('ux-pro.js: perpetual pointer requestAnimationFrame remains')
if 'MutationObserver' in ux: failures.append('ux-pro.js: broad MutationObserver remains')
if 'scroll-behavior: smooth' in (ROOT/'frontend/index.html').read_text(encoding='utf-8'):
    # This is a performance risk on the main scroller; current CSS should be auto.
    failures.append('index.html: smooth scrolling still enabled in main app scroller')
checks['main_scroll_not_smooth_css'] = 'scroll-behavior: smooth' not in (ROOT/'frontend/index.html').read_text(encoding='utf-8')

# Config safety check
cfg = (ROOT/'config.py').read_text(encoding='utf-8')
checks['local_env_precedence_fixed'] = '_LOCAL_ENV_MODE' in cfg and 'override=_LOCAL_ENV_MODE' in cfg
if not checks['local_env_precedence_fixed']: failures.append('config.py: local .env precedence fix missing')
main_py = (ROOT/'backend/main.py').read_text(encoding='utf-8')
checks['admin_pbkdf2'] = '_parse_pbkdf2_password_hash' in main_py and '_verify_admin_password' in main_py
checks['admin_retry_after'] = '_admin_login_retry_after' in main_py and 'Retry-After' in main_py
if not checks['admin_retry_after']: failures.append('admin login 429 retry-after support missing')

# Basic route inventory sanity
try:
    tree = ast.parse(main_py)
    route_count = 0
    seen_routes = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in n.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and isinstance(dec.func.value, ast.Name) and dec.func.value.id == 'app':
                    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                        route_count += 1; seen_routes.add(dec.args[0].value)
    checks['backend_route_count'] = route_count
    checks['backend_has_health'] = '/health' in seen_routes
    checks['backend_has_admin_login'] = '/api/admin/auth/login' in seen_routes
    if '/health' not in seen_routes or '/api/admin/auth/login' not in seen_routes: failures.append('Backend route inventory missing core routes')
except Exception as exc:
    failures.append(f'Backend AST route scan failed: {exc}')

# MP4 metadata with ffprobe when available
if shutil.which('ffprobe'):
    ok, out = run(['ffprobe','-v','error','-show_entries','stream=codec_name,width,height,r_frame_rate','-show_entries','format=duration,size','-of','json',str(mp4)])
    checks['entry_video_ffprobe'] = ok
    if not ok: failures.append(f'ffprobe failed for entry video: {out[-500:]}')
else:
    checks['entry_video_ffprobe'] = 'not-installed'

status = 'PASS' if not failures else 'FAIL'
report = {'status':status,'checks':checks,'failures':failures}
print(json.dumps(report, indent=2))
(Path(ROOT/'QA_REPORT_STATIC.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
sys.exit(0 if status == 'PASS' else 1)
