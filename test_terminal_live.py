import sys
import time
from pathlib import Path

# Ensure root directory is on the import path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from backend.utils.state_tracker import list_all_google_accounts
from backend.services.google_auth import (
    get_google_services_for_account,
    get_drive_service_for_account,
    get_credentials_for_account,
)
from backend.services.gmail_service import (
    get_unprocessed_message_ids,
    fetch_email_details,
)
from backend.services.ai_service import extract_events_from_email
from backend.services.drive_service import get_or_create_evidence_folder


def run_live_terminal_test():
    print("=" * 70)
    print("        EMA LIVE TERMINAL END-TO-END VERIFICATION")
    print("=" * 70)

    # 1. Database & Account Discovery
    print("\n[1/5] Checking connected Google accounts in local database...")
    accounts = list_all_google_accounts()
    if not accounts:
        print("  [!] No Google accounts found in database.")
        print("      Start your server (python -m uvicorn backend.main:app --port 8000)")
        print("      and connect an account via http://localhost:8000/login.html first.")
        return

    print(f"  [+] Found {len(accounts)} registered account(s):")
    for idx, acc in enumerate(accounts, 1):
        monitored = "MONITORED" if acc.get("is_monitored") else "PAUSED"
        print(f"      {idx}. {acc.get('email')} [{monitored}] (Session: {acc.get('session_id', 'None')[:8]}...)")

    target_account = accounts[0]["email"].strip().lower()
    print(f"\n[2/5] Testing credentials & services for: {target_account}")

    # 2. Token & Service Handshake
    try:
        creds = get_credentials_for_account(target_account)
        if not creds:
            print("  [X] Failed: Credentials invalid or expired. Re-auth required.")
            return
        print(f"  [+] OAuth Token Valid: Expires {creds.expiry}")

        gmail_service, calendar_service = get_google_services_for_account(target_account)
        drive_service = get_drive_service_for_account(target_account)
        print("  [+] Gmail, Calendar, and Drive API client services initialized successfully.")
    except Exception as e:
        print(f"  [X] Service handshake failed: {e}")
        return

    # 3. Live Gmail Inbox Scan
    print(f"\n[3/5] Querying Gmail inbox for recent messages...")
    try:
        query = "label:INBOX"
        msg_ids = get_unprocessed_message_ids(gmail_service, query=query, max_results=3)
        if not msg_ids:
            print("  [*] No messages returned from query. Inbox might be empty.")
            sample_email = None
        else:
            print(f"  [+] Retrieved {len(msg_ids)} recent email ID(s): {msg_ids}")
            print(f"      Fetching metadata for message ID: {msg_ids[0]}...")
            sample_email = fetch_email_details(gmail_service, msg_ids[0])
            print(f"      Subject  : {sample_email.get('subject', 'No Subject')}")
            print(f"      Sender   : {sample_email.get('sender', 'Unknown')}")
            print(f"      Date     : {sample_email.get('date_received', 'N/A')}")
            print(f"      Has Files: {sample_email.get('has_attachments', False)}")
    except Exception as e:
        print(f"  [X] Gmail retrieval failed: {e}")
        sample_email = None

    # 4. AI Analysis & Extraction Pipeline
    print(f"\n[4/5] Testing AI Classification Engine...")
    if sample_email:
        test_subject = sample_email.get("subject", "Sync Meeting")
        test_sender = sample_email.get("sender", "team@example.com")
        test_body = sample_email.get("email_body", "")[:400]
        test_att_text = sample_email.get("combined_attachment_text", "")
    else:
        # Fallback synthetic meeting payload if inbox had no unread messages
        test_subject = "Quarterly Architecture Review"
        test_sender = "lead-dev@startup.io"
        test_body = "Hi team, let's meet tomorrow at 3:00 PM for 45 minutes to review our cloud migration plan."
        test_att_text = ""
        print("  [*] Using test meeting payload for AI evaluation.")

    try:
        start_time = time.time()
        analysis = extract_events_from_email(
            subject=test_subject,
            sender=test_sender,
            date_received="",
            email_body=test_body,
            attachment_text=test_att_text,
            custom_prompt="",
            default_time="09:00",
            spam_threshold=0.5,
        )
        elapsed = round(time.time() - start_time, 2)
        print(f"  [+] AI Response received in {elapsed}s:")
        print(f"      Category     : {analysis.category}")
        print(f"      Priority     : {analysis.priority}")
        print(f"      Action Taken : {analysis.action_description}")
        print(f"      Has Event    : {analysis.has_calendar_event}")
        if analysis.events:
            for ev in analysis.events:
                print(f"        -> Event Title : {getattr(ev, 'title', 'Untitled')}")
                print(f"        -> Start Time  : {getattr(ev, 'start_time', 'N/A')}")
                print(f"        -> End Time    : {getattr(ev, 'end_time', 'N/A')}")
    except Exception as e:
        print(f"  [X] AI Extraction failed: {e}")

    # 5. Calendar & Drive Folder Verification
    print(f"\n[5/5] Verifying Calendar & Drive folder access...")
    try:
        # Check upcoming events
        events_res = calendar_service.events().list(
            calendarId=target_account,
            maxResults=3,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        cal_items = events_res.get("items", [])
        print(f"  [+] Google Calendar connection active ({len(cal_items)} upcoming event(s) fetched).")

        # Check Drive folder
        folder_id = get_or_create_evidence_folder(drive_service)
        print(f"  [+] Google Drive Evidence Folder accessible (Folder ID: {folder_id}).")
    except Exception as e:
        print(f"  [X] Calendar/Drive verification failed: {e}")

    print("\n" + "=" * 70)
    print("                TERMINAL LIVE TEST COMPLETE")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    run_live_terminal_test()