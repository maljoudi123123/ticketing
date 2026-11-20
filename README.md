# Support ticket classification service

The service accepts tickets and retrieves them by ID. Each new ticket starts as `pending`. Asynchronous classification and filtered listing are planned in later PRs.

## Run locally

With Docker Compose, uv, and Python 3.12 or newer installed, run these commands from the repository root.

```sh
docker compose up -d --wait db
uv sync --locked
uv run --locked uvicorn ticketing.api:app
```

Compose initializes a fresh MySQL 8.4 volume with the migration in `migrations/`. The API defaults to the local Compose credentials on `127.0.0.1:3307`. To use an existing MySQL instance, apply the SQL migration to an empty database and configure `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD` for the API.

Submit a ticket, then fetch it from another terminal. IDs are case-sensitive and accept up to 128 ASCII letters, digits, underscores, or hyphens.

```sh
curl -i http://127.0.0.1:8000/tickets -H 'Content-Type: application/json' \
  -d '{"id":"t-1001","subject":"Charged twice","body":"I see two charges for one subscription."}'
curl -i http://127.0.0.1:8000/tickets/t-1001
```

The first submission returns `202`. Repeating it returns `200`. Changing the subject or body while reusing the ID returns `409`.

With MySQL running, execute `uv run --locked python -m pytest -q`. The integration tests call the FastAPI application directly and clean up their own generated ticket IDs. Stop the local database with `docker compose stop db`.

## Architecture

Use **FastAPI** for the HTTP endpoints, **MySQL** for ticket storage and pending work, and **Pydantic** to validate model output. Run one Python worker with one classification in flight. FastAPI is familiar to the team, and the workload has a fixed sequence of steps that fits in a worker function.

Inserting a pending ticket also schedules it for classification. This avoids a failure window between committing the ticket and publishing a separate queue message. The API reads directly from MySQL so clients polling for completion see the committed status. Revisit Redis if measured read load justifies caching. LangGraph would become worth reconsidering if classification grows into a branching workflow.

## HTTP contract

| Request | Proposed behavior |
| --- | --- |
| `POST /tickets` | Accept `id`, `subject`, and `body`. Commit a new ticket as `pending` and return `202` with its ID and status. Allow an empty subject, as in the sample data. |
| Repeat `POST /tickets` | Enforce uniqueness on the ticket ID in MySQL. For identical content, return the existing ticket with `200` regardless of status. For different content under that ID, return `409`. Both paths leave the original work item intact. |
| `GET /tickets/{id}` | Return the ticket and status, including classification fields on success or a short failure reason on terminal failure. Return `404` for an unknown ID. |
| `GET /tickets` | Accept optional `category` and `priority` filters and apply both when supplied. Use a bounded `limit` and a cursor over `(created_at, id)`, newest first. Unfiltered results include every status. Label filters match classified tickets only. |

## Worker and recovery

1. In a short transaction, claim an eligible `pending` row by setting a unique lease token and expiry and incrementing its attempt count. Commit before contacting the classifier. Set the model timeout shorter than the lease.
2. Validate the response. In one transaction, write the three validated fields and set the status to `classified`. Condition the update on the current lease token so a worker that lost its claim cannot overwrite a later result.
3. On a timeout, transient provider error, or validation error, release the claim and schedule a retry with backoff. After three attempts, set the status to `failed` and store a short reason such as `invalid_model_output`. Keep invalid output out of the classification fields.
4. After a restart, reclaim expired leases. If the final attempt expired, mark the ticket failed so it cannot stay pending forever. A crash after the provider responds and before the result commits can cause another model call. Duplicate client submissions never trigger that retry.

## Classifier boundary

`ticketing.classifier.classify_ticket(subject, body)` returns a validated `Classification`. Its default provider is a deterministic keyword-based fake. To supply your own provider, pass a callable via `classifier=` that takes the subject and body and returns raw JSON text. Pydantic raises `ValidationError` for malformed JSON or invalid fields. Tests use this entry point to supply broken responses.

Try the classifier and run its unit tests locally with just Python and uv.

```sh
uv run --locked python -c 'from ticketing.classifier import classify_ticket; print(classify_ticket("Invoice", "Where can I download it?").model_dump_json())'
uv run --locked python -m pytest -q tests/test_classifier.py
```

For a live adapter, use one OpenAI Responses API call with Structured Outputs. The Decisions API fits the two fixed-choice labels. The required one-sentence summary makes a single structured generation simpler than combining separate classification and generation steps. [Decisions API](https://developers.openai.com/api/docs/guides/decisions/) · [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs/)

| Field | Validation and interpretation |
| --- | --- |
| `category` | Pydantic enum allowing `billing`, `technical`, `account`, or `other`. Choose the category of the primary request. |
| `priority` | Pydantic enum allowing `low`, `medium`, or `high`. Use reported impact. A routine information request is low, an impaired workflow with a workaround is medium, and a blocked production workflow is high. |
| `summary` | Ask for one factual sentence describing the request. Require a string, trim whitespace, and accept 1–500 characters. The length check alone cannot establish sentence count or accuracy. |

Require all three fields, reject unexpected fields, and validate before writing. Treat the ticket subject and body as untrusted user content. Keep the classification rubric in the application instructions and give the classifier no tools or authority to take actions.

The sample ticket `t-1005` illustrates the limit of enums. Its request concerns invoice downloads, but the body asks for the valid labels `technical` and `high` and a summary claiming a refund was approved. Under the proposed rubric the expected labels are `billing` and `low`, and the summary should describe the invoice question.

## Implementation PRs

Open one PR per slice, in order. Start each branch from updated `main` after the previous PR merges. Include the tests and local run commands for each slice in the same PR. All five should be testable without an API key.

| PR | Scope | How to verify it |
| --- | --- | --- |
| 1. Ingest and retrieve a pending ticket | Add the minimal FastAPI and MySQL setup, initial migration, `POST /tickets`, and `GET /tickets/{id}`. | Run integration tests against MySQL. Concurrent identical submissions yield one `202`, one `200`, and one row. Changed content under the same ID yields `409` and preserves the original. Fetch by ID returns `pending` or `404` for an unknown ID. |
| 2. Validate classifier responses | Add the Pydantic enums and result model, a deterministic fake, and a function that turns raw classifier output into a validated result. Keep this slice runnable as plain Python. | Unit-test a valid response, malformed JSON, an unknown label, a missing or extra field, and a whitespace-only summary. Include a well-formed but factually wrong answer to show exactly what schema validation accepts. |
| 3. Claim work and recover interrupted attempts | Add the worker-state migration and MySQL operations for claiming, retrying, completing, and failing a ticket. | With two database connections, prove only one can claim the same pending ticket. Advance a controlled clock past lease expiry, reclaim it, then verify that the old token cannot commit. Expire the third attempt and verify the ticket becomes `failed`. |
| 4. Run the asynchronous worker | Connect the worker to the fake classifier and validated storage path. Apply timeouts, backoff, and the three-attempt limit. Document how to start the worker. | Submit with the worker stopped and observe `pending`. Start it and read back a classification. Script a timeout followed by success, then three invalid responses. Verify the latter ends in `failed` with no classification fields. Resubmit a completed ticket and assert the classifier call count stays the same. |
| 5. List tickets and demonstrate the take-home | Add category and priority filters, cursor pagination, and a loader for all ten appendix tickets. Finish the clean-clone instructions and document limitations observed during testing. | Test each filter and their intersection. Page across tied timestamps and verify each expected ID appears once. From a clean database, run the sample loader twice, let the worker finish, and verify there are exactly ten tickets. |

If time remains after PR 5, pick one extension. A small live-classifier evaluation could compare the invoice question with and without the embedded instructions. Score the labels and inspect both summaries for an invented refund approval.
