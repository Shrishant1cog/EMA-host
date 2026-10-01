from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Category = Literal[
    "Meeting & Event",
    "Task & Deadline",
    "Important Update",
    "General / Marketing",
    "Spam / Scam",
]
Priority = Literal["High", "Normal", "Low"]
EventAction = Literal["CREATE", "UPDATE", "CANCEL"]
EventCategory = Literal["ACADEMIC", "MEETING", "EXAM", "DEADLINE", "WEBINAR", "PERSONAL"]
ImportanceFlag = Literal["HIGH", "MEDIUM", "LOW"]
ReminderMethod = Literal["popup", "email"]


class EventAttendee(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    email: Optional[str] = None


class EventReminder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    method: ReminderMethod = "popup"
    minutes_before: int = Field(default=15, ge=0, le=10080)


class VirtualMeeting(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: Optional[str] = None
    url: Optional[str] = None
    meeting_id: Optional[str] = None
    passcode: Optional[str] = None


class CalendarEvent(BaseModel):
    """Normalized event contract shared by AI, Calendar and frontend layers."""

    model_config = ConfigDict(extra="ignore")

    action: EventAction = "CREATE"
    title: str = Field(default="Scheduled Event", min_length=1, max_length=300)
    category: EventCategory = "MEETING"
    description: str = Field(default="", max_length=10000)
    start: str = Field(default="")
    end: str = Field(default="")
    timezone: str = Field(default="Asia/Kolkata")
    location: Optional[str] = Field(default=None, max_length=1000)
    meeting_url: Optional[str] = Field(default=None, max_length=2000)
    organizer: Optional[str] = Field(default=None, max_length=500)
    all_day: bool = False
    is_all_day: bool = False
    guests: List[str] = Field(default_factory=list)
    attendees: List[EventAttendee] = Field(default_factory=list)
    virtual_meeting: Optional[VirtualMeeting] = None
    recurrence_rule: Optional[str] = Field(default=None, max_length=1000)
    reminders: List[EventReminder] = Field(default_factory=lambda: [EventReminder(method="popup", minutes_before=15)])
    importance_flag: ImportanceFlag = "MEDIUM"

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # Title aliases.
        if not data.get("title"):
            data["title"] = (
                data.get("summary") or data.get("name") or data.get("subject") or "Scheduled Event"
            )

        # Event category aliases.
        category = str(data.get("category") or data.get("event_category") or "MEETING").upper().strip()
        category_aliases = {
            "MEETING & EVENT": "MEETING",
            "MEETING": "MEETING",
            "INTERVIEW": "MEETING",
            "EXAM": "EXAM",
            "ACADEMIC": "ACADEMIC",
            "DEADLINE": "DEADLINE",
            "TASK": "DEADLINE",
            "WEBINAR": "WEBINAR",
            "PERSONAL": "PERSONAL",
        }
        data["category"] = category_aliases.get(category, "MEETING")

        # Start/end aliases.
        start_val = data.get("start") or data.get("start_time") or data.get("startTime") or data.get("date") or ""
        if isinstance(start_val, dict):
            start_val = start_val.get("dateTime") or start_val.get("date") or ""
        data["start"] = str(start_val).strip()

        end_val = data.get("end") or data.get("end_time") or data.get("endTime") or ""
        if isinstance(end_val, dict):
            end_val = end_val.get("dateTime") or end_val.get("date") or ""
        data["end"] = str(end_val).strip()

        # Backward-compatible 1-hour end derivation only when end was genuinely omitted.
        if data["start"] and not data["end"]:
            try:
                clean_start = data["start"].replace("Z", "+00:00")
                if "T" in clean_start:
                    dt = datetime.fromisoformat(clean_start)
                    data["end"] = (dt + timedelta(hours=1)).isoformat()
                else:
                    data["end"] = data["start"]
                    data["all_day"] = True
                    data["is_all_day"] = True
            except Exception:
                # Leave invalid values visible for the calendar layer to reject.
                data["end"] = ""

        # Attendee/guest normalization while preserving structured attendee metadata.
        raw_attendees = data.get("attendees") or data.get("guests") or []
        attendee_objects: List[Dict[str, Optional[str]]] = []
        guest_emails: List[str] = []
        if isinstance(raw_attendees, str):
            raw_attendees = [x.strip() for x in raw_attendees.split(",") if x.strip()]
        if isinstance(raw_attendees, list):
            for item in raw_attendees:
                if isinstance(item, dict):
                    email = str(item.get("email") or "").strip().lower() or None
                    name = str(item.get("name") or "").strip() or None
                    if email or name:
                        attendee_objects.append({"name": name, "email": email})
                    if email and "@" in email:
                        guest_emails.append(email)
                elif isinstance(item, str) and item.strip():
                    value = item.strip()
                    if "@" in value:
                        value = value.lower()
                        guest_emails.append(value)
                        attendee_objects.append({"name": None, "email": value})
        data["guests"] = sorted(dict.fromkeys(guest_emails))
        data["attendees"] = attendee_objects

        all_day_flag = bool(data.get("all_day") or data.get("is_all_day", False))
        data["all_day"] = all_day_flag
        data["is_all_day"] = all_day_flag

        # Normalize meeting metadata.
        vm = data.get("virtual_meeting")
        if not isinstance(vm, dict):
            vm = None
        if vm:
            data["virtual_meeting"] = vm

        return data

    @field_validator("importance_flag", mode="before")
    @classmethod
    def normalize_importance(cls, v: Any) -> str:
        return str(v or "MEDIUM").upper()

    def __getitem__(self, key: str) -> Any:
        if key in ("summary", "title"):
            return self.title
        if key in ("link", "meeting_link", "meeting_url"):
            return self.meeting_url
        if key == "guests":
            return self.guests
        if key == "attendees":
            return self.attendees
        if key in ("all_day", "is_all_day"):
            return self.all_day
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in ("summary", "title"):
            return self.title
        if key in ("link", "meeting_link", "meeting_url"):
            return self.meeting_url
        if key in ("all_day", "is_all_day"):
            return self.all_day
        if hasattr(self, key):
            value = getattr(self, key)
            return default if value is None else value
        return default

    def __contains__(self, key: str) -> bool:
        return key in self.model_fields or key in {"summary", "title", "link", "meeting_link", "event"}

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class UniversalEmailAnalysis(BaseModel):
    """Strict universal email-analysis contract. Extra provider fields are ignored."""

    model_config = ConfigDict(extra="ignore")

    actionable: bool = False
    has_calendar_event: bool = False
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    spam_score: float = Field(default=0.0, ge=0.0, le=1.0)
    is_spam_or_scam: bool = False
    category: Category = "General / Marketing"
    priority: Priority = "Normal"
    primary_source_type: Literal["email", "pdf_attachment", "ocr_circular", "spreadsheet", "unknown"] = "unknown"
    summary: str = Field(default="General correspondence", max_length=1500)
    action_description: str = Field(default="Processed email", max_length=1500)
    is_attachment_evidence: bool = False
    spam_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    is_event: bool = False
    reasoning: str = Field(default="", max_length=4000)
    events: List[CalendarEvent] = Field(default_factory=list)

    @field_validator("spam_score", "spam_threshold", "confidence_score", mode="before")
    @classmethod
    def parse_ratio(cls, v: Any) -> float:
        if v is None:
            return 0.0
        try:
            if isinstance(v, str):
                v = float(v.replace("%", "").strip())
            value = float(v)
            if value > 1.0:
                value /= 100.0
            return max(0.0, min(1.0, value))
        except Exception:
            return 0.0

    @field_validator("actionable", "has_calendar_event", "is_spam_or_scam", "is_attachment_evidence", "is_event", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(v)

    @model_validator(mode="after")
    def enforce_consistency(self) -> "UniversalEmailAnalysis":
        if self.is_spam_or_scam or self.spam_score >= self.spam_threshold or self.category == "Spam / Scam":
            self.is_spam_or_scam = True
            self.category = "Spam / Scam"
            self.priority = "Low"
            self.actionable = False
            self.has_calendar_event = False
            self.is_event = False
            self.events = []
        elif self.category not in ("Meeting & Event", "Task & Deadline"):
            self.has_calendar_event = False
            self.is_event = False
            self.events = []
            self.actionable = bool(self.actionable)
        else:
            self.has_calendar_event = bool(self.events)
            self.is_event = self.has_calendar_event
            if self.has_calendar_event:
                self.actionable = True

        if not self.has_calendar_event:
            self.is_event = False
        return self

    @property
    def should_create_event(self) -> bool:
        return bool(
            self.actionable
            and self.has_calendar_event
            and self.events
            and not self.is_spam_or_scam
            and self.category in ("Meeting & Event", "Task & Deadline")
        )

    def __getitem__(self, key: str) -> Any:
        if key == "should_create_event":
            return self.should_create_event
        if key in ("event", "event_data"):
            return self.events[0] if self.events else None
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "should_create_event":
            return self.should_create_event
        if key in ("event", "event_data"):
            return self.events[0] if self.events else None
        if hasattr(self, key):
            value = getattr(self, key)
            return default if value is None else value
        return default

    def __contains__(self, key: str) -> bool:
        return key in self.model_fields or key in {"should_create_event", "event", "event_data"}

    def to_dict(self) -> Dict[str, Any]:
        data = self.model_dump()
        data["should_create_event"] = self.should_create_event
        data["event_data"] = self.events[0].model_dump() if self.events else None
        data["event"] = data["event_data"]
        return data


EmailEventAnalysis = UniversalEmailAnalysis
