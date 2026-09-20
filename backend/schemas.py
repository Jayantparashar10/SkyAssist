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

# Decision.action values that get an `actions` row, mapped to the status
# word written into it. Anything not listed here is conversational only —
# logged to the audit trail but never persisted as an action. Single copy
# shared by executor.py, db.py and tests/fakes.py.
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
    # Not shown to the supervisor — raw Decision.params so Approve/Complete
    # knows the exact outcome to record without parsing free text.
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
    """Persisted in ``sessions.context`` (JSONB) — cross-turn state policy.py needs.

    - ``pending_offers``: beyond-policy topics already explained once, so a
      repeat ask escalates instead of re-explaining.
    - ``resolved_topics``: beyond-policy topics a supervisor already decided.
    - ``pending_confirmations``: irreversible actions offered but not yet
      confirmed by the customer.
    - ``hotel_offered``: whether the over-5h hotel offer awaits accept/decline.
    - ``cancel_choice``: which option the customer picked for a cancelled booking.
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
