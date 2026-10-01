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
