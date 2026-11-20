import subprocess
import sys
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from ticketing import worker
from ticketing.api import app
from ticketing.classifier import fake_classifier


def test_worker_entry_point_classifies_a_pending_ticket(ticket):
    ticket["subject"] = "Charged twice"
    path = f"/tickets/{ticket['id']}"
    with TestClient(app) as client:
        response = client.post("/tickets", json=ticket)
        assert response.status_code == 202
        assert client.get(path).json() == {**ticket, "status": "pending"}

        process = subprocess.Popen([sys.executable, "-m", "ticketing.worker"])
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                assert process.poll() is None, "Worker exited before completing the ticket"
                result = client.get(path).json()
                if result["status"] == "classified":
                    break
                time.sleep(0.05)
            assert result == {
                **ticket, "status": "classified", "category": "billing", "priority": "medium",
                "summary": "The customer reports a possible duplicate charge.",
            }
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_blocked_classifier_times_out_then_retries_successfully(ticket, advance, monkeypatch):
    monkeypatch.setattr(worker, "MODEL_TIMEOUT_SECONDS", 0.1)
    calls = 0

    def provider(subject, body):
        nonlocal calls
        calls += 1
        if calls == 1:
            time.sleep(2)
        return fake_classifier(subject, body)

    with TestClient(app) as client:
        path = f"/tickets/{ticket['id']}"
        assert client.post("/tickets", json=ticket).status_code == 202
        assert worker.process_one(provider)
        assert client.get(path).json() == {**ticket, "status": "pending"}
        assert not worker.process_one(provider)
        advance(1)
        assert worker.process_one(provider)
        classified = client.get(path).json()
        assert classified["status"] == "classified"
        replay = client.post("/tickets", json=ticket)
        assert replay.status_code == 200
        assert replay.json() == classified
        assert not worker.process_one(provider)
        assert calls == 2


@pytest.mark.parametrize(
    "output, reason",
    [
        ("malformed", "invalid_model_output"),
        (TimeoutError(), "model_timeout"),
        (ConnectionError("provider unavailable"), "provider_error"),
    ],
)
def test_three_failed_attempts_expose_only_a_short_reason(ticket, advance, output, reason):
    provider = Mock(side_effect=[output, output, output])
    with TestClient(app) as client:
        path = f"/tickets/{ticket['id']}"
        assert client.post("/tickets", json=ticket).status_code == 202
        for delay in (1, 2):
            assert worker.process_one(provider)
            assert client.get(path).json() == {**ticket, "status": "pending"}
            advance(delay - 1)
            assert not worker.process_one(provider)
            advance(1)
        assert worker.process_one(provider)
        failed = {**ticket, "status": "failed", "failure_reason": reason}
        assert client.get(path).json() == failed
        assert client.post("/tickets", json=ticket).json() == failed
        advance(30)
        assert not worker.process_one(provider)
        assert provider.call_count == 3
