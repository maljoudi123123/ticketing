# Support ticket classification service

The API accepts a ticket and immediately returns its ID and `pending` status. A separate worker classifies it using a local fake. Fetch the result by ID, or list tickets by category and priority. The local demo runs without an API key.

## Run locally

With Docker Compose, uv, and Python 3.12 or newer installed, clone the repository and start the API.

```sh
git clone https://github.com/maljoudi123123/ticketing.git
cd ticketing
docker compose up -d --wait db
uv sync --locked
uv run --locked uvicorn ticketing.api:app
```

Start the worker in another terminal from the same directory. It polls the same database as the API and processes one ticket at a time. Run it on macOS or Linux, where Python supports the POSIX timer used for the classifier deadline.

```sh
uv run --locked python -m ticketing.worker
```

### Load and read the sample tickets

From a third terminal in the repository root, submit the ten appendix tickets through the API. The loader prints the ID, HTTP status, and ticket status for each submission.

```sh
uv run --locked python samples/load.py
curl -s 'http://127.0.0.1:8000/tickets/t-1005'
curl -s 'http://127.0.0.1:8000/tickets?category=billing&priority=low&limit=2'
```

New tickets return `202`. After the worker processes them, the combined query includes the invoice-download ticket (`t-1005`). Run the loader again to see ten `200` responses on a clean demo database. A `409` identifies an ID already used for different content. The loader stops and prints that ID.

### Existing databases

Compose applies the SQL files in `migrations/` in order on a fresh MySQL 8.4 volume. The API defaults to the local Compose credentials on `127.0.0.1:3307`. To use another MySQL instance, apply the migrations in order and configure `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD` for the API.

For an existing PR 1 or PR 2 volume, start the database and apply migration 002 once after MySQL accepts connections. Existing tickets keep their IDs, content, and status.

```sh
docker compose up -d db
docker compose exec -T db sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -u "$MYSQL_USER" "$MYSQL_DATABASE"' < migrations/002_ticket_work.sql
```

### Run the tests

Queue tests compete for the next pending ticket, so the integration suite uses a dedicated `tickets_test` database on port 3308. Start that Compose project once, then run the suite.

```sh
MYSQL_DATABASE=tickets_test MYSQL_PORT=3308 docker compose -p ticketing-test up -d --wait db
uv run --locked python -m pytest -q
```

To run only the lease and recovery tests, use `uv run --locked python -m pytest -q tests/test_work_queue.py`. For a different test port, set `MYSQL_PORT` on the test Compose command and `MYSQL_TEST_PORT` on pytest. Stop the test database with `docker compose -p ticketing-test stop db` and the development database with `docker compose stop db`.

## Architecture

Use **FastAPI** for the HTTP endpoints, **MySQL** for ticket storage and pending work, and **Pydantic** to validate model output. Run one Python worker with one classification in flight. FastAPI is familiar to the team, and the workload has a fixed sequence of steps that fits in a worker function.

Inserting a pending ticket also schedules it for classification. This avoids a failure window between committing the ticket and publishing a separate queue message. The API reads directly from MySQL so clients polling for completion see the committed status. Revisit Redis if measured read load justifies caching. LangGraph would become worth reconsidering if classification grows into a branching workflow.

## HTTP contract

| Request | Behavior |
| --- | --- |
| `POST /tickets` | Accept `id`, `subject`, and `body`. Commit a new ticket as `pending` and return `202` with its ID and status. Allow an empty subject, as in the sample data. |
| Repeat `POST /tickets` | Enforce uniqueness on the ticket ID in MySQL. For identical content, return the existing ticket with `200` regardless of status. For different content under that ID, return `409`. Both paths leave the original work item intact. |
| `GET /tickets/{id}` | Return the ticket and status, including classification fields on success or a short failure reason on terminal failure. Return `404` for an unknown ID. |
| `GET /tickets` | Accept optional `category` and `priority` filters and apply both when supplied. Use a bounded `limit` and a cursor over `(created_at, id)`, newest first. Unfiltered results include every status. Label filters match classified tickets only. |

A list response contains an `items` array and includes `next_cursor` only when another page is available. Pass it as `cursor` with the same filters. The default limit is 20 and the allowed range is 1–100. The cursor is the last returned ticket ID. The API looks up its immutable creation time and resumes strictly below that time and ID, including when several tickets share a timestamp.

A missing cursor ticket, invalid filter, or out-of-range limit returns `422`. Error responses have a `detail` field containing a message or FastAPI's list of validation errors.

## Worker and recovery

The database operations live in `ticketing.work_queue`. Each operation opens a short transaction. `claim_next()` uses `FOR UPDATE SKIP LOCKED` and returns the ticket content, attempt number, and a fresh token. `available_at` is the earliest time another worker can claim the row. It holds the lease expiry while owned and the retry time after a failure. MySQL supplies UTC time for all comparisons. The current lease lasts 30 seconds.

The worker polls every half second when idle and immediately checks for more work after each attempt. It passes validated results to `complete()` and schedules failures through `record_failure()`. Both return `False` when the claim is stale. A poll also retires expired final attempts before returning the next ticket.

1. In a short transaction, claim an eligible `pending` row by setting a unique lease token and expiry and incrementing its attempt count. Commit before contacting the classifier. The worker gives the classifier 10 seconds within the 30-second lease.
2. Validate the response. In one transaction, write the three validated fields and set the status to `classified`. Condition the update on the current lease token so a worker that lost its claim cannot overwrite a later result.
3. On a timeout, provider exception, or validation error, release the claim. Retry after one second for the first failure and two seconds for the second. A third failure sets the status to `failed` with `model_timeout`, `provider_error`, or `invalid_model_output`.
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

## Limitations and next steps

The fake uses English keywords and canned summaries. For example, it can identify the invoice topic in `t-1007` but miss the customer's correction deadline. Schema validation cannot establish whether a summary is accurate. With more time, I would compare a live classifier against a small labeled set, including clean and instruction-laden versions of the same request.

List pages read current data. A pending ticket that becomes classified after a client has paged past its position can appear on a fresh filtered query. At higher volume, measure the list queries and add indexes for the most common filter and sort combinations.

## Implementation PRs

| PR | Scope | How to verify it |
| --- | --- | --- |
| 1. Ingest and retrieve a pending ticket | Add the minimal FastAPI and MySQL setup, initial migration, `POST /tickets`, and `GET /tickets/{id}`. | Run integration tests against MySQL. Concurrent identical submissions yield one `202`, one `200`, and one row. Changed content under the same ID yields `409` and preserves the original. Fetch by ID returns `pending` or `404` for an unknown ID. |
| 2. Validate classifier responses | Add the Pydantic enums and result model, a deterministic fake, and a function that turns raw classifier output into a validated result. Keep this slice runnable as plain Python. | Unit-test a valid response, malformed JSON, an unknown label, a missing or extra field, and a whitespace-only summary. Include a well-formed but factually wrong answer to show exactly what schema validation accepts. |
| 3. Claim work and recover interrupted attempts | Add the worker-state migration and MySQL operations for claiming, retrying, completing, and failing a ticket. | With two database connections, prove only one can claim the same pending ticket. Advance a controlled clock past lease expiry, reclaim it, then verify that the old token cannot commit. Expire the third attempt and verify the ticket becomes `failed`. |
| 4. Run the asynchronous worker | Connect the worker to the fake classifier and validated storage path. Apply timeouts, backoff, and the three-attempt limit. Document how to start the worker. | Submit with the worker stopped and observe `pending`. Start it and read back a classification. Script a timeout followed by success, then three invalid responses. Verify the latter ends in `failed` with no classification fields. Resubmit a completed ticket and assert the classifier call count stays the same. |
| 5. List tickets and demonstrate the take-home | Add category and priority filters, cursor pagination, and a loader for all ten appendix tickets. Finish the clean-clone instructions and document limitations observed during testing. | Test each filter and their intersection. Page across tied timestamps and verify each expected ID appears once. From a clean database, run the sample loader twice, let the worker finish, and verify there are exactly ten tickets. |
