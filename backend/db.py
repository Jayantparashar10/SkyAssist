"""Database access. The only module that talks to Postgres. Every function
that touches business logic (policy, guards, validation, the audit hash
chain) takes plain typed data in and out — this module's job is purely to
move rows in and out of Neon.

``Store`` is a ``Protocol`` (structural typing, not inheritance) so
``backend/tests/fakes.py`` can provide an ``InMemoryStore`` with the exact
same method signatures, letting executor.py and api/index.py be tested
without a live database. ``PostgresStore`` below is the real implementation
used at runtime.

Serverless functions cannot keep a connection pool alive, so each request
opens one connection through Neon's pooled endpoint and closes it when the
request finishes — see ``get_connection`` / the FastAPI dependency in
api/index.py.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from backend.schemas import (
    ACTION_STATUS_WORD,
    ActionRecord,
    Booking,
    Customer,
    Escalation,
    EscalationKind,
    EscalationPacket,
    FareQuote,
    Message,
    Session,
    SessionContext,
)


class Store(Protocol):
    def authenticate(self, pnr: str, last_name: str) -> Optional[tuple[Customer, list[Booking]]]: ...

    def get_bookings(self, pnr: str) -> list[Booking]: ...

    def get_customer_by_pnr(self, pnr: str) -> Optional[Customer]: ...

    def get_fare_quotes(self, pnr: str) -> list[FareQuote]: ...

    def create_session(self, pnr: str) -> Session: ...

    def get_session(self, session_id: str) -> Optional[Session]: ...

    def update_session_context(self, session_id: str, state: str, context: SessionContext) -> None: ...

    def add_message(self, session_id: str, role: str, content: str, meta: Optional[dict] = None) -> Message: ...

    def get_messages(self, session_id: str) -> list[Message]: ...

    def get_actions_for_pnr(self, pnr: str) -> list[ActionRecord]: ...

    def insert_action_if_new(self, session_id: str, pnr: str, type_: str, params: dict, rule_id: str) -> ActionRecord: ...

    def upsert_action(self, session_id: str, pnr: str, type_: str, params: dict, rule_id: str) -> ActionRecord: ...

    def insert_escalation(self, session_id: str, pnr: str, kind: EscalationKind, reason: str, rule_id: str, packet: EscalationPacket) -> Escalation: ...

    def get_escalation(self, escalation_id: int) -> Optional[Escalation]: ...

    def get_escalations_for_pnr(self, pnr: str) -> list[Escalation]: ...

    def get_all_escalations(self) -> list[Escalation]: ...

    def update_escalation(self, escalation_id: int, status: str, note: Optional[str]) -> Escalation: ...

    def insert_audit(self, session_id: Optional[str], event: str, payload: dict) -> None: ...

    def get_all_audit_entries(self) -> list[dict]: ...

    def reset_and_seed(self) -> None: ...

    def ping(self) -> bool: ...


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return dsn


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(_dsn(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class PostgresStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    # --- customers / bookings / fare quotes -----------------------------------

    def authenticate(self, pnr: str, last_name: str) -> Optional[tuple[Customer, list[Booking]]]:
        # A-09: PNR format/length is never validated (WL7742 is 6 characters,
        # the others 7) — matching is by exact string equality only.
        bookings = self.get_bookings(pnr)
        if not bookings:
            return None
        customer = self._get_customer(bookings[0].customer_id)
        if customer is None or customer.last_name.strip().lower() != last_name.strip().lower():
            return None
        return customer, bookings

    def get_bookings(self, pnr: str) -> list[Booking]:
        rows = self.conn.execute(
            "SELECT * FROM bookings WHERE pnr = %s ORDER BY leg", (pnr,)
        ).fetchall()
        return [Booking(**row) for row in rows]

    def get_customer_by_pnr(self, pnr: str) -> Optional[Customer]:
        bookings = self.get_bookings(pnr)
        if not bookings:
            return None
        return self._get_customer(bookings[0].customer_id)

    def _get_customer(self, customer_id: int) -> Optional[Customer]:
        row = self.conn.execute("SELECT * FROM customers WHERE id = %s", (customer_id,)).fetchone()
        return Customer(**row) if row else None

    def get_fare_quotes(self, pnr: str) -> list[FareQuote]:
        rows = self.conn.execute("SELECT * FROM fare_quotes WHERE pnr = %s ORDER BY id", (pnr,)).fetchall()
        return [FareQuote(**row) for row in rows]

    # --- sessions ------------------------------------------------------------

    def create_session(self, pnr: str) -> Session:
        session_id = str(uuid.uuid4())
        row = self.conn.execute(
            "INSERT INTO sessions (id, pnr, state, context) VALUES (%s, %s, %s, %s) RETURNING *",
            (session_id, pnr, "ACTIVE", Json({})),
        ).fetchone()
        return self._row_to_session(row)

    def get_session(self, session_id: str) -> Optional[Session]:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = %s", (session_id,)).fetchone()
        return self._row_to_session(row) if row else None

    def update_session_context(self, session_id: str, state: str, context: SessionContext) -> None:
        self.conn.execute(
            "UPDATE sessions SET state = %s, context = %s WHERE id = %s",
            (state, Json(context.model_dump()), session_id),
        )

    @staticmethod
    def _row_to_session(row: dict) -> Session:
        return Session(
            id=str(row["id"]),
            pnr=row["pnr"],
            state=row["state"],
            context=SessionContext(**(row["context"] or {})),
            created_at=row["created_at"],
        )

    # --- messages --------------------------------------------------------------

    def add_message(self, session_id: str, role: str, content: str, meta: Optional[dict] = None) -> Message:
        row = self.conn.execute(
            "INSERT INTO messages (session_id, role, content, meta) VALUES (%s, %s, %s, %s) RETURNING *",
            (session_id, role, content, Json(meta) if meta is not None else None),
        ).fetchone()
        return Message(**{**row, "session_id": str(row["session_id"])})

    def get_messages(self, session_id: str) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE session_id = %s ORDER BY id", (session_id,)
        ).fetchall()
        return [Message(**{**row, "session_id": str(row["session_id"])}) for row in rows]

    # --- actions -----------------------------------------------------------------

    def get_actions_for_pnr(self, pnr: str) -> list[ActionRecord]:
        rows = self.conn.execute(
            "SELECT * FROM actions WHERE pnr = %s ORDER BY id", (pnr,)
        ).fetchall()
        return [self._row_to_action(row) for row in rows]

    def insert_action_if_new(self, session_id: str, pnr: str, type_: str, params: dict, rule_id: str) -> ActionRecord:
        existing = self.conn.execute(
            "SELECT * FROM actions WHERE pnr = %s AND type = %s", (pnr, type_)
        ).fetchone()
        if existing:
            return self._row_to_action(existing)
        status = ACTION_STATUS_WORD.get(type_, "issued")
        row = self.conn.execute(
            """INSERT INTO actions (session_id, pnr, type, params, rule_id, status)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (session_id, pnr, type_, Json(params), rule_id, status),
        ).fetchone()
        return self._row_to_action(row)

    def upsert_action(self, session_id: str, pnr: str, type_: str, params: dict, rule_id: str) -> ActionRecord:
        """Used by the supervisor-approval path, where an approved exception
        may legitimately supersede an action the agent already recorded
        (e.g. a fare waiver approved after the customer already saw the
        pay-the-difference quote)."""
        status = ACTION_STATUS_WORD.get(type_, "issued")
        row = self.conn.execute(
            """INSERT INTO actions (session_id, pnr, type, params, rule_id, status)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (pnr, type) DO UPDATE
                 SET params = EXCLUDED.params, rule_id = EXCLUDED.rule_id,
                     session_id = EXCLUDED.session_id, status = EXCLUDED.status
               RETURNING *""",
            (session_id, pnr, type_, Json(params), rule_id, status),
        ).fetchone()
        return self._row_to_action(row)

    @staticmethod
    def _row_to_action(row: dict) -> ActionRecord:
        return ActionRecord(**{**row, "session_id": str(row["session_id"])})

    # --- escalations -----------------------------------------------------------

    def insert_escalation(self, session_id: str, pnr: str, kind: EscalationKind, reason: str, rule_id: str, packet: EscalationPacket) -> Escalation:
        row = self.conn.execute(
            """INSERT INTO escalations (session_id, pnr, kind, reason, rule_id, packet)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (session_id, pnr, kind, reason, rule_id, Json(packet.model_dump())),
        ).fetchone()
        return self._row_to_escalation(row)

    def get_escalation(self, escalation_id: int) -> Optional[Escalation]:
        row = self.conn.execute("SELECT * FROM escalations WHERE id = %s", (escalation_id,)).fetchone()
        return self._row_to_escalation(row) if row else None

    def get_escalations_for_pnr(self, pnr: str) -> list[Escalation]:
        rows = self.conn.execute(
            "SELECT * FROM escalations WHERE pnr = %s ORDER BY id", (pnr,)
        ).fetchall()
        return [self._row_to_escalation(row) for row in rows]

    def get_all_escalations(self) -> list[Escalation]:
        rows = self.conn.execute("SELECT * FROM escalations ORDER BY id").fetchall()
        return [self._row_to_escalation(row) for row in rows]

    def update_escalation(self, escalation_id: int, status: str, note: Optional[str]) -> Escalation:
        row = self.conn.execute(
            """UPDATE escalations SET status = %s, supervisor_note = %s, resolved_at = now()
               WHERE id = %s RETURNING *""",
            (status, note, escalation_id),
        ).fetchone()
        return self._row_to_escalation(row)

    @staticmethod
    def _row_to_escalation(row: dict) -> Escalation:
        return Escalation(**{**row, "session_id": str(row["session_id"]), "packet": EscalationPacket(**row["packet"])})

    # --- audit log ---------------------------------------------------------------

    def insert_audit(self, session_id: Optional[str], event: str, payload: dict) -> None:
        from backend import audit as audit_module
        from backend.clock import sim_now

        last = self.conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = last["hash"] if last else audit_module.GENESIS_HASH
        link = audit_module.next_link(prev_hash, event, payload, datetime.now(sim_now().tzinfo))
        self.conn.execute(
            "INSERT INTO audit_log (session_id, event, payload, prev_hash, hash, ts) VALUES (%s, %s, %s, %s, %s, %s)",
            (session_id, link["event"], Json(link["payload"]), link["prev_hash"], link["hash"], link["ts"]),
        )

    def get_all_audit_entries(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        return list(rows)

    # --- demo reset ----------------------------------------------------------------

    def reset_and_seed(self) -> None:
        from backend import seed as seed_module

        self.conn.execute("TRUNCATE audit_log, escalations, actions, messages, sessions RESTART IDENTITY")
        self.conn.execute("TRUNCATE fare_quotes, bookings, customers RESTART IDENTITY CASCADE")

        name_to_id: dict[str, int] = {}
        for c in seed_module.CUSTOMERS:
            row = self.conn.execute(
                """INSERT INTO customers (name, last_name, tier, email, phone, history)
                   VALUES (%(name)s, %(last_name)s, %(tier)s, %(email)s, %(phone)s, %(history)s) RETURNING id""",
                c,
            ).fetchone()
            name_to_id[c["name"]] = row["id"]

        for b in seed_module.BOOKINGS:
            self.conn.execute(
                """INSERT INTO bookings (pnr, customer_id, leg, flight_no, origin, destination, sched_dep, status, new_dep, cause)
                   VALUES (%(pnr)s, %(customer_id)s, %(leg)s, %(flight_no)s, %(origin)s, %(destination)s, %(sched_dep)s, %(status)s, %(new_dep)s, %(cause)s)""",
                {**b, "customer_id": name_to_id[b["customer_name"]]},
            )

        for q in seed_module.FARE_QUOTES:
            self.conn.execute(
                """INSERT INTO fare_quotes (pnr, description, fare_diff_inr, source)
                   VALUES (%(pnr)s, %(description)s, %(fare_diff_inr)s, %(source)s)""",
                q,
            )

    def ping(self) -> bool:
        try:
            self.conn.execute("SELECT 1")
            return True
        except Exception:
            return False
