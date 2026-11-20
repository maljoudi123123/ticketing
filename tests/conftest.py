import os
from uuid import uuid4

import pytest

from ticketing import api, work_queue as queue
from ticketing.db import connect


@pytest.fixture(autouse=True)
def use_test_database(monkeypatch):
    # A queue poll can claim any eligible row. Keep it away from development tickets.
    monkeypatch.setenv("MYSQL_DATABASE", "tickets_test")
    monkeypatch.setenv("MYSQL_PORT", os.getenv("MYSQL_TEST_PORT", "3308"))


@pytest.fixture
def ticket():
    ticket_id = f"test-{uuid4().hex}"
    yield {"id": ticket_id, "subject": "", "body": "أحتاج نسخة من الفاتورة 🧾"}
    with connect() as db, db.cursor() as cursor:
        cursor.execute("DELETE FROM tickets WHERE id = %s", (ticket_id,))
        db.commit()


@pytest.fixture
def advance(monkeypatch):
    timestamp = 1_800_000_000

    def connect_at_test_time():
        db = connect()
        with db.cursor() as cursor:
            cursor.execute("SET SESSION timestamp = %s", (timestamp,))
        return db

    def advance_clock(seconds):
        nonlocal timestamp
        timestamp += seconds

    monkeypatch.setattr(queue, "connect", connect_at_test_time)
    monkeypatch.setattr(api, "connect", connect_at_test_time)
    return advance_clock
