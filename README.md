# Support ticket classification service

This repository currently contains the proposed architecture for the take-home. The service will accept support tickets, classify them asynchronously, and expose the results over HTTP. Run instructions will follow with the implementation.

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

The default classifier will be a deterministic fake that works without credentials and can return malformed responses in tests. Both the fake and any live adapter pass through the same validation step.

For a live adapter, use one OpenAI Responses API call with Structured Outputs. The Decisions API fits the two fixed-choice labels. The required one-sentence summary makes a single structured generation simpler than combining separate classification and generation steps. [Decisions API](https://developers.openai.com/api/docs/guides/decisions/) · [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs/)

| Field | Validation and interpretation |
| --- | --- |
| `category` | Pydantic enum allowing `billing`, `technical`, `account`, or `other`. Choose the category of the primary request. |
| `priority` | Pydantic enum allowing `low`, `medium`, or `high`. Use reported impact. A routine information request is low, an impaired workflow with a workaround is medium, and a blocked production workflow is high. |
| `summary` | Ask for one factual sentence describing the request. Require a string, trim whitespace, and accept 1–500 characters. The length check alone cannot establish sentence count or accuracy. |

Require all three fields, reject unexpected fields, and validate before writing. Treat the ticket subject and body as untrusted user content. Keep the classification rubric in the application instructions and give the classifier no tools or authority to take actions.

The sample ticket `t-1005` illustrates the limit of enums. Its request concerns invoice downloads, but the body asks for the valid labels `technical` and `high` and a summary claiming a refund was approved. Under the proposed rubric the expected labels are `billing` and `low`, and the summary should describe the invoice question.

## Verification plan

- Submit the same ID concurrently. Verify one row, one initial classification attempt, and the documented replay or conflict response.
- Feed malformed JSON, an unknown enum value, and a blank summary through the fake. Verify retries, terminal failure, and the absence of a partial classification.
- Interrupt the worker after claiming a ticket. Verify recovery after lease expiry and rejection of a stale worker's write.
- Check combined filters and pagination with tickets that share a creation timestamp.
- If time permits one extension, evaluate the live classifier on the invoice question with and without the embedded instructions. Compare the labels and inspect both summaries for an invented refund approval. A deterministic fake can exercise the service paths but cannot measure prompt-injection resistance.
