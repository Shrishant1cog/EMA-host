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

# Public launch and design guardrails for EMA 2.0.1.
privacy = F/'privacy.html'
terms = F/'terms.html'
uxjs = (F/'ux-pro.js').read_text(encoding='utf-8')
public_pages = {
    'dashboard': idx,
    'login': login,
    'privacy': privacy.read_text(encoding='utf-8'),
    'terms': terms.read_text(encoding='utf-8'),
    'admin': adm,
}
checks['privacy_page_exists'] = privacy.exists()
checks['terms_page_exists'] = terms.exists()
checks['legal_pages_linked_from_dashboard'] = '/privacy.html' in idx and '/terms.html' in idx
checks['legal_pages_linked_from_login'] = '/privacy.html' in login and '/terms.html' in login
checks['favicon_linked_on_all_pages'] = all('/favicon.ico' in page for page in public_pages.values()) and (F/'favicon.ico').is_file()
checks['no_made_with_ai_badge'] = all('made with ai' not in page.lower() for page in public_pages.values())
checks['no_trust_badges_or_fake_testimonials'] = all(
    all(token not in page.lower() for token in ('trust-avatars','trust-row','trusted by','happy customers','customer reviews','★★★★★'))
    for page in public_pages.values()
)
checks['no_cursor_tracking_handlers'] = all(
    token not in (idx + login + adm + adjs + uxjs).lower()
    for token in ('addEventListener("pointermove"','addEventListener("mousemove"','addEventListener(\'pointermove\'','addEventListener(\'mousemove\'','cursor-ripple','cursor-trail')
)
checks['no_injected_marketing_block'] = 'ema-pro-welcome' not in uxjs and '/ux-pro.js' not in idx
checks['no_remote_google_font_dependency'] = all('fonts.googleapis.com' not in page for page in public_pages.values())
checks['responsive_flat_design_overrides'] = 'prefers-reduced-motion:reduce' in idx and 'background-image:none!important' in idx and 'button:focus-visible' in login
admin_css = (F/'admin/admin.css').read_text(encoding='utf-8')
checks['no_invisible_gradient_text'] = 'text-transparent' not in idx and '.login-hero h1 em' in admin_css and '-webkit-text-fill-color:#edf2f7!important' in admin_css
checks['mobile_zoom_not_disabled'] = 'user-scalable=no' not in login and 'viewport-fit=cover' in login
checks['no_fabricated_time_saved_metric'] = '"time_saved"' not in main
checks['dashboard_actioned_metric_labeled_accurately'] = 'Emails Actioned' in idx and 'Events Scheduled' not in idx
checks['dashboard_filtered_metric_labeled_accurately'] = 'Emails Filtered' in idx and 'Files Saved to Drive' not in idx
checks['dashboard_uses_user_worker_preference'] = 'data.worker?.user_enabled' in idx and 'sidebar-worker-badge' in idx and 'currentUserWorkerEnabled' in idx
checks['dashboard_backend_health_is_verified'] = 'refreshConnectionBadge' in idx and 'Service OK' in idx and 'textContent=online?' not in idx
checks['no_calendar_description_emoji'] = '📎' not in (B/'services/calendar_service.py').read_text(encoding='utf-8')
checks['no_emoji_calendar_source_labels'] = '⚡ Scheduled by EMA' not in idx and '📅 Google Calendar' not in idx
checks['admin_runtime_health_is_dynamic'] = 'id="runtime-health-badge"' in adm and "runtimeBadge.textContent=healthy?'HEALTHY':'DEGRADED'" in adjs
checks['admin_counts_not_mislabeled_as_events'] = '<th>Emails actioned</th>' in adm and "detail('Emails actioned'" in adjs and '<th>Events</th>' not in adm

low_power_gate = idx.find('if (lowPower)')
video_load = idx.find('video.load()', low_power_gate if low_power_gate >= 0 else 0)
checks['intro_video_skipped_on_low_resource_devices'] = low_power_gate >= 0 and video_load > low_power_gate and 'navigator.connection?.saveData' in idx

print(json.dumps(checks,indent=2))
failed=[k for k,v in checks.items() if not v]
print('UX_CONTRACT_TEST:', 'PASS' if not failed else 'FAIL')
if failed:
    print('FAILED:', ', '.join(failed)); raise SystemExit(1)
