from typing import Annotated, Literal

import pymysql
from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pymysql.constants import ER
from pymysql.cursors import DictCursor

from ticketing.classifier import Category, Priority
from ticketing.db import connect

app = FastAPI(title="Ticketing")
TicketID = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
TICKET_FIELDS = "id, subject, body, status, category, priority, summary, failure_reason"


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: TicketID
    subject: str = Field(max_length=1024)
    body: str = Field(max_length=1_000_000)


class Ticket(TicketInput):
    status: Literal["pending", "classified", "failed"]
    category: Category | None = None
    priority: Priority | None = None
    summary: str | None = None
    failure_reason: str | None = None


class TicketPage(BaseModel):
    items: list[Ticket]
    next_cursor: TicketID | None = None


def fetch_ticket(cursor: DictCursor, ticket_id: str) -> Ticket:
    cursor.execute(
        f"SELECT {TICKET_FIELDS} FROM tickets WHERE id = %s",
        (ticket_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return Ticket(**row)


@app.post(
    "/tickets",
    status_code=status.HTTP_202_ACCEPTED,
    response_model_exclude_none=True,
    responses={
        200: {"model": Ticket, "description": "Identical ticket already exists"},
        409: {"description": "ID already used for different content"},
    },
)
def create_ticket(ticket: TicketInput, response: Response) -> Ticket:
    with connect() as db, db.cursor() as cursor:
        try:
            cursor.execute(
                "INSERT INTO tickets (id, subject, body) VALUES (%s, %s, %s)",
                (ticket.id, ticket.subject, ticket.body),
            )
        except pymysql.IntegrityError as exc:
            if exc.args[0] != ER.DUP_ENTRY:
                raise
            db.rollback()
            existing = fetch_ticket(cursor, ticket.id)
            if (existing.subject, existing.body) != (ticket.subject, ticket.body):
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Ticket ID already used for different content"
                ) from None
            response.status_code = status.HTTP_200_OK
            return existing

        db.commit()
        return Ticket(**ticket.model_dump(), status="pending")


@app.get("/tickets/{ticket_id}", response_model_exclude_none=True)
def get_ticket(ticket_id: TicketID) -> Ticket:
    with connect() as db, db.cursor() as cursor:
        return fetch_ticket(cursor, ticket_id)


@app.get("/tickets", response_model_exclude_none=True)
def list_tickets(
    category: Category | None = None,
    priority: Priority | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: TicketID | None = None,
) -> TicketPage:
    conditions = []
    params = []
    if category is not None or priority is not None:
        conditions.append("status = 'classified'")
    if category is not None:
        conditions.append("category = %s")
        params.append(category.value)
    if priority is not None:
        conditions.append("priority = %s")
        params.append(priority.value)

    with connect() as db, db.cursor() as query:
        if cursor is not None:
            query.execute("SELECT created_at FROM tickets WHERE id = %s", (cursor,))
            anchor = query.fetchone()
            if anchor is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown ticket cursor")
            conditions.append("(created_at, id) < (%s, %s)")
            params.extend((anchor["created_at"], cursor))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        query.execute(
            f"SELECT {TICKET_FIELDS} FROM tickets{where} ORDER BY created_at DESC, id DESC LIMIT %s",
            (*params, limit + 1),
        )
        rows = query.fetchall()

    items = [Ticket(**row) for row in rows[:limit]]
    return TicketPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)
