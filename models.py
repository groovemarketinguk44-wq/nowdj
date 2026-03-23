from pydantic import BaseModel, field_validator
from typing import Any, Optional


class QuoteRequest(BaseModel):
    name: str
    email: str
    phone: Optional[str] = ""
    event_date: Optional[str] = ""
    location: Optional[str] = ""
    event_type: Optional[str] = ""
    selected_items: list[str] = []
    item_quantities: dict[str, Any] = {}  # item_id → qty (int) or "qty:days" (str) for dual-stepper items
    message: Optional[str] = ""

    @field_validator("name")
    @classmethod
    def name_required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name is required")
        return v.strip()

    @field_validator("email")
    @classmethod
    def email_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Email is required")
        if "@" not in v:
            raise ValueError("Valid email is required")
        return v


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        allowed = {"new", "contacted", "booked", "declined"}
        if v not in allowed:
            raise ValueError(f"Status must be one of: {', '.join(allowed)}")
        return v
