from __future__ import annotations
import json, os, sys, types, tempfile, pathlib, importlib.util
from datetime import datetime, timedelta, timezone

ROOT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))

# Stub groq so the local structural AI tests can run without a cloud SDK.
groq=types.ModuleType('groq')
class DummyGroq:
    def __init__(self,*a,**k): pass
groq.Groq=DummyGroq
sys.modules['groq']=groq

# Import config and services.
import config
from backend.models.event_schemas import UniversalEmailAnalysis, CalendarEvent
from backend.services import ai_service, calendar_service
from backend.utils import state_tracker

# AI deterministic gates.
spam=ai_service._hard_gate({"category":"Spam / Scam","spam_score":0.9,"events":[{}]},"Buy now","marketing@example.com","", "09:00",0.5, "2026-09-30T10:00:00+05:30", False,"Asia/Kolkata")
assert spam["events"]==[] and spam["is_spam_or_scam"]
no_time=ai_service._hard_gate({"category":"Meeting & Event","events":[{"title":"meeting","start":"2026-10-01T10:00:00+05:30","end":"2026-10-01T11:00:00+05:30"}]},"meeting","person@example.com","","09:00",0.5,"2026-09-30T10:00:00+05:30",False,"Asia/Kolkata")
assert no_time["events"]==[], "events without source temporal evidence must fail closed"
with_time=ai_service._hard_gate({"category":"Meeting & Event","events":[{"title":"meeting","start":"2026-10-01T10:00:00+05:30","end":"2026-10-01T11:00:00+05:30"}]},"meeting tomorrow","person@example.com","","09:00",0.5,"2026-09-30T10:00:00+05:30",False,"Asia/Kolkata")
assert len(with_time["events"])==1

# Meeting provider detection must match actual hostname boundaries, not substrings.
assert ai_service._trusted_meeting_provider('meet.google.com') == 'Google Meet'
assert ai_service._trusted_meeting_provider('us02web.zoom.us') == 'Zoom'
assert ai_service._trusted_meeting_provider('meet.google.com.attacker.example') is None
assert ai_service._trusted_meeting_provider('notzoom.us.attacker.example') is None
assert ai_service._extract_meeting_url('Join at https://meet.google.com/abc-defg-hij') == 'https://meet.google.com/abc-defg-hij'
assert ai_service._extract_meeting_url('Join at https://meet.google.com.attacker.example/abc') is None

# Temporal evidence in the email body must be considered even when the subject is generic.
body_date_only = ai_service._hard_gate(
    {"category":"Meeting & Event","events":[{"title":"Project Call","start":"2026-10-01T10:00:00+05:30","end":"2026-10-01T11:00:00+05:30"}]},
    "Meeting Invitation",
    "organizer@example.com",
    "",
    "09:00",
    0.5,
    "2026-09-30T10:00:00+05:30",
    False,
    "Asia/Kolkata",
    email_body="Please join the project call tomorrow at 10 AM.",
)
assert len(body_date_only["events"]) == 1, "A date/time in the email body was missed by the temporal gate"

# Calendar invalid datetime must reject instead of inventing a future event.
try:
    calendar_service._format_datetime_for_google('not-a-datetime','Asia/Kolkata')
except ValueError:
    pass
else:
    raise AssertionError('Invalid calendar datetime did not fail closed')

# Schema normalization.
ev=CalendarEvent(summary='Test', start='2026-10-01T10:00:00+05:30', guests=[{'email':'a@example.com'}])
assert ev.title=='Test' and ev.end and ev.guests==['a@example.com']

# Date-only values must be actual calendar dates, not just YYYY-MM-DD-shaped strings.
assert calendar_service._format_datetime_for_google('2026-10-01', 'Asia/Kolkata') == {'date': '2026-10-01'}
for bad_date in ('2026-99-99', '2026-02-30'):
    try:
        calendar_service._format_datetime_for_google(bad_date, 'Asia/Kolkata')
    except ValueError:
        pass
    else:
        raise AssertionError(f'Invalid all-day date was accepted: {bad_date}')

# Same title at a materially different time must not be mistaken for a duplicate.
from unittest.mock import MagicMock
calendar_api = MagicMock()
calendar_api.events.return_value.list.return_value.execute.return_value = {
    'items': [{
        'id': 'existing-event',
        'summary': 'Sprint Planning',
        'description': '',
        'location': '',
        'start': {'dateTime': '2026-10-01T11:20:00+05:30'},
        'end': {'dateTime': '2026-10-01T12:20:00+05:30'},
    }]
}
candidate = CalendarEvent(
    title='Sprint Planning',
    start='2026-10-01T10:00:00+05:30',
    end='2026-10-01T11:00:00+05:30',
    timezone='Asia/Kolkata',
)
assert calendar_service.find_duplicate_event(calendar_api, candidate, 'primary') is None, \
    'An event with the same title but a different start time was incorrectly treated as a duplicate'

# Calendar must not create an event when duplicate-check lookup fails.
calendar_lookup_failure = MagicMock()
calendar_lookup_failure.events.return_value.list.return_value.execute.side_effect = RuntimeError('calendar offline')
lookup_candidate = CalendarEvent(
    title='Network Failure Meeting',
    start='2026-10-05T10:00:00+05:30',
    end='2026-10-05T11:00:00+05:30',
    timezone='Asia/Kolkata',
)
lookup_result = calendar_service.create_or_update_event(
    calendar_lookup_failure,
    lookup_candidate,
    calendar_id='primary',
)
assert lookup_result.status == 'failed', "Calendar insertion should fail closed when duplicate lookup fails"
assert not calendar_lookup_failure.events.return_value.insert.called, "Event insert ran without duplicate verification"

# Gmail query failures must not be returned as an empty inbox when the worker asks to retry them.
from backend.services import gmail_service
failing_gmail = MagicMock()
failing_gmail.users.return_value.messages.return_value.list.return_value.execute.side_effect = RuntimeError('offline')
assert gmail_service.get_unprocessed_message_ids(failing_gmail) == []
try:
    gmail_service.get_unprocessed_message_ids(failing_gmail, raise_errors=True)
except RuntimeError:
    pass
else:
    raise AssertionError('raise_errors=True did not propagate Gmail API failures')

# Startup database initialization must preserve legacy email history tables.
with tempfile.TemporaryDirectory() as temp_dir:
    test_db = pathlib.Path(temp_dir) / 'history.db'
    state_tracker.init_db(test_db, force=True)
    conn = state_tracker.get_db_connection(test_db)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS email_actions (email_id TEXT PRIMARY KEY, action TEXT)')
        conn.execute('INSERT INTO email_actions(email_id, action) VALUES (?, ?)', ('preserve-me', 'ACTIONED'))
        conn.commit()
    finally:
        conn.close()
    state_tracker.init_db(test_db, force=True)
    conn = state_tracker.get_db_connection(test_db)
    try:
        rows = conn.execute('SELECT email_id, action FROM email_actions').fetchall()
        assert len(rows) == 1 and rows[0]['email_id'] == 'preserve-me', \
            'init_db unexpectedly removed existing email history'
    finally:
        conn.close()

# A global legacy refresh token must never be borrowed by another account.
from backend.services import google_auth
with tempfile.TemporaryDirectory() as temp_dir:
    legacy_token_path = pathlib.Path(temp_dir) / 'token.json'
    legacy_token_path.write_text(json.dumps({'refresh_token': 'belongs-to-account-a'}), encoding='utf-8')
    old_get_account = google_auth.get_google_account
    old_token_path = google_auth.TOKEN_PATH
    old_credentials = google_auth.Credentials
    captured = {}
    class CapturingCredentials:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.refresh_token = kwargs.get('refresh_token')
            self.expired = True
            self.valid = False
        def refresh(self, request):
            raise AssertionError('refresh should not run without this account\'s refresh token')
    try:
        google_auth.get_google_account = lambda email: {
            'email': 'account-b@example.com',
            'name': 'Account B',
            'picture': '',
            'token_data': json.dumps({'token': 'account-b-access-token'}),
            'session_id': 'session-account-b-123456789',
            'is_monitored': 1,
        }
        google_auth.TOKEN_PATH = legacy_token_path
        google_auth.Credentials = CapturingCredentials
        assert google_auth.get_credentials_for_account('account-b@example.com') is None
        assert captured.get('refresh_token') is None, 'A refresh token from the global legacy file was assigned to another account'
        assert google_auth.is_reauth_needed('account-b@example.com')
    finally:
        google_auth.get_google_account = old_get_account
        google_auth.TOKEN_PATH = old_token_path
        google_auth.Credentials = old_credentials
        google_auth.clear_account_reauth('account-b@example.com')

# Local state shadows must not collide when distinct emails normalize to the same filename.
from backend.services import drive_state_adapter
with tempfile.TemporaryDirectory() as temp_dir:
    old_base_dir = drive_state_adapter.BASE_DIR
    first_email = 'user.name@example.com'
    second_email = 'user_name@example.com'
    try:
        drive_state_adapter.BASE_DIR = pathlib.Path(temp_dir)
        assert drive_state_adapter._get_shadow_path(first_email) != drive_state_adapter._get_shadow_path(second_email)
        legacy_path = drive_state_adapter._get_legacy_shadow_path(first_email)
        legacy_payload = {
            'settings': {'spam_threshold': '35'},
            'email_actions': [{'email_id': 'one', 'account_email': first_email, 'status': 'PROCESSED'}],
        }
        # Legacy data without an owner marker is ambiguous even if recent actions
        # appear to match; never trust settings/stats that may have collided.
        legacy_path.write_text(json.dumps(legacy_payload), encoding='utf-8')
        assert drive_state_adapter._load_local_shadow(first_email) is None, 'Ambiguous legacy shadow was trusted'
        legacy_payload['_shadow_account_email'] = first_email
        legacy_path.write_text(json.dumps(legacy_payload), encoding='utf-8')
        migrated = drive_state_adapter._load_local_shadow(first_email)
        assert migrated and migrated['settings']['spam_threshold'] == '35', 'Owner-marked legacy shadow did not migrate'
        assert drive_state_adapter._get_shadow_path(first_email).exists(), 'Collision-resistant shadow was not written'
        assert drive_state_adapter._load_local_shadow(second_email) is None, 'Legacy shadow from a different email was loaded'
    finally:
        drive_state_adapter.BASE_DIR = old_base_dir

# Per-session worker preferences survive process restarts and disappear on logout.
with tempfile.TemporaryDirectory() as temp_dir:
    test_db = pathlib.Path(temp_dir) / 'worker-preferences.db'
    state_tracker.init_db(test_db, force=True)
    sid = 'session-worker-test-123456789'
    assert state_tracker.create_user_session(sid, db_path=test_db)
    assert state_tracker.is_user_session_worker_enabled(sid, db_path=test_db) is True
    assert state_tracker.set_user_session_worker_enabled(sid, False, db_path=test_db)
    assert state_tracker.is_user_session_worker_enabled(sid, db_path=test_db) is False
    assert state_tracker.count_active_user_sessions(300, db_path=test_db) == 1
    assert state_tracker.set_user_session_worker_enabled(sid, True, db_path=test_db)
    assert state_tracker.is_user_session_worker_enabled(sid, db_path=test_db) is True
    state_tracker.revoke_user_session(sid, db_path=test_db)
    conn = state_tracker.get_db_connection(test_db)
    try:
        pref_key = f"worker_enabled:{state_tracker._session_hash(sid)}"
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (pref_key,)).fetchone()
        assert row is None, "logout left a persisted worker preference behind"
    finally:
        conn.close()

# Expired sessions linked to accounts must not be recreated by legacy migration.
with tempfile.TemporaryDirectory() as temp_dir:
    test_db = pathlib.Path(temp_dir) / 'expired-session.db'
    state_tracker.init_db(test_db, force=True)
    sid = 'session-expired-regression-123456789'
    assert state_tracker.create_user_session(sid, db_path=test_db)
    state_tracker.upsert_google_account(
        email='expiry@example.com',
        name='Expiry test',
        picture='',
        token_data='',
        session_id=sid,
        db_path=test_db,
    )
    conn = state_tracker.get_db_connection(test_db)
    try:
        past = (datetime.now(timezone.utc) - timedelta(seconds=60)).replace(tzinfo=None).isoformat()
        conn.execute(
            'UPDATE user_sessions SET expires_at = ? WHERE session_hash = ?',
            (past, state_tracker._session_hash(sid)),
        )
        conn.commit()
    finally:
        conn.close()
    assert not state_tracker.is_user_session_valid(sid, db_path=test_db)
    assert not state_tracker.ensure_legacy_session(sid, db_path=test_db), 'Expired session was revived by legacy migration'
    state_tracker.revoke_user_session(sid, db_path=test_db)
    assert not state_tracker.ensure_legacy_session(sid, db_path=test_db), 'Revoked expired session was revived by legacy migration'
    conn = state_tracker.get_db_connection(test_db)
    try:
        row = conn.execute(
            'SELECT expires_at FROM user_sessions WHERE session_hash = ?',
            (state_tracker._session_hash(sid),),
        ).fetchone()
        assert row is not None, 'Expired session marker was deleted while an account still references it'
        assert datetime.fromisoformat(row['expires_at']) <= datetime.now(timezone.utc).replace(tzinfo=None)
    finally:
        conn.close()

# Token encryption round trip with an isolated temporary key.
try:
    from cryptography.fernet import Fernet
    old_key=config.TOKEN_ENCRYPTION_KEY
    state_tracker.config.TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()
    state_tracker.config.TOKEN_ENCRYPTION_REQUIRED=True
    enc=state_tracker._encrypt_token_data('{"refresh_token":"secret"}')
    dec=state_tracker._decrypt_token_data(enc)
    assert enc.startswith('enc:v1:') and dec=='{"refresh_token":"secret"}'
    state_tracker.config.TOKEN_ENCRYPTION_KEY=old_key
except ImportError:
    pass

print('PHASE1_SELFTEST_OK')
print('models=', UniversalEmailAnalysis.__name__, CalendarEvent.__name__)
print('groq_primary=', config.GROQ_MODEL)
print('groq_candidates=', config.GROQ_MODEL_CANDIDATES)
print('verifier=', config.AI_VERIFIER_MODEL)
print('token_encryption_required_default=', config.TOKEN_ENCRYPTION_REQUIRED)
