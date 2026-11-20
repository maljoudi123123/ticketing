from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ticketing.api import app
from ticketing.db import connect


@pytest.fixture
def ordered_ids():
    # The three middle rows tie and IDs alone give the wrong overall order.
    prefix = uuid4().hex
    ids = [f"{prefix}-{suffix}" for suffix in ("a", "e", "d", "c", "b")]
    states = [
        ("pending", None, None, "2026-01-01 12:00:00.000002"),
        ("classified", "billing", "low", "2026-01-01 12:00:00.000001"),
        ("classified", "billing", "high", "2026-01-01 12:00:00.000001"),
        ("classified", "account", "low", "2026-01-01 12:00:00.000001"),
        ("failed", None, None, "2026-01-01 12:00:00.000000"),
    ]
    with connect() as db, db.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO tickets (id, subject, body, status, category, priority, created_at)
               VALUES (%s, 'subject', 'body', %s, %s, %s, %s)""",
            [(key, *state) for key, state in zip(ids, states, strict=True)],
        )
        db.commit()
    yield ids
    with connect() as db, db.cursor() as cursor:
        cursor.executemany("DELETE FROM tickets WHERE id = %s", [(key,) for key in ids])
        db.commit()


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({}, [0, 1, 2, 3, 4]),
        ({"category": "billing"}, [1, 2]),
        ({"priority": "low"}, [1, 3]),
        ({"category": "billing", "priority": "low"}, [1]),
        ({"category": "technical", "priority": "high"}, []),
    ],
)
def test_filters_and_cursor_pages(ordered_ids, filters, expected):
    found = []
    params = {**filters, "limit": 1}
    with TestClient(app) as client:
        for _ in range(len(expected) + 1):
            response = client.get("/tickets", params=params)
            assert response.status_code == 200
            page = response.json()
            assert len(page["items"]) <= 1
            found.extend(item["id"] for item in page["items"])
            if "next_cursor" not in page:
                break
            params["cursor"] = page["next_cursor"]
        else:
            pytest.fail("Pagination did not terminate")
    assert found == [ordered_ids[i] for i in expected]


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"category": "unknown"}, {"priority": "urgent"},
    {"cursor": "bad cursor"}, {"cursor": "missing-" + uuid4().hex},
])
def test_invalid_list_parameters(params):
    with TestClient(app) as client:
        assert client.get("/tickets", params=params).status_code == 422
