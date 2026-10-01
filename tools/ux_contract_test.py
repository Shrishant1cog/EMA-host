from pathlib import Path
import subprocess, re, json
ROOT=Path(__file__).resolve().parents[1]
F=ROOT/'frontend'; B=ROOT/'backend'
checks={}
idx=(F/'index.html').read_text(encoding='utf-8')
login=(F/'login.html').read_text(encoding='utf-8')
adm=(F/'admin/index.html').read_text(encoding='utf-8')
adjs=(F/'admin/admin.js').read_text(encoding='utf-8')
main=(B/'main.py').read_text(encoding='utf-8')
st=(B/'utils/state_tracker.py').read_text(encoding='utf-8')
video=F/'assets/ema-entry.mp4'
checks['user_entry_has_auth_gate']='ema:auth-verified' in idx and 'await video.play()' in idx
checks['user_entry_not_autoplay_before_auth']='preload="none"' in idx and not ('autoplay' in idx[idx.find('<video'):idx.find('</video>')])
checks['admin_entry_hidden']='id="entry" hidden' in adm
checks['admin_entry_not_autoplay']='autoplay' not in adm[adm.find('<video'):adm.find('</video>')]
checks['admin_entry_starts_after_auth']='playEntry();await bootAfterAuth()' in adjs and 'hideLogin();showShell();playEntry();return true' in adjs
checks['no_duplicate_js_session_cookie']='document.cookie' not in idx and 'document.cookie' not in login
checks['admin_cookie_local_http_safe']='def _admin_cookie_secure(request: Request)' in main and 'if scheme != "https":\n        return False' in main
checks['session_legacy_migration']='ensure_legacy_session' in main and 'if len(clean) < 8' in st
checks['dark_only_backend']="theme_mode must be 'dark' or 'read'" in main
checks['dark_only_frontend']='theme-btn-light' not in idx and 'value="light"' not in idx
checks['video_present']=video.exists() and video.stat().st_size>0
checks['backend_compile']=subprocess.run(['python','-m','py_compile',str(B/'main.py'),str(B/'utils/state_tracker.py')]).returncode==0
for p in [F/'admin/admin.js',F/'ux-pro.js']:
    checks[f'js_{p.name}']=subprocess.run(['node','--check',str(p)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
print(json.dumps(checks,indent=2))
failed=[k for k,v in checks.items() if not v]
print('UX_CONTRACT_TEST:', 'PASS' if not failed else 'FAIL')
if failed:
    print('FAILED:', ', '.join(failed)); raise SystemExit(1)
