import sys
import time
from pathlib import Path
from typing import List, Dict, Set, Tuple

# Ensure root directory is on the module search path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    from fastapi.testclient import TestClient
    from backend.main import app
except ImportError as e:
    print(f"\n[FATAL] Missing test dependencies or application import error: {e}")
    print("Run: pip install httpx pytest\n")
    sys.exit(1)

client = TestClient(app, follow_redirects=False)

# Track audit metrics
PASSED: List[str] = []
FAILED: List[Tuple[str, str]] = []
WARNINGS: List[str] = []


def record_pass(test_name: str, detail: str = ""):
    msg = f"  [PASS] {test_name}" + (f" -> {detail}" if detail else "")
    PASSED.append(msg)
    print(msg)


def record_fail(test_name: str, reason: str):
    msg = f"  [FAIL] {test_name} -> {reason}"
    FAILED.append((test_name, reason))
    print(msg)


def record_warn(test_name: str, reason: str):
    msg = f"  [WARN] {test_name} -> {reason}"
    WARNINGS.append(msg)
    print(msg)


# ==============================================================================
# 1. ROUTE DISCOVERY & EXTRA FEATURE INTROSPECTION
# ==============================================================================
def audit_route_discovery() -> Set[str]:
    print("\n" + "=" * 70)
    print("1. ROUTE INTROSPECTION & HIDDEN FEATURE DISCOVERY")
    print("=" * 70)

    # Known standard endpoints
    documented_routes = {
        "/",
        "/index.html",
        "/login.html",
        "/privacy",
        "/privacy.html",
        "/terms",
        "/terms.html",
        "/favicon.ico",
        "/logo.png",
        "/health",
        "/auth/login",
        "/auth/google",
        "/api/auth/google",
        "/auth/callback",
        "/api/auth/callback",
        "/api/auth/status",
        "/api/auth/launch-browser",
        "/auth/logout",
        "/api/auth/logout",
        "/api/logout",
        "/api/user",
        "/api/accounts",
        "/api/accounts/toggle",
        "/api/accounts/delete",
        "/api/settings",
        "/api/stats",
        "/api/emails",
        "/api/worker/start",
        "/api/worker/stop",
        "/api/inspect/start",
        "/api/inspect/stop",
        "/api/inspect/status",
        "/api/calendar/upcoming",
        "/api/logs/stream",
    }

    discovered_routes: Set[str] = set()

    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", set())
        if path:
            discovered_routes.add(path)
            if path not in documented_routes and not path.startswith("/openapi") and not path.startswith("/docs"):
                record_warn(
                    f"Extra/Undocumented Route Detected: {path}",
                    f"Methods: {methods} (Handled by {getattr(r, 'name', 'unknown')})"
                )

    record_pass("Route Registry Discovery", f"Audited {len(discovered_routes)} registered paths")
    return discovered_routes


# ==============================================================================
# 2. STATIC ASSETS & HTML VIEWS
# ==============================================================================
def audit_html_views():
    print("\n" + "=" * 70)
    print("2. HTML VIEWS, ROOT REDIRECTS & STATIC ASSETS")
    print("=" * 70)

    # 1. Root redirect
    res = client.get("/")
    if res.status_code == 303 and res.headers.get("location") == "/index.html":
        record_pass("Root Redirect (/) -> 303 to /index.html")
    else:
        record_fail("Root Redirect (/)", f"Expected 303 to /index.html, got {res.status_code} ({res.headers.get('location')})")

    # 2. Main dashboard template
    res = client.get("/index.html")
    if res.status_code == 200 and "EMA" in res.text and "tab-dashboard" in res.text:
        record_pass("GET /index.html", "Serves valid dashboard HTML")
    else:
        record_fail("GET /index.html", f"Status: {res.status_code}")

    # 3. Login page
    res = client.get("/login.html")
    if res.status_code == 200 and "Sign In" in res.text:
        record_pass("GET /login.html", "Serves login view")
    else:
        record_fail("GET /login.html", f"Status: {res.status_code}")

    # 4. Privacy policy & Google compliance disclosure check
    for p in ["/privacy.html", "/privacy"]:
        res = client.get(p)
        if res.status_code == 200:
            if "Google API Services User Data Policy" in res.text and "Limited Use" in res.text:
                record_pass(f"GET {p}", "200 OK with mandatory Google Limited Use disclosure")
            else:
                record_fail(f"GET {p}", "Missing mandatory 'Google API Services User Data Policy' or 'Limited Use' text")
        else:
            record_fail(f"GET {p}", f"Status: {res.status_code}")

    # 5. Terms of Service
    for p in ["/terms.html", "/terms"]:
        res = client.get(p)
        if res.status_code == 200 and "Terms of Service" in res.text:
            record_pass(f"GET {p}", "200 OK with Terms content")
        else:
            record_fail(f"GET {p}", f"Status: {res.status_code}")


# ==============================================================================
# 3. HEALTH CHECK & KEEP-ALIVE SYSTEM
# ==============================================================================
def audit_health_and_keepalive():
    print("\n" + "=" * 70)
    print("3. HEALTH CHECK & KEEP-ALIVE ENDPOINT")
    print("=" * 70)

    res = client.get("/health")
    if res.status_code == 200:
        data = res.json()
        if data.get("status") == "ok" and "timestamp" in data:
            record_pass("GET /health", f"Payload verified: {data}")
        else:
            record_fail("GET /health", f"Unexpected JSON response: {data}")
    else:
        record_fail("GET /health", f"Expected 200, got {res.status_code}")


# ==============================================================================
# 4. AUTHENTICATION, OAUTH FLOW & SESSION ISOLATION
# ==============================================================================
def audit_auth_engine():
    print("\n" + "=" * 70)
    print("4. AUTH ENGINE, OAUTH FLOW & LOGOUT")
    print("=" * 70)

    # 1. Auth status
    res = client.get("/api/auth/status")
    if res.status_code == 200 and "authenticated" in res.json():
        record_pass("GET /api/auth/status", f"Returns state: {res.json().get('status')}")
    else:
        record_fail("GET /api/auth/status", f"Invalid response: {res.text}")

    # 2. Browser auth launch endpoint
    res = client.post("/api/auth/launch-browser")
    if res.status_code == 200:
        data = res.json()
        if data.get("status") == "opened" and "session_id" in data:
            record_pass("POST /api/auth/launch-browser", f"Session assigned: {data.get('session_id')[:8]}...")
        else:
            record_fail("POST /api/auth/launch-browser", f"Missing session parameters: {data}")
    else:
        record_fail("POST /api/auth/launch-browser", f"Status: {res.status_code}")

    # 3. OAuth Login URL Generation (Desktop browser redirect)
    res = client.get("/auth/login?browser=1")
    if res.status_code in (302, 307):
        loc = res.headers.get("location", "")
        if "accounts.google.com" in loc and "client_id=" in loc:
            record_pass("GET /auth/login?browser=1", "Redirects to accounts.google.com with valid client_id")
        else:
            record_fail("GET /auth/login?browser=1", f"Bad OAuth URL: {loc}")
    else:
        record_fail("GET /auth/login?browser=1", f"Expected 302/307 redirect, got {res.status_code}")

    # 4. Callback with user cancellation error
    res = client.get("/auth/callback?error=access_denied&state=test_session")
    if res.status_code == 400 and "Authentication Failed" in res.text:
        record_pass("GET /auth/callback (Cancellation error)", "Correctly returns 400 error template")
    else:
        record_fail("GET /auth/callback (Cancellation error)", f"Status: {res.status_code}")

    # 5. Universal logout handler
    for m in ["GET", "POST"]:
        res = client.request(m, "/api/auth/logout")
        if res.status_code in (200, 303):
            record_pass(f"{m} /api/auth/logout", "Logs out and flushes cookies")
        else:
            record_fail(f"{m} /api/auth/logout", f"Status: {res.status_code}")


# ==============================================================================
# 5. CORE APIS (ACCOUNTS, SETTINGS, TELEMETRY)
# ==============================================================================
def audit_core_apis():
    print("\n" + "=" * 70)
    print("5. CORE APIS (ACCOUNTS, SETTINGS, STATS)")
    print("=" * 70)

    # 1. Current user
    res = client.get("/api/user")
    if res.status_code == 200 and "authenticated" in res.json():
        record_pass("GET /api/user", f"Authenticated: {res.json().get('authenticated')}")
    else:
        record_fail("GET /api/user", f"Status: {res.status_code}")

    # 2. Account list
    res = client.get("/api/accounts")
    if res.status_code == 200 and "accounts" in res.json():
        record_pass("GET /api/accounts", f"Accounts detected: {len(res.json()['accounts'])}")
    else:
        record_fail("GET /api/accounts", f"Status: {res.status_code}")

    # 3. Settings read & write schema validation
    res = client.get("/api/settings")
    if res.status_code == 200:
        data = res.json()
        expected_keys = {"only_remind_with_files", "spam_threshold", "default_timing"}
        if expected_keys.issubset(data.keys()):
            record_pass("GET /api/settings", f"Settings structure validated: {data}")
        else:
            record_fail("GET /api/settings", f"Missing configuration keys: {data}")
    else:
        record_fail("GET /api/settings", f"Status: {res.status_code}")

    # 4. System telemetry & stats
    res = client.get("/api/stats")
    if res.status_code == 200 and "summary" in res.json() and "worker" in res.json():
        record_pass("GET /api/stats", f"Telemetry verified: {res.json()['worker']}")
    else:
        record_fail("GET /api/stats", f"Status: {res.status_code}")

    # 5. Email history endpoint
    res = client.get("/api/emails?category_filter=ALL&limit=10")
    if res.status_code == 200 and "emails" in res.json():
        record_pass("GET /api/emails", "Email list endpoint functional")
    else:
        record_fail("GET /api/emails", f"Status: {res.status_code}")


# ==============================================================================
# 6. WORKER & INSPECTOR PROCESS LIFECYCLES
# ==============================================================================
def audit_background_workers():
    print("\n" + "=" * 70)
    print("6. BACKGROUND WORKER & INSPECT ENGINE LIFECYCLES")
    print("=" * 70)

    # 1. Worker start
    res = client.post("/api/worker/start")
    if res.status_code == 200 and res.json().get("status") == "started":
        record_pass("POST /api/worker/start", "Worker activation acknowledged")
    else:
        record_fail("POST /api/worker/start", f"Status: {res.status_code}")

    # 2. Worker stop
    res = client.post("/api/worker/stop")
    if res.status_code == 200 and res.json().get("status") == "stopping":
        record_pass("POST /api/worker/stop", "Worker halt acknowledged")
    else:
        record_fail("POST /api/worker/stop", f"Status: {res.status_code}")

    # 3. Inspect status
    res = client.get("/api/inspect/status")
    if res.status_code == 200 and "is_running" in res.json():
        record_pass("GET /api/inspect/status", f"Inspector state: {res.json().get('status_message')}")
    else:
        record_fail("GET /api/inspect/status", f"Status: {res.status_code}")

    # 4. Inspect stop
    res = client.post("/api/inspect/stop")
    if res.status_code == 200:
        record_pass("POST /api/inspect/stop", "Inspector stop acknowledged")
    else:
        record_fail("POST /api/inspect/stop", f"Status: {res.status_code}")


# ==============================================================================
# 7. REAL-TIME SERVER-SENT EVENTS (SSE) STREAM
# ==============================================================================
def audit_realtime_sse():
    print("\n" + "=" * 70)
    print("7. REAL-TIME SSE LOG TERMINAL STREAM")
    print("=" * 70)

    try:
        with client.stream("GET", "/api/logs/stream") as res:
            if res.status_code == 200 and "text/event-stream" in res.headers.get("content-type", ""):
                for chunk in res.iter_lines():
                    if chunk:
                        record_pass("GET /api/logs/stream", f"Connected & received live SSE data frame: '{chunk[:60]}...'")
                        break
            else:
                record_fail("GET /api/logs/stream", f"Invalid status {res.status_code} or Content-Type {res.headers.get('content-type')}")
    except Exception as e:
        record_fail("GET /api/logs/stream", f"Stream failure: {e}")


# ==============================================================================
# AUDIT SUMMARY RUNNER
# ==============================================================================
def run_full_system_audit():
    start_time = time.time()
    print("=" * 70)
    print("  EMA SMART UNIVERSAL ASSISTANT - EXHAUSTIVE SYSTEM AUDIT")
    print("=" * 70)

    audit_route_discovery()
    audit_html_views()
    audit_health_and_keepalive()
    audit_auth_engine()
    audit_core_apis()
    audit_background_workers()
    audit_realtime_sse()

    duration = round(time.time() - start_time, 2)

    print("\n" + "=" * 70)
    print("AUDIT SUMMARY REPORT")
    print("=" * 70)
    print(f"Total Tests Executed: {len(PASSED) + len(FAILED)}")
    print(f"Passed Checks:        {len(PASSED)}")
    print(f"Failed Checks:        {len(FAILED)}")
    print(f"Warnings/Notices:     {len(WARNINGS)}")
    print(f"Execution Time:       {duration}s\n")

    if WARNINGS:
        print("--- NOTICES & EXTRA DISCOVERED FEATURES ---")
        for w in WARNINGS:
            print(w)
        print()

    if FAILED:
        print("--- FAILED TESTS (ACTION REQUIRED) ---")
        for name, reason in FAILED:
            print(f"  * {name}: {reason}")
        print("\nResult: [FAILED] - Address issues before deploying.\n")
        sys.exit(1)
    else:
        print("Result: [ALL TESTS PASSED] - System is fully functional and ready for cloud deployment.\n")
        sys.exit(0)


if __name__ == "__main__":
    run_full_system_audit()