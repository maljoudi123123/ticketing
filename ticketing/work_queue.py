from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from ticketing.classifier import Classification
from ticketing.db import connect

MAX_ATTEMPTS = 3
LEASE_SECONDS = 30


@dataclass(frozen=True)
class Claim:
    id: str
    subject: str
    body: str
    attempt: int
    token: str


class FailureReason(StrEnum):
    MODEL_TIMEOUT = "model_timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


def claim_next() -> Claim | None:
    """Claim the oldest eligible ticket, retiring exhausted leases along the way."""
    with connect() as db, db.cursor() as cursor:
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        while True:
            cursor.execute(
                """
                SELECT id, subject, body, attempt_count FROM tickets
                WHERE status = 'pending' AND available_at <= UTC_TIMESTAMP(6)
                ORDER BY available_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
                """
            )
            row = cursor.fetchone()
            if row is None:
                return None
            if row["attempt_count"] >= MAX_ATTEMPTS:
                cursor.execute(
                    """
                    UPDATE tickets SET status = 'failed', failure_reason = 'lease_expired',
                                       lease_token = NULL
                    WHERE id = %s
                    """,
                    (row["id"],),
                )
                db.commit()
                continue

            token = uuid4().hex
            cursor.execute(
                """
                UPDATE tickets SET attempt_count = attempt_count + 1, lease_token = %s,
                                   available_at = UTC_TIMESTAMP(6) + INTERVAL %s SECOND
                WHERE id = %s
                """,
                (token, LEASE_SECONDS, row["id"]),
            )
            db.commit()
            return Claim(row["id"], row["subject"], row["body"], row["attempt_count"] + 1, token)


def _update_owned_claim(claim: Claim, assignments: str, params: tuple) -> bool:
    with connect() as db, db.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE tickets SET lease_token = NULL, {assignments}
            WHERE id = %s AND lease_token = %s AND status = 'pending'
              AND available_at > UTC_TIMESTAMP(6)
            """,
            (*params, claim.id, claim.token),
        )
        updated = cursor.rowcount == 1
        db.commit()
        return updated


def complete(claim: Claim, result: Classification) -> bool:
    """Store a validated result atomically. Return False if ownership was lost."""
    return _update_owned_claim(
        claim,
        "status = 'classified', category = %s, priority = %s, summary = %s",
        (result.category.value, result.priority.value, result.summary),
    )


def record_failure(claim: Claim, reason: FailureReason, *, retry_delay_seconds: int) -> bool:
    """Release a live claim for retry, or record the reason if the budget is exhausted."""
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must be nonnegative")
    return _update_owned_claim(
        claim,
        """
        status = IF(attempt_count >= %s, 'failed', 'pending'),
        failure_reason = IF(attempt_count >= %s, %s, NULL),
        available_at = UTC_TIMESTAMP(6) + INTERVAL %s SECOND
        """,
        (MAX_ATTEMPTS, MAX_ATTEMPTS, reason.value, retry_delay_seconds),
    )
