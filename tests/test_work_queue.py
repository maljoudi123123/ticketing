from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from ticketing import work_queue as queue
from ticketing.classifier import classify_ticket
from ticketing.db import connect


@pytest.fixture
def enqueue(advance):
    ids = []

    def insert():
        ticket_id = f"queue-{uuid4().hex}"
        with queue.connect() as db, db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO tickets (id, subject, body) VALUES (%s, 'Invoice', 'Need a copy')",
                (ticket_id,),
            )
            db.commit()
        ids.append(ticket_id)
        return ticket_id

    yield insert
    with connect() as db, db.cursor() as cursor:
        cursor.executemany("DELETE FROM tickets WHERE id = %s", [(key,) for key in ids])
        db.commit()


def stored(ticket_id):
    with connect() as db, db.cursor() as cursor:
        cursor.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        return cursor.fetchone()


RESULT = classify_ticket("Invoice", "Need a copy")


def test_concurrent_claim_and_atomic_completion(enqueue):
    ticket_id = enqueue()
    start = Barrier(2)

    def compete():
        start.wait(timeout=5)
        return queue.claim_next()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete) for _ in range(2)]
        claims = [future.result() for future in futures]
    assert claims.count(None) == 1
    claim = next(claim for claim in claims if claim is not None)
    assert (claim.id, claim.subject, claim.body, claim.attempt) == (ticket_id, "Invoice", "Need a copy", 1)
    assert stored(ticket_id)["status"] == "pending"
    assert queue.complete(claim, RESULT)
    assert not queue.complete(claim, RESULT)
    row = stored(ticket_id)
    assert (row["status"], row["lease_token"], row["failure_reason"]) == ("classified", None, None)
    assert {key: row[key] for key in ("category", "priority", "summary")} == RESULT.model_dump(mode="json")
    assert queue.claim_next() is None


def test_locked_ticket_does_not_block_other_work(enqueue, advance):
    first = enqueue()
    advance(1)
    second = enqueue()
    with connect() as db, db.cursor() as cursor:
        cursor.execute("SELECT id FROM tickets WHERE id = %s FOR UPDATE", (first,))
        assert queue.claim_next().id == second
        db.rollback()
    assert queue.claim_next().id == first


def test_expired_lease_rejects_stale_writes_before_and_after_reclaim(enqueue, advance):
    ticket_id = enqueue()
    old = queue.claim_next()
    advance(queue.LEASE_SECONDS)
    assert not queue.complete(old, RESULT)
    current = queue.claim_next()
    assert (current.id, current.attempt) == (ticket_id, 2)
    assert current.token != old.token
    assert not queue.complete(old, RESULT)
    assert not queue.record_failure(old, queue.FailureReason.MODEL_TIMEOUT, retry_delay_seconds=0)
    assert queue.complete(current, RESULT)


def test_delayed_retry_then_terminal_failure(enqueue, advance):
    ticket_id = enqueue()
    for attempt in range(1, 4):
        claim = queue.claim_next()
        assert claim.attempt == attempt
        assert queue.record_failure(claim, queue.FailureReason.INVALID_MODEL_OUTPUT, retry_delay_seconds=5)
        assert not queue.complete(claim, RESULT)
        if attempt < 3:
            row = stored(ticket_id)
            assert (row["status"], row["lease_token"], row["failure_reason"]) == ("pending", None, None)
        advance(4)
        assert queue.claim_next() is None
        advance(1)
    row = stored(ticket_id)
    assert (row["status"], row["attempt_count"], row["failure_reason"]) == ("failed", 3, "invalid_model_output")
    assert all(row[key] is None for key in ("lease_token", "category", "priority", "summary"))
    assert queue.claim_next() is None


def test_expired_final_attempt_fails_and_poll_continues(enqueue, advance):
    ticket_id = enqueue()
    for attempt in range(1, 4):
        last = queue.claim_next()
        assert last.attempt == attempt
        advance(queue.LEASE_SECONDS)
    advance(1)
    next_ticket = enqueue()
    assert queue.claim_next().id == next_ticket
    row = stored(ticket_id)
    assert (row["status"], row["attempt_count"], row["failure_reason"]) == ("failed", 3, "lease_expired")
    assert not queue.complete(last, RESULT)
