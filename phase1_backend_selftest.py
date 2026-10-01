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
