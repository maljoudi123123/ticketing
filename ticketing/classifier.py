import json
from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    OTHER = "other"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, frozen=True)

    category: Category
    priority: Priority
    summary: str = Field(min_length=1, max_length=500)


def fake_classifier(subject: str, body: str) -> str:
    """Return raw JSON using a small set of deterministic support scenarios."""
    text = f"{subject}\n{body}".casefold()
    if "charged twice" in text or "two charges" in text:
        category, priority = Category.BILLING, Priority.MEDIUM
        summary = "The customer reports a possible duplicate charge."
    elif "password" in text or "log in" in text:
        category, priority = Category.ACCOUNT, Priority.MEDIUM
        summary = "The customer needs help accessing their account."
    elif "email address" in text or "change email" in text:
        category, priority = Category.ACCOUNT, Priority.LOW
        summary = "The customer asks about changing their email address."
    elif any(term in text for term in ("500", "timeout", "export")):
        category = Category.TECHNICAL
        priority = Priority.HIGH if "blocking" in text else Priority.MEDIUM
        summary = "The customer reports a technical problem."
    elif "invoice" in text:
        category, priority = Category.BILLING, Priority.LOW
        summary = "The customer needs help with invoicing."
    else:
        category, priority = Category.OTHER, Priority.LOW
        summary = "The customer submitted a general support request."
    return json.dumps({"category": category, "priority": priority, "summary": summary})


def classify_ticket(
    subject: str, body: str, *, classifier: Callable[[str, str], str] = fake_classifier
) -> Classification:
    """Validate raw provider output. Invalid responses raise pydantic.ValidationError."""
    raw_response = classifier(subject, body)
    return Classification.model_validate_json(raw_response)
