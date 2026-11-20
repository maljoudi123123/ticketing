"""Run the ticket worker in its own process with python -m ticketing.worker."""

import logging
import signal
import time
from collections.abc import Callable

import pymysql
from pydantic import ValidationError

from ticketing import work_queue
from ticketing.classifier import Classification, classify_ticket, fake_classifier
from ticketing.work_queue import Claim, FailureReason

MODEL_TIMEOUT_SECONDS = 10
POLL_SECONDS = 0.5
log = logging.getLogger(__name__)


def _deadline_expired(signum, frame):
    raise TimeoutError("Classifier deadline expired")


def _classify(claim: Claim, classifier: Callable[[str, str], str]) -> Classification:
    # The standalone worker owns the main thread and its real-time interval timer.
    previous = signal.signal(signal.SIGALRM, _deadline_expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, MODEL_TIMEOUT_SECONDS)
        return classify_ticket(claim.subject, claim.body, classifier=classifier)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def process_one(classifier: Callable[[str, str], str] = fake_classifier) -> bool:
    """Process at most one claim. Return False when there was no eligible ticket."""
    claim = work_queue.claim_next()
    if claim is None:
        return False

    try:
        result = _classify(claim, classifier)
    except TimeoutError:
        reason = FailureReason.MODEL_TIMEOUT
    except ValidationError:
        reason = FailureReason.INVALID_MODEL_OUTPUT
    except Exception:
        # Provider exception messages may include ticket content or raw output.
        reason = FailureReason.PROVIDER_ERROR
    else:
        work_queue.complete(claim, result)
        return True

    work_queue.record_failure(claim, reason, retry_delay_seconds=2 ** (claim.attempt - 1))
    return True


def main() -> None:
    if not hasattr(signal, "setitimer"):
        raise SystemExit("The ticket worker requires POSIX interval timers (macOS or Linux).")
    while True:
        try:
            if process_one():
                continue
        except pymysql.OperationalError as exc:
            log.warning("MySQL operation failed (code %s). Retrying poll.", exc.args[0])
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
