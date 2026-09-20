"""Step 4 — executor. Runs a decision against the database: an ``actions``
row for the outcomes the airline system actually records (a voucher
issued, a rebooking request submitted, a hotel offered or booked, a refund
initiated), an ``escalations`` row for anything that needs a human, and an
audit entry for every decision regardless of outcome. Idempotent: db.py's
``insert_action_if_new`` enforces ``UNIQUE(pnr, type)``, so re-processing
the same decision twice never double-grants anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.clock import IST
from backend.schemas import (
    ACTION_STATUS_WORD,
    ActionRecord,
    Booking,
    Customer,
    Decision,
    Escalation,
    EscalationKind,
    EscalationPacket,
    PacketCustomer,
    Session,
    Understanding,
)

if TYPE_CHECKING:
    from backend.db import Store

_ALREADY_DONE_LABELS: dict[str, str] = {
    "ISSUE_MEAL_VOUCHER": "Meal voucher issued (R-DELAY)",
    "GRANT_LOUNGE": "Lounge access issued (R-DELAY)",
    "REBOOK_REQUEST": "Rebooking request submitted (R-REBOOK)",
    "OFFER_HOTEL": "Hotel for delayed hours offered (R-DELAY)",
    "BOOK_HOTEL_DELAYED_HOURS": "Hotel for delayed hours booked (R-DELAY)",
    "REFUND": "Refund initiated (R-REFUND)",
}

# Which rule triggered the escalation decides who has to act on it and how
# (project's three-way escalation model): a supervisor approves/denies an
# *exception* to policy; a human simply completes an *allowed* outcome the
# agent can't process by itself; a legal/formal-complaint threat always
# routes to a specialist immediately.
_ESCALATION_KIND_BY_RULE: dict[str, EscalationKind] = {
    "R-LEGAL": "immediate",
    "R-BEYOND": "approval",
    "R-REFUND-METHOD": "approval",
    "R-RETURN": "handoff",
    "R-NONAIRLINE": "handoff",
}


def _escalation_kind(d: Decision) -> EscalationKind:
    if d.action == "HANDOFF":
        return "handoff"
    return _ESCALATION_KIND_BY_RULE.get(d.rule_id, "approval")


def _already_done_lines(existing_actions: list[ActionRecord]) -> list[str]:
    return [_ALREADY_DONE_LABELS.get(a.type, f"{a.type} ({a.rule_id})") for a in existing_actions]


def _booking_label(bookings: list[Booking]) -> str:
    if not bookings:
        return ""
    booking = next((b for b in bookings if b.status in ("cancelled", "delayed")), bookings[0])
    route = f"{booking.flight_no or ''} {booking.origin}→{booking.destination}".strip()
    sched = booking.sched_dep.astimezone(IST)
    if booking.status == "cancelled":
        return f"{route}, scheduled {sched:%H:%M}, cancelled"
    if booking.status == "delayed" and booking.new_dep:
        new_dep = booking.new_dep.astimezone(IST)
        return f"{route}, scheduled {sched:%H:%M}, delayed to {new_dep:%H:%M}"
    return f"{route}, scheduled {sched:%H:%M}"


def _requested_text(d: Decision) -> str:
    if d.params.get("requested"):
        return str(d.params["requested"])
    if d.customer_facing_facts:
        return d.customer_facing_facts[0]
    return d.rule_id


def _build_packet(
    d: Decision,
    customer: Customer,
    session: Session,
    bookings: list[Booking],
    understanding: Understanding,
    running_existing: list[ActionRecord],
) -> EscalationPacket:
    notes = list(d.assumption_ids)
    already_hotel = any(a.type in ("OFFER_HOTEL", "BOOK_HOTEL_DELAYED_HOURS") for a in running_existing)
    if already_hotel and d.rule_id in ("R-FARE", "R-CANCEL", "R-REBOOK"):
        notes.append(
            "A-08: whether the hotel offer still applies if the customer moves to a different "
            "flight isn't addressed by the rules — for the human to decide."
        )
    blocked_by = f"{d.rule_id}: {d.customer_facing_facts[0]}" if d.customer_facing_facts else d.rule_id
    quote = next((i.quote for i in understanding.intents), "")
    return EscalationPacket(
        kind=_escalation_kind(d),
        customer=PacketCustomer(name=customer.name, tier=customer.tier, pnr=session.pnr),
        booking=_booking_label(bookings),
        already_done=_already_done_lines(running_existing),
        requested=_requested_text(d),
        blocked_by=blocked_by,
        sentiment=understanding.sentiment,
        customer_quote=quote,
        notes=notes,
        transcript_session_id=session.id,
        decision_params=d.params,
    )


def execute_decisions(
    store: "Store",
    session: Session,
    customer: Customer,
    bookings: list[Booking],
    decisions: list[Decision],
    understanding: Understanding,
    existing_actions_before: list[ActionRecord],
) -> tuple[list[ActionRecord], list[Escalation]]:
    new_actions: list[ActionRecord] = []
    new_escalations: list[Escalation] = []
    running_existing = list(existing_actions_before)

    for d in decisions:
        store.insert_audit(session.id, "decision", {"decision": d.model_dump()})

        action_status = ACTION_STATUS_WORD.get(d.action)
        if action_status is not None:
            action = store.insert_action_if_new(
                session.id, session.pnr, d.action,
                {**d.params, "assumption_ids": d.assumption_ids}, d.rule_id,
            )
            new_actions.append(action)
            running_existing.append(action)
            store.insert_audit(
                session.id, "action",
                {"type": action.type, "status": action_status, "params": action.params, "rule_id": action.rule_id},
            )

        elif d.status == "escalate":
            packet = _build_packet(d, customer, session, bookings, understanding, running_existing)
            escalation = store.insert_escalation(session.id, session.pnr, kind=packet.kind, reason=d.rule_id, rule_id=d.rule_id, packet=packet)
            new_escalations.append(escalation)
            store.insert_audit(
                session.id, "escalation",
                {"id": escalation.id, "kind": packet.kind, "rule_id": d.rule_id, "packet": packet.model_dump()},
            )

    return new_actions, new_escalations
