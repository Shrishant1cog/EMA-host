from __future__ import annotations
from pathlib import Path
from bs4 import BeautifulSoup
import subprocess, tempfile

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / 'frontend'
fail: list[str] = []

required = [
    FRONT/'index.html', FRONT/'login.html', FRONT/'privacy.html', FRONT/'terms.html',
    FRONT/'styles.css', FRONT/'main.js', FRONT/'favicon.ico',
    FRONT/'assets'/'logo.png', FRONT/'assets'/'ema-entry.mp4',
    FRONT/'admin'/'index.html', FRONT/'admin'/'admin.css', FRONT/'admin'/'admin.js'
]
for p in required:
    if not p.exists(): fail.append(f'missing {p.relative_to(ROOT)}')

if not fail:
    index = (FRONT/'index.html').read_text(encoding='utf-8', errors='ignore')
    admin = (FRONT/'admin'/'index.html').read_text(encoding='utf-8', errors='ignore')
    admin_js = (FRONT/'admin'/'admin.js').read_text(encoding='utf-8', errors='ignore')
    if 'theme-btn-light' in index or "applyVisualMode('light')" in index or 'applyVisualMode("light")' in index:
        fail.append('light theme control/reference still present')
    if 'id="ema-entry-video"' not in index or 'assets/ema-entry.mp4' not in index:
        fail.append('dashboard entry video wiring missing')
    if 'deviceMemory' not in index or 'data-performance' not in index:
        fail.append('adaptive performance layer missing')
    if 'openQuickPalette' not in index or 'Ctrl K' not in index:
        fail.append('quick action palette missing')
    if '/api/admin/auth/me' not in admin_js or '/api/admin/auth/logout' not in admin_js:
        fail.append('admin auth endpoints missing from frontend')
    if 'admin-login-form' not in admin or 'Users & accounts' not in admin or 'Errors & warnings' not in admin:
        fail.append('admin authentication/users/errors UI incomplete')

    for html_path in [FRONT/'index.html', FRONT/'login.html', FRONT/'admin'/'index.html']:
        soup = BeautifulSoup(html_path.read_text(encoding='utf-8', errors='ignore'), 'html.parser')
        ids = [x.get('id') for x in soup.find_all(attrs={'id': True})]
        dupes = sorted({x for x in ids if ids.count(x) > 1})
        if dupes: fail.append(f'duplicate ids in {html_path.relative_to(ROOT)}: {dupes}')
        scripts = [s.string or s.get_text() for s in soup.find_all('script') if (s.string or s.get_text()).strip()]
        for i, code in enumerate(scripts):
            with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as t:
                t.write(code); temp = Path(t.name)
            try:
                r = subprocess.run(['node', '--check', str(temp)], capture_output=True, text=True)
                if r.returncode != 0: fail.append(f'JS syntax {html_path.name} script#{i+1}: {r.stderr.strip()}')
            finally:
                temp.unlink(missing_ok=True)

    for js_path in [FRONT/'main.js', FRONT/'admin'/'admin.js']:
        r = subprocess.run(['node', '--check', str(js_path)], capture_output=True, text=True)
        if r.returncode != 0: fail.append(f'JS syntax {js_path.relative_to(ROOT)}: {r.stderr.strip()}')

video_size = (FRONT/'assets'/'ema-entry.mp4').stat().st_size if (FRONT/'assets'/'ema-entry.mp4').exists() else 0
if video_size > 1024*1024: fail.append(f'entry video is too large: {video_size} bytes')

print({'status':'PASS' if not fail else 'FAIL', 'failures':fail, 'entry_video_kb':round(video_size/1024,1)})
raise SystemExit(1 if fail else 0)
