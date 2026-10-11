from __future__ import annotations
import json, os, sys, types, tempfile, pathlib, importlib.util

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

# Expired legacy session IDs must not be reactivated from an account row.
with tempfile.TemporaryDirectory() as temp_dir:
    test_db = pathlib.Path(temp_dir) / 'session-expiry.db'
    state_tracker.init_db(test_db, force=True)
    state_tracker.upsert_google_account(
        email='legacy@example.com',
        name='Legacy',
        picture='',
        token_data='{}',
        session_id='legacy-session-token',
        db_path=test_db,
    )
    assert not state_tracker.ensure_legacy_session('legacy-session-token', db_path=test_db), \
        'A legacy account row must not revive a session without a valid session record'
    assert not state_tracker.is_user_session_valid('legacy-session-token', db_path=test_db)

# Source-message identity should prevent duplicate creation after a Drive state-save retry.
calendar_api = MagicMock()
calendar_api.events.return_value.list.return_value.execute.return_value = {
    'items': [{
        'id': 'event-from-retried-email',
        'summary': 'Sprint Planning',
        'description': '',
        'location': '',
        'start': {'dateTime': '2026-10-01T10:00:00+05:30'},
        'end': {'dateTime': '2026-10-01T11:00:00+05:30'},
        'extendedProperties': {'private': {'source': 'EmailAutomater', 'source_email_id': 'gmail-message-123'}},
    }]
}
same_event = calendar_service.find_duplicate_event(
    calendar_api, candidate, 'primary', source_email_id='gmail-message-123'
)
assert same_event and same_event.get('id') == 'event-from-retried-email', \
    'An event created from the same Gmail source message must be recognized on retry'

# If duplicate verification is unavailable for a source-aware retry, fail closed.
failing_calendar = MagicMock()
failing_calendar.events.return_value.list.return_value.execute.side_effect = RuntimeError('calendar offline')
try:
    calendar_service.find_duplicate_event(
        failing_calendar, candidate, 'primary', source_email_id='gmail-message-456'
    )
except RuntimeError:
    pass
else:
    raise AssertionError('Calendar duplicate verification failure must not silently permit creation')

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
