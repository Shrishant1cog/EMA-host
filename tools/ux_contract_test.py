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
styles=(F/'styles.css').read_text(encoding='utf-8')
uxcss=(F/'ux-pro.css').read_text(encoding='utf-8')
admin_css=(F/'admin/admin.css').read_text(encoding='utf-8')
ui_source='\n'.join([idx, login, adm, adjs, styles, uxcss, admin_css])
gradient_defs=re.findall(r'(?:linear|radial|conic)-gradient[(][^;{}]*', ui_source, re.I)
checks['no_purple_gradient_defs']=not any(re.search(r'indigo|violet|purple|99,\\s*102,\\s*241|129,\\s*140,\\s*248|#6366f1|#4f46e5|#818cf8',g,re.I) for g in gradient_defs)
checks['no_purple_tailwind_gradients']=not re.search(r'bg-gradient-to-[^"]*(?:indigo|violet|purple)',ui_source,re.I)
checks['no_emoji_icons_in_ui']=not any(0x1F300 <= ord(ch) <= 0x1FAFF or 0x2600 <= ord(ch) <= 0x27BF for ch in ui_source)
checks['no_em_or_en_dashes_in_ui']=chr(0x2014) not in ui_source and chr(0x2013) not in ui_source
def css_block(source, selector):
    start=source.find(selector)
    if start<0: return ''
    end=source.find('}',start)
    return source[start:end+1] if end>=0 else source[start:]
checks['no_pill_shaped_ctas']=all('border-radius: 999px' not in css_block(styles, selector) for selector in ('.nav-pill', '.sign-in-btn', '.cta-btn', '.mobile-sign-in'))
checks['user_entry_has_auth_gate']='ema:auth-verified' in idx and 'await video.play()' in idx
checks['user_entry_not_autoplay_before_auth']='preload="none"' in idx and not ('autoplay' in idx[idx.find('<video'):idx.find('</video>')])
checks['admin_entry_hidden']='id="entry" hidden' in adm
checks['admin_entry_not_autoplay']='autoplay' not in adm[adm.find('<video'):adm.find('</video>')]
checks['admin_entry_starts_after_auth']='playEntry();await bootAfterAuth()' in adjs and 'hideLogin();showShell();playEntry();return true' in adjs
checks['attachment_links_require_safe_http_urls']="function safeHttpUrl(value)" in idx and 'href="${escapeHtml(safeUrl)}"' in idx and 'rel="noopener noreferrer"' in idx
checks['calendar_event_handlers_are_not_inline']='onclick="onEventCardClick(' not in idx and "onclick="+'"'+"event.stopPropagation(); openEventDetailModal(" not in idx and 'data-event-card-id=' in idx and 'data-event-detail-id=' in idx
checks['external_calendar_link_is_validated']='const calendarLink = safeHttpUrl(ev.htmlLink)' in idx and 'openCalendarButton.rel = \'noopener noreferrer\'' in idx
checks['account_controls_avoid_dynamic_inline_js']="onclick=\"toggleAccount('${acc.email}'" not in idx and "onclick=\"signOutSpecificAccount('${acc.email}'" not in idx and 'data-toggle-email=' in idx and 'data-disconnect-email=' in idx
checks['calendar_select_escapes_email_attributes']='option value="${escapeHtml(a.email)}"' in idx
checks['no_duplicate_js_session_cookie']='document.cookie' not in idx and 'document.cookie' not in login
checks['admin_cookie_local_http_safe']='def _admin_cookie_secure(request: Request)' in main and 'if scheme != "https":\n        return False' in main
checks['expired_session_not_reactivated']='if not is_user_session_valid(sid):' in main and 'return []' in main[main.find('def get_accounts_for_session'):main.find('def verify_account_ownership')] and 'return is_user_session_valid(clean, db_path)' in st
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
