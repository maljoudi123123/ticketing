from typing import Annotated, Literal

import pymysql
from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pymysql.constants import ER
from pymysql.cursors import DictCursor

from ticketing.db import connect

app = FastAPI(title="Ticketing")
TicketID = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: TicketID
    subject: str = Field(max_length=1024)
    body: str = Field(max_length=1_000_000)


class Ticket(TicketInput):
    status: Literal["pending", "classified", "failed"]


def fetch_ticket(cursor: DictCursor, ticket_id: str) -> Ticket:
    cursor.execute("SELECT id, subject, body, status FROM tickets WHERE id = %s", (ticket_id,))
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return Ticket(**row)


@app.post(
    "/tickets",
    status_code=status.HTTP_202_ACCEPTED,
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


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: TicketID) -> Ticket:
    with connect() as db, db.cursor() as cursor:
        return fetch_ticket(cursor, ticket_id)
