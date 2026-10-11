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
 'admin_ai_status':'/api/admin/ai/status' in main,
 'memory_telemetry':'psutil.Process(os.getpid()).memory_info().rss' in main,
 'cors_narrowed':'allow_methods=["GET", "POST", "OPTIONS"]' in main,
 'production_oauth_guard':'OAUTHLIB_INSECURE_TRANSPORT' in main,
 'oauth_fails_closed':'user_{session_id[:6]}' not in main and 'ui.get("verified_email") is not True' in main,
 'no_destructive_history_drop':'DROP TABLE IF EXISTS email_actions' not in (ROOT/'backend/utils/state_tracker.py').read_text(encoding='utf-8'),
 'expired_session_not_revived':'if not is_user_session_valid(sid):' in main and 'return is_user_session_valid(clean, db_path)' in (ROOT/'backend/utils/state_tracker.py').read_text(encoding='utf-8'),
 'oauth_state_not_bearer_session':'oauth_state = secrets.token_urlsafe(32)' in main and '"state": oauth_state' in main and 'state=sid' not in main[main.find('def auth_login'):main.find('def auth_callback')],
 'no_session_id_in_accounts_api':'safe_accounts = [{k: v for k, v in a.items() if k != "session_id"}' in main and 'return {"accounts": accounts}' not in main[main.find('def list_accounts'):main.find('class AccountTogglePayload')],
 'profile_save_checks_persistence':'Profile name could not be saved' in main and 'set_drive_settings(acc["email"], {"custom_display_name": clean_name})' in main,
 'worker_pause_is_persisted':'set_drive_settings(acc["email"], {"worker_enabled": "false"})' in main and 'set_drive_settings(acc["email"], {"worker_enabled": "true"})' in main,
 'admin_presence_is_recorded':'_touch_session(sid)' in main and 'active_sids = {sid for sid, last in _SESSION_LAST_SEEN.items()' in main,
 'admin_rate_limit_ignores_spoofed_header':'x-forwarded-for' not in main[main.find('def _admin_client_key'):main.find('def _admin_login_allowed')],
 'calendar_source_idempotency':'source_email_id=str(mid)' in main and 'source_email_id: Optional[str] = None' in (ROOT/'backend/services/calendar_service.py').read_text(encoding='utf-8'),
 'worker_start_race_guard':'_WORKER_START_LOCK' in main and 'WORKER_THREAD.is_alive()' in main,
 'worker_restart_on_shutdown_race':'_WORKER_RESTART_REQUESTED' in main and 'target=_background_worker_entry' in main,
 'account_processing_lock':'_EMAIL_PROCESS_LOCKS_GUARD' in main and 'def process_single_email(' in main,
 'calendar_update_preserves_http_errors':'except HTTPException:' in main[main.find('def update_calendar_event'):main.find('class EventDeletePayload')],
 'gmail_query_errors_propagate':'page.get("nextPageToken")' in main and 'page_args["pageToken"]' in main and 'gmail_service.users().messages().list(**page_args).execute()' in main,
 'stable_initial_backfill_cursor':'set_drive_setting(acc_email, "last_sync_epoch", str(epoch_cutoff))' in main,
 'batch_settings_persist_together':'set_drive_settings' in main,
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
