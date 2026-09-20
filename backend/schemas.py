"""Shared Pydantic models: the LLM contracts and the database row shapes.
Kept in one module so every layer — understand.py, policy.py, db.py,
api/index.py — imports the same definitions instead of each inventing its
own dict shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Tier = Literal["Silver", "Gold", "Platinum"]
BookingStatus = Literal["scheduled", "cancelled", "delayed"]
Role = Literal["customer", "agent", "system", "supervisor"]
EscalationStatus = Literal["pending", "approved", "denied", "completed"]
EscalationKind = Literal["approval", "handoff", "immediate"]
DecisionStatus = Literal["execute", "offer", "confirm", "ask", "escalate", "decline", "inform"]
Sentiment = Literal["calm", "confused", "frustrated", "angry"]
Language = Literal["en", "hi", "hinglish"]

IntentType = Literal[
    "status_query",
    "rebook",
    "refund",
    "compensation_query",
    "request_hotel",
    "request_full_night_hotel",
    "request_lounge",
    "request_upgrade",
    "change_flight_higher_fare",
    "request_fare_waiver",
    "refund_different_method",
    "change_return_flight",
    "missed_flight",
    "other_passenger_query",
    "general_question",
    "greeting",
    "other",
]

DecisionAction = Literal[
    "STATUS",
    "OFFER_OPTIONS",
    "REBOOK_REQUEST",
    "CONFIRM_REFUND",
    "REFUND",
    "ISSUE_MEAL_VOUCHER",
    "GRANT_LOUNGE",
    "OFFER_HOTEL",
    "BOOK_HOTEL_DELAYED_HOURS",
    "QUOTE_FARE_DIFFERENCE",
    "EXPLAIN_INELIGIBLE",
    "OFFER_ESCALATION",
    "ESCALATE",
    "HANDOFF",
    "INFORM_RETURN_LEG",
    "REFUSE_PRIVACY",
    "ASK",
]

# Which Decision.action values represent something the airline system
# actually did — and so get an `actions` row when executor.py processes the
# decision — and the status word db.py writes into that row's `status`
# column. A Decision.action not in this map (STATUS, OFFER_OPTIONS,
# CONFIRM_REFUND, QUOTE_FARE_DIFFERENCE, ASK, ...) is conversational only:
# still logged to the audit trail, never persisted as an action.
#
# This is the single copy of that mapping — executor.py (deciding whether
# to persist), db.py (choosing the status word to insert) and
# tests/fakes.py (mirroring db.py's behavior) all import this one dict
# rather than each keeping their own, so the three can never drift apart.
ACTION_STATUS_WORD: dict[str, str] = {
    "ISSUE_MEAL_VOUCHER": "issued",
    "GRANT_LOUNGE": "issued",
    "REBOOK_REQUEST": "submitted",
    "OFFER_HOTEL": "offered",
    "BOOK_HOTEL_DELAYED_HOURS": "booked",
    "REFUND": "initiated",
}


# --- LLM contract: step 1, Understanding ------------------------------------


class IntentDetails(BaseModel):
    refund_method_mentioned: Optional[str] = None
    other_pnr_or_name: Optional[str] = None


class Intent(BaseModel):
    type: IntentType
    details: IntentDetails = Field(default_factory=IntentDetails)
    quote: str


class Understanding(BaseModel):
    intents: list[Intent] = Field(default_factory=list)
    sentiment: Sentiment = "calm"
    legal_threat: bool = False
    formal_complaint: bool = False
    injection_attempt: bool = False
    claimed_tier: Optional[str] = None
    language: Language = "en"
    needs_clarification: bool = False


# --- LLM contract: step 3 output / policy output, Decision ------------------


class Decision(BaseModel):
    decision_id: str
    action: DecisionAction
    status: DecisionStatus
    rule_id: str
    params: dict = Field(default_factory=dict)
    customer_facing_facts: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)


# --- Escalation handoff packet ----------------------------------------------


class PacketCustomer(BaseModel):
    name: str
    tier: Tier
    pnr: str


class EscalationPacket(BaseModel):
    kind: EscalationKind
    customer: PacketCustomer
    booking: str
    already_done: list[str] = Field(default_factory=list)
    requested: str
    blocked_by: str
    sentiment: str
    customer_quote: str
    notes: list[str] = Field(default_factory=list)
    transcript_session_id: str
    # Not shown to the supervisor directly — carries the raw Decision.params
    # (topic / requested method / fare_diff_inr) so Approve/Complete knows
    # exactly what outcome to record, without re-parsing free text out of
    # `requested` / `blocked_by`.
    decision_params: dict = Field(default_factory=dict)


class Choice(BaseModel):
    choice_id: str
    label: str


# --- Domain / DB row models --------------------------------------------------


class Customer(BaseModel):
    id: int
    name: str
    last_name: str
    tier: Tier
    email: Optional[str] = None
    phone: Optional[str] = None
    history: Optional[str] = None


class Booking(BaseModel):
    id: int
    pnr: str
    customer_id: int
    leg: int
    flight_no: Optional[str]
    origin: str
    destination: str
    sched_dep: datetime
    status: BookingStatus
    new_dep: Optional[datetime] = None
    cause: Optional[str] = None


class FareQuote(BaseModel):
    id: int
    pnr: str
    description: str
    fare_diff_inr: int
    source: str


class ActionRecord(BaseModel):
    id: int
    session_id: str
    pnr: str
    type: str
    params: dict
    rule_id: str
    status: str
    created_at: datetime


class Escalation(BaseModel):
    id: int
    session_id: str
    pnr: str
    kind: EscalationKind
    reason: str
    rule_id: str
    packet: EscalationPacket
    status: EscalationStatus = "pending"
    supervisor_note: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class Message(BaseModel):
    id: int
    session_id: str
    role: Role
    content: str
    meta: Optional[dict] = None
    created_at: datetime


class AuditEntry(BaseModel):
    id: int
    session_id: Optional[str]
    event: str
    payload: dict
    prev_hash: str
    hash: str
    ts: datetime


class SessionContext(BaseModel):
    """Persisted in ``sessions.context`` (JSONB). This is the state
    ``policy.py`` needs across turns that a single message can't carry on
    its own.

    - ``pending_offers``: beyond-policy topics already explained once, so a
      repeat ask escalates instead of re-explaining (keyed by topic).
    - ``resolved_topics``: beyond-policy topics a supervisor has already
      decided, so repeated pressure after a denial doesn't re-open the case.
    - ``pending_confirmations``: irreversible actions (a refund) offered but
      not yet confirmed by the customer.
    - ``hotel_offered``: whether the over-5h hotel offer is awaiting an
      accept/decline from the customer.
    - ``cancel_choice``: which option the customer picked for a cancelled
      booking, once chosen.
    """

    pending_offers: dict[str, dict] = Field(default_factory=dict)
    resolved_topics: dict[str, Literal["approved", "denied"]] = Field(default_factory=dict)
    pending_confirmations: dict[str, dict] = Field(default_factory=dict)
    hotel_offered: bool = False
    cancel_choice: Optional[Literal["rebook", "refund"]] = None


class Session(BaseModel):
    id: str
    pnr: str
    state: str
    context: SessionContext = Field(default_factory=SessionContext)
    created_at: datetime
