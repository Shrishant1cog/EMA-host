import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dateutil import parser

import config
from backend.models.event_schemas import CalendarEvent

logger = logging.getLogger("CalendarService")


class CalendarResult(dict):
    """Dictionary subclass supporting both dot-attribute and dictionary key access."""

    def __getattr__(self, name):
        if name in self:
            return self[name]
        for k, v in self.items():
            if k.lower() == name.lower():
                return v
        return None

    def __getitem__(self, key):
        if key in self:
            return super().__getitem__(key)
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return None


# ----------------- Timezone & Datetime Utilities ----------------- #

def _get_safe_zoneinfo(tz_name: Optional[str] = None) -> ZoneInfo:
    """Returns a valid ZoneInfo instance with tiered fallback to prevent crashes."""
    candidates = [
        tz_name,
        getattr(config, "USER_TIMEZONE", "Asia/Kolkata"),
        "Asia/Kolkata",
        "UTC",
    ]
    for c in candidates:
        if c and isinstance(c, str):
            try:
                return ZoneInfo(c.strip())
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def _normalize_to_calendar_event(
    event: Union[CalendarEvent, Dict[str, Any], Any]
) -> CalendarEvent:
    """Normalizes dictionaries or generic model instances into a typed CalendarEvent."""
    if isinstance(event, CalendarEvent):
        return event

    default_tz = getattr(config, "USER_TIMEZONE", "Asia/Kolkata")

    if isinstance(event, dict):
        title = (
            event.get("title")
            or event.get("summary")
            or event.get("name")
            or "New Scheduled Event"
        )
        desc = event.get("description", "")
        start = str(event.get("start", ""))
        end = str(event.get("end") or start)
        tz = event.get("timezone") or default_tz
        loc = event.get("location")
        m_url = (
            event.get("meeting_url")
            or event.get("meeting_link")
            or event.get("url")
        )
        org = event.get("organizer")
        all_day = bool(event.get("all_day", False) or event.get("is_all_day", False))
        guests = event.get("guests", []) or event.get("attendees", [])
        return CalendarEvent(
            title=title,
            description=desc,
            start=start,
            end=end,
            timezone=tz,
            location=loc,
            meeting_url=m_url,
            organizer=org,
            all_day=all_day,
            guests=guests,
        )

    return CalendarEvent(
        title=getattr(event, "title", None)
        or getattr(event, "summary", "New Scheduled Event"),
        description=getattr(event, "description", ""),
        start=str(getattr(event, "start", "")),
        end=str(getattr(event, "end", "") or getattr(event, "start", "")),
        timezone=getattr(event, "timezone", default_tz),
        location=getattr(event, "location", None),
        meeting_url=getattr(event, "meeting_url", None)
        or getattr(event, "meeting_link", None),
        organizer=getattr(event, "organizer", None),
        all_day=bool(getattr(event, "all_day", False) or getattr(event, "is_all_day", False)),
        guests=getattr(event, "guests", []) or getattr(event, "attendees", []),
    )


def _format_datetime_for_google(
    dt_str: str, timezone_str: str = "Asia/Kolkata"
) -> Dict[str, str]:
    """Converts a datetime string to an RFC 3339 payload expected by Google Calendar."""
    clean = str(dt_str or "").strip()
    safe_zone = _get_safe_zoneinfo(timezone_str)

    # Date-only format (All-Day Event). Validate the actual calendar date,
    # not merely the string shape (e.g. reject 2026-99-99).
    if len(clean) == 10 and clean.count("-") == 2 and "T" not in clean:
        try:
            datetime.strptime(clean, "%Y-%m-%d")
            return {"date": clean}
        except ValueError as exc:
            raise ValueError(f"Invalid calendar date: {dt_str!r}") from exc

    try:
        clean_iso = clean.replace("Z", "+00:00")
        dt = parser.isoparse(clean_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=safe_zone)
        return {"dateTime": dt.isoformat(), "timeZone": str(safe_zone.key)}
    except Exception as exc:
        raise ValueError(f"Invalid calendar datetime: {dt_str!r}") from exc


# ----------------- Duplicate Reconciliation ----------------- #

def find_duplicate_event(
    calendar_service,
    event: Union[CalendarEvent, Dict[str, Any], Any],
    calendar_id: str = "primary",
    source_email_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find the same scheduled occurrence, using Gmail source ID when available."""
    if not event or not calendar_service:
        return None

    ev = _normalize_to_calendar_event(event)
    if not ev.start:
        return None

    tz = _get_safe_zoneinfo(ev.timezone)
    start_clean = str(ev.start).replace("Z", "+00:00")
    try:
        if len(start_clean) == 10 and start_clean.count("-") == 2 and "T" not in start_clean:
            start_dt = datetime.fromisoformat(start_clean).replace(hour=9, minute=0, tzinfo=tz)
        else:
            start_dt = parser.isoparse(start_clean)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=tz)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Cannot check duplicate for invalid event start: {ev.start!r}") from exc

    time_min = (start_dt - timedelta(minutes=90)).isoformat()
    time_max = (start_dt + timedelta(minutes=90)).isoformat()
    target_title = (ev.title or "").strip().casefold()
    target_url = (ev.meeting_url or "").strip().casefold()
    source_id = str(source_email_id or "").strip()

    def event_start(existing_event: Dict[str, Any]) -> Optional[datetime]:
        start_value = existing_event.get("start", {}) or {}
        raw = start_value.get("dateTime") or start_value.get("date")
        if not raw:
            return None
        try:
            if len(raw) == 10 and "T" not in raw:
                return datetime.fromisoformat(raw).replace(hour=9, minute=0, tzinfo=tz)
            parsed = parser.isoparse(raw.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
        except (TypeError, ValueError, OverflowError):
            return None

    def close_start(existing: Dict[str, Any]) -> bool:
        candidate = event_start(existing)
        if candidate is None:
            return False
        try:
            return abs((candidate - start_dt).total_seconds()) <= 10 * 60
        except (TypeError, ValueError, OverflowError):
            return False

    items: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    try:
        # Calendar may return a page token even for a narrow window. Continue through
        # every page (bounded) so a busy calendar doesn't hide the matching occurrence.
        for _ in range(5):
            params: Dict[str, Any] = {
                "calendarId": calendar_id,
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": 2500,
                "singleEvents": True,
                "orderBy": "startTime",
                "fields": "nextPageToken,items(id,summary,description,location,start,end,htmlLink,extendedProperties)",
            }
            if page_token:
                params["pageToken"] = page_token
            result = calendar_service.events().list(**params).execute()
            items.extend(result.get("items", []) or [])
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        if page_token and source_id:
            # Do not create a potentially duplicate event if we couldn't finish the scan.
            raise RuntimeError("Calendar duplicate scan exceeded its safe page limit.")

        for existing in items:
            existing_title = (existing.get("summary") or "").strip().casefold()
            existing_desc = (existing.get("description") or "").casefold()
            existing_loc = (existing.get("location") or "").casefold()

            # Meeting URLs are often reused for recurring events, so require the same
            # occurrence time instead of treating the URL alone as globally unique.
            if target_url and close_start(existing) and (
                target_url in existing_desc or target_url in existing_loc
            ):
                return existing

            private_props = ((existing.get("extendedProperties") or {}).get("private") or {})
            existing_source_id = str(private_props.get("source_email_id") or "").strip()
            if (
                source_id
                and existing_source_id == source_id
                and existing_title == target_title
                and close_start(existing)
            ):
                return existing

            # Legacy events did not store a source email ID; retain conservative
            # exact-title/time matching for those.
            if target_title and target_title == existing_title and close_start(existing):
                return existing

        return None
    except Exception as exc:
        logger.warning("Duplicate reconciliation failed on %s: %s", calendar_id, exc)
        if source_id:
            # Creating after a failed lookup can duplicate an event whose insert
            # succeeded before a previous state-save failure. Fail closed on retries.
            raise RuntimeError("Calendar duplicate verification is unavailable; event creation deferred.") from exc
        return None


# ----------------- Event Creation Pipeline ----------------- #

def create_or_update_event(
    calendar_service,
    event_data: Union[CalendarEvent, Dict[str, Any], Any],
    original_sender: str = "",
    attachments: Optional[List[Dict[str, Any]]] = None,
    calendar_id: str = "primary",
    target_account_email: Optional[str] = None,
    source_email_id: Optional[str] = None,
) -> CalendarResult:
    """
    Creates a calendar event strictly in the target account's calendar.
    Includes an automatic fallback that strips Google Drive attachments if
    the Calendar API rejects the file attachments with a 400/403 error.
    """
    ev = _normalize_to_calendar_event(event_data)
    tz_str = getattr(config, "USER_TIMEZONE", ev.timezone or "Asia/Kolkata")
    resolved_calendar_id = (target_account_email or calendar_id).strip().lower()

    # 1. Skip creating duplicate if matching event exists
    existing = find_duplicate_event(
        calendar_service, ev, resolved_calendar_id, source_email_id=source_email_id
    )
    if existing:
        logger.info(
            f"[{resolved_calendar_id}] Skipped duplicate event '{ev.title}' (ID: {existing.get('id')})."
        )
        return CalendarResult(
            event_id=existing.get("id"),
            html_link=existing.get("htmlLink", ""),
            status="duplicate_skipped",
            summary=existing.get("summary", ev.title),
        )

    # 2. Build start and end payloads. Invalid times fail closed; never invent tomorrow.
    try:
        start_raw = str(ev.start or "").strip()
        end_raw = str(ev.end or ev.start or "").strip()
        start_is_date = len(start_raw) == 10 and "T" not in start_raw
        end_is_date = len(end_raw) == 10 and "T" not in end_raw
        if ev.all_day or (start_is_date and end_is_date):
            start_date = start_raw.split("T")[0]
            end_date = end_raw.split("T")[0]
            # Validate dates and preserve Google's exclusive all-day end semantics.
            dt_start_date = datetime.strptime(start_date, "%Y-%m-%d")
            dt_end_date = datetime.strptime(end_date, "%Y-%m-%d")
            if dt_end_date < dt_start_date:
                raise ValueError("All-day event end date must not precede its start date.")
            if dt_start_date == dt_end_date:
                end_date = (dt_start_date + timedelta(days=1)).strftime("%Y-%m-%d")
            start_payload = {"date": start_date}
            end_payload = {"date": end_date}
        else:
            if start_is_date or end_is_date:
                raise ValueError("Start and end must both be dates for all-day events or both be datetimes.")
            start_payload = _format_datetime_for_google(start_raw, tz_str)
            end_payload = _format_datetime_for_google(end_raw, tz_str)
            if "dateTime" not in start_payload or "dateTime" not in end_payload:
                raise ValueError("Timed events require start and end datetimes.")
            dt_start = parser.isoparse(start_payload["dateTime"])
            dt_end = parser.isoparse(end_payload["dateTime"])
            if dt_end <= dt_start:
                return CalendarResult(
                    event_id=None,
                    html_link="",
                    status="failed",
                    error="End time must be later than start time.",
                )
    except Exception as exc:
        logger.error(f"[{resolved_calendar_id}] Rejected invalid event '{ev.title}': {exc}")
        return CalendarResult(event_id=None, html_link="", status="failed", error=str(exc))

    # 3. Assemble Description & Source Attributions
    description_lines = []
    if ev.description:
        description_lines.append(ev.description.strip())

    source_label = original_sender if original_sender else "Smart Universal Email Assistant"
    description_lines.append(
        f"\n---\nAuto-scheduled by Smart Universal Email Assistant\nSource: {source_label}\nAccount: {resolved_calendar_id}"
    )

    if ev.organizer:
        description_lines.append(f"Organizer: {ev.organizer}")
    if ev.meeting_url:
        description_lines.append(f"Meeting URL: {ev.meeting_url}")

    # 4. Format Attachments
    drive_attachments = []
    if attachments:
        description_lines.append("\nAttached evidence and documents:")
        for att in attachments:
            name = att.get("name") or att.get("title") or att.get("filename") or "Document"
            file_id = att.get("id") or att.get("fileId")
            url = (
                att.get("webViewLink")
                or att.get("fileUrl")
                or att.get("url")
                or att.get("alternateLink")
                or att.get("webContentLink")
                or (f"https://drive.google.com/file/d/{file_id}/view" if file_id else "")
            )
            mime_type = att.get("mimeType") or att.get("mime_type") or "application/octet-stream"

            if url:
                description_lines.append(f"• {name}: {url}")
            else:
                description_lines.append(f"• {name}")

            if file_id and url:
                drive_attachments.append(
                    {
                        "fileUrl": url,
                        "title": name,
                        "mimeType": mime_type,
                        "fileId": file_id,
                    }
                )

    loc = ev.location or ev.meeting_url

    event_body: Dict[str, Any] = {
        "summary": ev.title,
        "description": "\n".join(description_lines).strip(),
        "start": start_payload,
        "end": end_payload,
        "extendedProperties": {
            "private": {
                "source": "EmailAutomater",
                "scheduled_by": "SmartUniversalEmailAssistant",
                "account_email": resolved_calendar_id,
                **({"source_email_id": str(source_email_id).strip()} if source_email_id else {}),
            }
        },
        "reminders": {
            "useDefault": False,
            "overrides": [],
        },
    }

    # Honor validated AI reminder instructions when present; otherwise use a conservative default.
    reminder_overrides = []
    for reminder in (getattr(ev, "reminders", None) or []):
        try:
            method = str(reminder.get("method", "popup")).lower()
            minutes = int(reminder.get("minutes_before", 15))
            if method in {"popup", "email"} and 0 <= minutes <= 10080:
                reminder_overrides.append({"method": method, "minutes": minutes})
        except (TypeError, ValueError, AttributeError):
            continue
    event_body["reminders"]["overrides"] = reminder_overrides[:5] or [{"method": "popup", "minutes": 15}]

    recurrence = getattr(ev, "recurrence_rule", None)
    if recurrence:
        recurrence_text = str(recurrence).strip()
        if recurrence_text and not recurrence_text.upper().startswith(("RRULE:", "EXDATE:", "RDATE:")):
            recurrence_text = "RRULE:" + recurrence_text
        if recurrence_text.startswith("RRULE:"):
            event_body["recurrence"] = [recurrence_text]

    if loc:
        event_body["location"] = loc

    # Filter out self-invites and duplicates from attendees list
    if ev.guests:
        clean_attendees = set()
        for g in ev.guests:
            clean_g = str(g).strip().lower()
            if "@" in clean_g and clean_g != resolved_calendar_id:
                clean_attendees.add(clean_g)
        if clean_attendees:
            event_body["attendees"] = [{"email": email} for email in sorted(clean_attendees)]

    if drive_attachments:
        event_body["attachments"] = drive_attachments

    # 5. Insert Event with Automatic Attachment Fallback
    try:
        created_event = (
            calendar_service.events()
            .insert(
                calendarId=resolved_calendar_id,
                body=event_body,
                supportsAttachments=bool(drive_attachments),
            )
            .execute()
        )

        logger.info(
            f"[{resolved_calendar_id}] Created event '{ev.title}'. Link: {created_event.get('htmlLink')}"
        )
        return CalendarResult(
            event_id=created_event.get("id"),
            html_link=created_event.get("htmlLink", ""),
            status="created",
            summary=created_event.get("summary"),
        )

    except Exception as primary_err:
        err_str = str(primary_err)
        # If rejected due to attachment permissions, retry without body["attachments"]
        if drive_attachments and ("attachments" in err_str.lower() or "400" in err_str or "403" in err_str):
            logger.warning(
                f"[{resolved_calendar_id}] Attachment insertion failed ({err_str[:80]}). Retrying event without attachment payload..."
            )
            try:
                event_body.pop("attachments", None)
                retry_event = (
                    calendar_service.events()
                    .insert(
                        calendarId=resolved_calendar_id,
                        body=event_body,
                        supportsAttachments=False,
                    )
                    .execute()
                )
                logger.info(
                    f"[{resolved_calendar_id}] Created event '{ev.title}' without direct attachment payload. Link: {retry_event.get('htmlLink')}"
                )
                return CalendarResult(
                    event_id=retry_event.get("id"),
                    html_link=retry_event.get("htmlLink", ""),
                    status="created",
                    summary=retry_event.get("summary"),
                )
            except Exception as retry_err:
                primary_err = retry_err

        logger.error(f"[{resolved_calendar_id}] Failed to create event '{ev.title}': {primary_err}")
        return CalendarResult(
            event_id=None,
            html_link="",
            status="failed",
            error=str(primary_err),
        )