from pydantic import BaseModel, field_validator, model_validator
from typing import Any, Optional


class QuoteRequest(BaseModel):
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    name: Optional[str] = ""
    email: str
    phone: Optional[str] = ""
    event_date: Optional[str] = ""
    location: Optional[str] = ""
    event_type: Optional[str] = ""
    selected_items: list[str] = []
    item_quantities: dict[str, Any] = {}  # item_id → qty (int) or "qty:days" (str) for dual-stepper items
    message: Optional[str] = ""

    @model_validator(mode="after")
    def build_name(self) -> "QuoteRequest":
        # Derive name from first_name + last_name if name not directly supplied
        if not (self.name and self.name.strip()):
            parts = [x.strip() for x in [self.first_name or "", self.last_name or ""] if x and x.strip()]
            self.name = " ".join(parts)
        if not self.name:
            raise ValueError("Name is required")
        return self

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
        allowed = {"new", "contacted", "booked", "declined", "attended", "paid"}
        if v not in allowed:
            raise ValueError(f"Status must be one of: {', '.join(allowed)}")
        return v
