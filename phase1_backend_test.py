from pathlib import Path
import ast, json, re, sys, py_compile
ROOT=Path(__file__).resolve().parent
fail=[]
# compile all
for p in ROOT.rglob('*.py'):
    if '__pycache__' in p.parts: continue
    try: py_compile.compile(str(p), doraise=True)
    except Exception as e: fail.append(f'compile {p}: {e}')
# main source checks
main=(ROOT/'backend/main.py').read_text(encoding='utf-8')
checks={
 'worker_start_auth':'def start_worker(request: Request):' in main and 'if not accounts:' in main[main.find('def start_worker(request: Request):'):main.find('def stop_worker(request: Request):')],
 'worker_stop_auth':'def stop_worker(request: Request):' in main and '_set_session_worker_enabled(sid, False)' in main,
 'inspect_owner':'INSPECT_STATE["owner_session"] = sid' in main and 'This inspection belongs to another session' in main,
 'safe_get_logout':'GET is safe' in main and 'delete_google_account' not in main[main.find('def auth_logout_get'):main.find('def auth_logout(request: Request)')],
 'secure_admin':'/api/admin/auth/login' in main and 'httponly=True' in main and 'samesite="strict"' in main,
 'admin_auth_endpoints_present': all(name in main for name in ('def admin_auth_login(', 'def admin_auth_me(', 'def admin_auth_logout(')),
 'admin_ai_status':'/api/admin/ai/status' in main,
 'memory_telemetry':'psutil.Process(os.getpid()).memory_info().rss' in main,
 'cors_narrowed':'allow_methods=["GET", "POST", "OPTIONS"]' in main,
 'production_oauth_guard':'OAUTHLIB_INSECURE_TRANSPORT' in main,
 'oauth_fails_closed':'user_{session_id[:6]}' not in main and 'ui.get("verified_email") is not True' in main,
 'no_destructive_history_drop':'DROP TABLE IF EXISTS email_actions' not in (ROOT/'backend/utils/state_tracker.py').read_text(encoding='utf-8'),
 'worker_start_race_guard':'_WORKER_START_LOCK' in main and 'WORKER_THREAD.is_alive()' in main,
 'worker_restart_on_shutdown_race':'_WORKER_RESTART_REQUESTED' in main and 'target=_background_worker_entry' in main,
 'account_processing_lock':'_EMAIL_PROCESS_LOCKS_GUARD' in main and 'def process_single_email(' in main,
 'calendar_update_preserves_http_errors':'except HTTPException:' in main[main.find('def update_calendar_event'):main.find('class EventDeletePayload')],
 'gmail_query_errors_propagate':'raise_errors=True' in main and 'len(msg_ids) < EMAIL_BATCH_SIZE' in main,
 'stable_initial_backfill_cursor':'set_drive_setting(acc_email, "last_sync_epoch", str(epoch_cutoff))' in main,
 'batch_settings_persist_together':'set_drive_settings' in main,
 'worker_preference_persisted':'set_user_session_worker_enabled' in main and 'is_user_session_worker_enabled' in main,
 'worker_rejects_unscoped_legacy_accounts':'if a.get("session_id") and _session_worker_enabled(a.get("session_id"))' in main,
 'admin_active_sessions_durable':'count_active_user_sessions(window_seconds=300)' in main,
 'admin_health_status_present':'"status": "ok" if ram >= 0 else "degraded"' in main,
 'no_cross_account_refresh_fallback':'legacy_data.get("refresh_token")' not in (ROOT/'backend/services/google_auth.py').read_text(encoding='utf-8'),
 'desktop_oauth_reuses_registered_flow':'reuse_registered_flow' in main and 'registered_flow.get("status") == "pending"' in main,
 'desktop_oauth_same_origin':'local_origin = str(request.base_url).rstrip("/")' in main,
 'desktop_oauth_returns_to_cookie_session':'window.location.replace("/index.html");' in main and 'window.location.replace("/index.html?session_id={sid}")' not in main,
 'launch_browser_reserves_auth_flow':'set_auth_flow(sid, status="pending", redirect_uri=get_dynamic_redirect_uri(request)' in main,
 'worker_ui_enabled_requires_monitored_account':'any(bool(a.get("is_monitored")) for a in session_accs) and _session_worker_enabled(sid)' in main,

}
for k,v in checks.items():
    if not v: fail.append(k)
# AI checks
ai=(ROOT/'backend/services/ai_service.py').read_text(encoding='utf-8')
for key, needle in {
 'ai_120b':'openai/gpt-oss-120b',
 'ai_20b':'openai/gpt-oss-20b',
 'strict_schema':'json_schema',
 'reasoning_supported_mode':'include_reasoning',
 'temporal_gate':'no explicit temporal evidence',
 'injection_defense':'PROMPT-INJECTION FLAG',
}.items():
    if needle not in ai: fail.append(key)
# requirements
req=(ROOT/'requirements.txt').read_text(encoding='utf-8')
if 'groq>=1.7.0,<2.0.0' not in req: fail.append('groq requirement')
# print result
print(json.dumps({'checks':checks,'failures':fail,'status':'PASS' if not fail else 'FAIL'},indent=2))
raise SystemExit(1 if fail else 0)
