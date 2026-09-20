"""In-memory fake implementing the same method surface as db.PostgresStore
(structurally — db.Store is a Protocol, so no inheritance is required).
Lets executor.py / api/index.py be exercised end-to-end in tests without a
live Postgres connection. The LLM is what "mocked" refers to in
test_scenarios.py / test_redteam.py — this store is a real, if in-memory,
implementation of the same data operations Postgres performs, including
the ``UNIQUE(pnr, type)`` idempotency guarantee.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime

import audit as audit_module
from clock import sim_now
from schemas import (
    ACTION_STATUS_WORD,
    ActionRecord,
    Escalation,
    EscalationPacket,
    Message,
    Session,
    SessionContext,
)
from seed import build_fixtures


class InMemoryStore:
    def __init__(self) -> None:
        self._customers_by_pnr, self._bookings_by_pnr, self._fare_quotes_by_pnr = build_fixtures()
        self._sessions: dict[str, Session] = {}
        self._messages: list[Message] = []
        self._actions: list[ActionRecord] = []
        self._escalations: list[Escalation] = []
        self._audit: list[dict] = []
        self._ids = itertools.count(1)

    # --- customers / bookings / fare quotes -----------------------------------

    def authenticate(self, pnr, last_name):
        customer = self._customers_by_pnr.get(pnr)
        bookings = self._bookings_by_pnr.get(pnr, [])
        if not customer or not bookings:
            return None
        if customer.last_name.strip().lower() != last_name.strip().lower():
            return None
        return customer, bookings

    def get_bookings(self, pnr):
        return list(self._bookings_by_pnr.get(pnr, []))

    def get_customer_by_pnr(self, pnr):
        return self._customers_by_pnr.get(pnr)

    def get_fare_quotes(self, pnr):
        return list(self._fare_quotes_by_pnr.get(pnr, []))

    # --- sessions --------------------------------------------------------------

    def create_session(self, pnr):
        session = Session(id=str(uuid.uuid4()), pnr=pnr, state="ACTIVE", context=SessionContext(), created_at=datetime.now())
        self._sessions[session.id] = session
        return session

    def get_session(self, session_id):
        return self._sessions.get(session_id)

    def update_session_context(self, session_id, state, context):
        s = self._sessions[session_id]
        self._sessions[session_id] = s.model_copy(update={"state": state, "context": context})

    # --- messages ----------------------------------------------------------------

    def add_message(self, session_id, role, content, meta=None):
        msg = Message(id=next(self._ids), session_id=session_id, role=role, content=content, meta=meta, created_at=datetime.now())
        self._messages.append(msg)
        return msg

    def get_messages(self, session_id):
        return [m for m in self._messages if m.session_id == session_id]

    # --- actions -------------------------------------------------------------------

    def get_actions_for_pnr(self, pnr):
        return [a for a in self._actions if a.pnr == pnr]

    def insert_action_if_new(self, session_id, pnr, type_, params, rule_id):
        existing = next((a for a in self._actions if a.pnr == pnr and a.type == type_), None)
        if existing:
            return existing
        action = ActionRecord(
            id=next(self._ids), session_id=session_id, pnr=pnr, type=type_,
            params=params, rule_id=rule_id, status=ACTION_STATUS_WORD.get(type_, "issued"),
            created_at=datetime.now(),
        )
        self._actions.append(action)
        return action

    def upsert_action(self, session_id, pnr, type_, params, rule_id):
        existing = next((a for a in self._actions if a.pnr == pnr and a.type == type_), None)
        if existing:
            updated = existing.model_copy(update={
                "params": params, "rule_id": rule_id, "session_id": session_id,
                "status": ACTION_STATUS_WORD.get(type_, "issued"),
            })
            self._actions[self._actions.index(existing)] = updated
            return updated
        return self.insert_action_if_new(session_id, pnr, type_, params, rule_id)

    # --- escalations -----------------------------------------------------------------

    def insert_escalation(self, session_id, pnr, kind, reason, rule_id, packet: EscalationPacket):
        esc = Escalation(
            id=next(self._ids), session_id=session_id, pnr=pnr, kind=kind, reason=reason, rule_id=rule_id,
            packet=packet, status="pending", created_at=datetime.now(),
        )
        self._escalations.append(esc)
        return esc

    def get_escalation(self, escalation_id):
        return next((e for e in self._escalations if e.id == escalation_id), None)

    def get_escalations_for_pnr(self, pnr):
        return [e for e in self._escalations if e.pnr == pnr]

    def get_all_escalations(self):
        return list(self._escalations)

    def update_escalation(self, escalation_id, status, note):
        e = self.get_escalation(escalation_id)
        updated = e.model_copy(update={"status": status, "supervisor_note": note, "resolved_at": datetime.now()})
        self._escalations[self._escalations.index(e)] = updated
        return updated

    # --- audit log -----------------------------------------------------------------

    def insert_audit(self, session_id, event, payload):
        prev_hash = self._audit[-1]["hash"] if self._audit else audit_module.GENESIS_HASH
        link = audit_module.next_link(prev_hash, event, payload, datetime.now(sim_now().tzinfo))
        self._audit.append({"session_id": session_id, **link})

    def get_all_audit_entries(self):
        return list(self._audit)

    # --- demo reset --------------------------------------------------------------------

    def reset_and_seed(self):
        self.__init__()

    def ping(self):
        return True
