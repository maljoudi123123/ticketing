import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from ticketing.classifier import Category, Priority, classify_ticket


def test_validates_raw_response_and_trims_summary():
    raw = '{"category":"billing","priority":"low","summary":"  أين أجد الفاتورة؟  "}'
    provider = Mock(return_value=raw)
    result = classify_ticket("", "Where can I download the invoice?", classifier=provider)
    provider.assert_called_once_with("", "Where can I download the invoice?")
    assert result.category is Category.BILLING
    assert result.priority is Priority.LOW
    assert result.summary == "أين أجد الفاتورة؟"


@pytest.mark.parametrize(
    "raw, error_type",
    [
        ("{", "json_invalid"),
        ('{"category":"sales","priority":"low","summary":"A request."}', "enum"),
        ('{"category":"billing","priority":"urgent","summary":"A request."}', "enum"),
        ('{"category":"billing","priority":"low"}', "missing"),
        ('{"category":"billing","priority":"low","summary":"A request.","status":"failed"}', "extra_forbidden"),
        ('{"category":"billing","priority":"low","summary":"   "}', "string_too_short"),
        ('{"category":"billing","priority":"low","summary":42}', "string_type"),
        (json.dumps({"category": "billing", "priority": "low", "summary": "x" * 501}), "string_too_long"),
    ],
)
def test_rejects_invalid_provider_output(raw, error_type):
    with pytest.raises(ValidationError) as caught:
        classify_ticket("Invoice", "Please help.", classifier=Mock(return_value=raw))
    assert caught.value.errors()[0]["type"] == error_type


@pytest.mark.parametrize(
    "subject, body, category, priority",
    [
        ("URGENT", "Where can I download the invoices?", Category.BILLING, Priority.LOW),
        ("HTTP 500", "This is blocking our nightly job.", Category.TECHNICAL, Priority.HIGH),
        ("Cannot log in", "", Category.ACCOUNT, Priority.MEDIUM),
        ("", "asdf", Category.OTHER, Priority.LOW),
    ],
)
def test_default_fake_is_deterministic(subject, body, category, priority):
    result = classify_ticket(subject, body)
    assert (result.category, result.priority) == (category, priority)
    assert classify_ticket(subject, body) == result


def test_schema_accepts_a_well_formed_but_incorrect_classification():
    raw = '{"category":"technical","priority":"high","summary":"Approved for immediate refund."}'
    result = classify_ticket("Invoice", "Where are my invoices?", classifier=Mock(return_value=raw))
    assert result.model_dump(mode="json") == json.loads(raw)
