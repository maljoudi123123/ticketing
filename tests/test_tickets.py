from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ticketing.api import app
from ticketing.db import connect


def test_concurrent_replay_creates_one_pending_ticket(ticket):
    start = Barrier(2)

    def submit():
        with TestClient(app) as client:
            start.wait(timeout=10)
            return client.post("/tickets", json=ticket)

    with ThreadPoolExecutor(max_workers=2) as pool:
        submissions = [pool.submit(submit) for _ in range(2)]
        responses = [submission.result() for submission in submissions]

    assert sorted(response.status_code for response in responses) == [200, 202]
    expected = {**ticket, "status": "pending"}
    assert all(response.json() == expected for response in responses)
    with TestClient(app) as client:
        fetched = client.get(f"/tickets/{ticket['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == expected
    with connect() as db, db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS total FROM tickets WHERE id = %s", (ticket["id"],))
        assert cursor.fetchone()["total"] == 1


@pytest.mark.parametrize("field", ["subject", "body"])
def test_conflicting_replay_preserves_original(ticket, field):
    with TestClient(app) as client:
        assert client.post("/tickets", json=ticket).status_code == 202
        conflict = client.post("/tickets", json={**ticket, field: "updated"})
        assert conflict.status_code == 409
        assert client.get(f"/tickets/{ticket['id']}").json() == {**ticket, "status": "pending"}


def test_unknown_ticket():
    with TestClient(app) as client:
        assert client.get(f"/tickets/test-{uuid4().hex}").status_code == 404


def test_replay_preserves_existing_status(ticket):
    with TestClient(app) as client:
        assert client.post("/tickets", json=ticket).status_code == 202
        with connect() as db, db.cursor() as cursor:
            cursor.execute("UPDATE tickets SET status = 'failed' WHERE id = %s", (ticket["id"],))
            db.commit()
        replay = client.post("/tickets", json=ticket)
        assert replay.status_code == 200
        assert replay.json() == {**ticket, "status": "failed"}


def test_client_cannot_assign_status(ticket):
    with TestClient(app) as client:
        response = client.post("/tickets", json={**ticket, "status": "classified"})
        assert response.status_code == 422
        assert client.get(f"/tickets/{ticket['id']}").status_code == 404
