"""Policy engine: pure functions, no I/O, no LLM calls. Applies the A-01..
A-15 assumptions from ``assumptions.py``; ``claimed_tier`` is never read
here, only ``Customer.tier`` decides entitlements.

Two entry points: ``evaluate`` (free-text intents) and ``resolve_choice``
(a button click). Both return an ``EvaluationResult`` that executor.py persists.
"""

from __future__ import annotations

from dataclasses import dataclass

from clock import IST
from schemas import (
    ActionRecord,
    Booking,
    Customer,
    Decision,
    FareQuote,
    Intent,
    SessionContext,
    Understanding,
)

FARE_WAIVER_LIMIT_INR = 1500


@dataclass
class EvaluationResult:
    decisions: list[Decision]
    session_ctx: SessionContext


# --- small pure helpers ------------------------------------------------------


def _fmt_time(dt) -> str:
    return dt.astimezone(IST).strftime("%H:%M")


def _fmt_date(dt) -> str:
    return dt.astimezone(IST).strftime("%a %d %b")


def delay_hours(booking: Booking) -> float | None:
    if booking.status != "delayed" or booking.new_dep is None:
        return None
    return (booking.new_dep - booking.sched_dep).total_seconds() / 3600


def find_cancelled_leg(bookings: list[Booking]) -> Booking | None:
    return next((b for b in bookings if b.status == "cancelled"), None)


def find_delayed_leg(bookings: list[Booking]) -> Booking | None:
    return next((b for b in bookings if b.status == "delayed"), None)


def _leg_named_in_quote(bookings: list[Booking], quote: str) -> Booking | None:
    """Matches by flight number, or by route order — "goa to delhi" and
    "delhi to goa" are different legs, so origin must appear before
    destination, not just both city names present."""
    quote_lower = quote.lower()
    for b in bookings:
        if b.flight_no and b.flight_no.lower() in quote_lower:
            return b
    for b in bookings:
        origin_idx = quote_lower.find(b.origin.lower())
        dest_idx = quote_lower.find(b.destination.lower())
        if origin_idx != -1 and dest_idx != -1 and origin_idx < dest_idx:
            return b
    return None


def _target_booking(bookings: list[Booking], quote: str = "") -> Booking:
    named = _leg_named_in_quote(bookings, quote) if quote else None
    if named is not None:
        return named
    disrupted = next((b for b in bookings if b.status in ("cancelled", "delayed")), None)
    return disrupted or bookings[0]


def _assign_ids(decisions: list[Decision]) -> list[Decision]:
    for i, d in enumerate(decisions, start=1):
        d.decision_id = f"d_{i:02d}"
    return decisions


# --- R-STATUS -----------------------------------------------------------------


def status_decision(booking: Booking) -> Decision:
    if booking.status == "cancelled":
        cause = f" ({booking.cause})" if booking.cause else ""
        facts = [f"{booking.flight_no} ({booking.origin} → {booking.destination}) is cancelled{cause}."]
    elif booking.status == "delayed":
        hours = delay_hours(booking)
        facts = [
            f"{booking.flight_no} ({booking.origin} → {booking.destination}) is delayed "
            f"{hours:g}h, from {_fmt_time(booking.sched_dep)} to {_fmt_time(booking.new_dep)}."
        ]
    else:
        facts = [
            f"{booking.flight_no or 'Your flight'} ({booking.origin} → {booking.destination}) is on "
            f"schedule, departing {_fmt_time(booking.sched_dep)}."
        ]
    return Decision(
        decision_id="", action="STATUS", status="execute", rule_id="R-STATUS",
        params={"flight": booking.flight_no, "status": booking.status},
        customer_facing_facts=facts, assumption_ids=[],
    )


# --- R-CANCEL / R-REBOOK / R-TIER -------------------------------------------


def cancel_offer_decision() -> Decision:
    return Decision(
        decision_id="", action="OFFER_OPTIONS", status="offer", rule_id="R-CANCEL",
        params={},
        customer_facing_facts=["Free rebooking on the next available flight within 24 hours, or a full refund — your choice."],
        assumption_ids=["A-01", "A-10"],
    )


def _not_cancelled_decision(bookings: list[Booking], requested: str) -> Decision:
    """Rebooking/refund require a cancelled leg — explain why when there isn't one."""
    delayed = find_delayed_leg(bookings)
    if delayed is not None:
        fact = (
            f"{delayed.flight_no} isn't cancelled, it's delayed, so {requested} isn't available for "
            "it — only delay compensation applies."
        )
    else:
        fact = f"There's no cancelled flight on this booking, so {requested} isn't available."
    return Decision(
        decision_id="", action="EXPLAIN_INELIGIBLE", status="decline", rule_id="R-CANCEL",
        params={}, customer_facing_facts=[fact], assumption_ids=[],
    )


def _rebook_request_decision(customer: Customer, booking: Booking) -> Decision:
    route = f"{booking.origin} → {booking.destination}"
    params: dict = {"route": route, "window": "within 24 hours", "charge": "none"}
    facts = [
        f"We've submitted a request to rebook you on the next available {route} flight within "
        "24 hours, at no charge. Reservations will confirm the exact flight."
    ]
    if customer.tier in ("Gold", "Platinum"):
        params["priority_rule_id"] = "R-TIER"
        facts.append(f"As a {customer.tier} member, you get priority access to the next available seat.")
    return Decision(
        decision_id="", action="REBOOK_REQUEST", status="execute", rule_id="R-REBOOK",
        params=params, customer_facing_facts=facts, assumption_ids=["A-01", "A-10"],
    )


# --- R-REFUND / R-REFUND-METHOD / R-RETURN (inform) -------------------------


def _confirm_refund_decisions(bookings: list[Booking], ctx: SessionContext) -> list[Decision]:
    cancelled = find_cancelled_leg(bookings)
    if cancelled is None:
        return []
    decisions = [
        Decision(
            decision_id="", action="CONFIRM_REFUND", status="confirm", rule_id="R-REFUND",
            params={"flight": cancelled.flight_no},
            customer_facing_facts=[
                f"Full refund for {cancelled.flight_no}, to your original payment method, processed within 7 business days."
            ],
            assumption_ids=["A-05", "A-11", "A-15"],
        )
    ]
    for leg in bookings:
        if leg.id == cancelled.id or leg.status == "cancelled":
            continue
        label = leg.flight_no or f"{leg.origin} → {leg.destination}"
        decisions.append(
            Decision(
                decision_id="", action="INFORM_RETURN_LEG", status="inform", rule_id="R-RETURN",
                params={"flight": leg.flight_no},
                customer_facing_facts=[
                    f"Your {leg.origin} → {leg.destination} flight on {_fmt_date(leg.sched_dep)} stays booked."
                ],
                assumption_ids=["A-06"],
            )
        )
    ctx.pending_confirmations["refund"] = {"rule_id": "R-REFUND", "flight": cancelled.flight_no}
    return decisions


def _refund_decision(bookings: list[Booking]) -> Decision | None:
    cancelled = find_cancelled_leg(bookings)
    if cancelled is None:
        return None
    return Decision(
        decision_id="", action="REFUND", status="execute", rule_id="R-REFUND",
        params={"flight": cancelled.flight_no, "amount": "full", "method": "original", "business_days": 7},
        customer_facing_facts=[
            f"Refund for {cancelled.flight_no} initiated — full amount, to your original payment "
            "method, processed within 7 business days."
        ],
        assumption_ids=["A-05", "A-11", "A-15"],
    )


def _refund_method_escalate_decision(method: str | None) -> Decision:
    return Decision(
        decision_id="", action="ESCALATE", status="escalate", rule_id="R-REFUND-METHOD",
        params={"requested_method": method},
        customer_facing_facts=["I've sent this refund-method request to a supervisor for review."],
        assumption_ids=["A-05"],
    )


def _return_leg_escalate_decision(bookings: list[Booking]) -> Decision:
    other = next((b for b in bookings if b.status != "cancelled"), None)
    label = (other.flight_no if other and other.flight_no else (f"{other.origin} → {other.destination}" if other else "your return flight"))
    return Decision(
        decision_id="", action="ESCALATE", status="escalate", rule_id="R-RETURN",
        params={"flight": label},
        customer_facing_facts=["Changes to that flight aren't covered here — I've sent this to a human to handle."],
        assumption_ids=["A-06"],
    )


# --- "what's already happened" status, for a repeat ask after the fact -----

_DELAY_ALREADY_GRANTED_LABELS: dict[str, str] = {
    "ISSUE_MEAL_VOUCHER": "a meal voucher",
    "GRANT_LOUNGE": "lounge access",
    "OFFER_HOTEL": "a hotel offer for the delayed hours",
    "BOOK_HOTEL_DELAYED_HOURS": "a hotel for the delayed hours",
}


def _join_labels(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def _already_handled_decision(booking: Booking, existing_types: set[str]) -> Decision | None:
    """A repeat ask about something already done (refund, rebooking, delay
    compensation) reports its status instead of silently doing nothing."""
    if booking.status == "cancelled":
        if "REFUND" in existing_types:
            return Decision(
                decision_id="", action="STATUS", status="execute", rule_id="R-REFUND",
                params={"flight": booking.flight_no},
                customer_facing_facts=[
                    f"Your refund for {booking.flight_no} has already been initiated — full amount, to "
                    "your original payment method, processed within 7 business days."
                ],
                assumption_ids=["A-05", "A-11", "A-15"],
            )
        if "REBOOK_REQUEST" in existing_types:
            route = f"{booking.origin} → {booking.destination}"
            return Decision(
                decision_id="", action="STATUS", status="execute", rule_id="R-REBOOK",
                params={"route": route},
                customer_facing_facts=[
                    f"A free rebooking request for your {route} flight has already been submitted — "
                    "reservations will confirm the exact flight."
                ],
                assumption_ids=["A-01", "A-10"],
            )
        return None

    if booking.status == "delayed":
        granted = [label for key, label in _DELAY_ALREADY_GRANTED_LABELS.items() if key in existing_types]
        if not granted:
            return None
        return Decision(
            decision_id="", action="STATUS", status="execute", rule_id="R-DELAY",
            params={"flight": booking.flight_no},
            customer_facing_facts=[f"You've already been given {_join_labels(granted)} for this delay."],
            assumption_ids=[],
        )

    return None


# --- R-DELAY (A-02, A-03, A-04) ----------------------------------------------


def delay_decisions(booking: Booking) -> list[Decision]:
    """Exclusive compensation tiers. A-04: exactly 3h/5h falls into the
    lower band, via ``<=``."""
    hours = delay_hours(booking)
    if hours is None:
        return []

    if hours <= 3:
        return [
            Decision(
                decision_id="", action="ISSUE_MEAL_VOUCHER", status="execute", rule_id="R-DELAY",
                params={"amount_inr": 500, "flight": booking.flight_no, "hours": hours},
                customer_facing_facts=[f"₹500 meal voucher for your {hours:g}h delay on {booking.flight_no}."],
                assumption_ids=["A-02"],
            )
        ]

    if hours <= 5:
        return [
            Decision(
                decision_id="", action="ISSUE_MEAL_VOUCHER", status="execute", rule_id="R-DELAY",
                params={"flight": booking.flight_no, "hours": hours},
                customer_facing_facts=[f"A meal voucher for your {hours:g}h delay on {booking.flight_no}."],
                assumption_ids=["A-02"],
            ),
            Decision(
                decision_id="", action="GRANT_LOUNGE", status="execute", rule_id="R-DELAY",
                params={"flight": booking.flight_no, "hours": hours},
                customer_facing_facts=["Lounge access for the duration of your wait."],
                assumption_ids=["A-03"],
            ),
        ]

    window = f"{_fmt_time(booking.sched_dep)}–{_fmt_time(booking.new_dep)}"
    return [
        Decision(
            decision_id="", action="ISSUE_MEAL_VOUCHER", status="execute", rule_id="R-DELAY",
            params={"flight": booking.flight_no, "hours": hours},
            customer_facing_facts=[f"A meal voucher for your {hours:g}h delay on {booking.flight_no}."],
            assumption_ids=["A-02"],
        ),
        Decision(
            decision_id="", action="OFFER_HOTEL", status="offer", rule_id="R-DELAY",
            params={"flight": booking.flight_no, "hours": hours, "window": window},
            customer_facing_facts=[f"A hotel for the delayed hours, {window}, if you'd like it — not a full night's stay."],
            assumption_ids=["A-03"],
        ),
    ]


def _delay_decisions_if_new(booking: Booking, existing_types: set[str]) -> list[Decision]:
    return [d for d in delay_decisions(booking) if d.action not in existing_types]


def _book_hotel_decision(booking: Booking) -> Decision:
    window = f"{_fmt_time(booking.sched_dep)}–{_fmt_time(booking.new_dep)}"
    return Decision(
        decision_id="", action="BOOK_HOTEL_DELAYED_HOURS", status="execute", rule_id="R-DELAY",
        params={"flight": booking.flight_no, "window": window},
        customer_facing_facts=[f"Hotel booked for {window} while you wait."],
        assumption_ids=["A-03"],
    )


def _decline_hotel_decision() -> Decision:
    return Decision(
        decision_id="", action="EXPLAIN_INELIGIBLE", status="decline", rule_id="R-DELAY",
        params={}, customer_facing_facts=["No problem — let me know if you change your mind."],
        assumption_ids=[],
    )


def _offer_delay_boundary_review(ctx: SessionContext) -> list[Decision]:
    """A-04 boundary delay: offer a supervisor review once, not on every message."""
    topic = "delay_band_boundary"
    if topic in ctx.pending_offers or topic in ctx.resolved_topics:
        return []
    ctx.pending_offers[topic] = {"rule_id": "R-DELAY", "requested": BEYOND_TOPICS[topic]["requested"]}
    return [
        Decision(
            decision_id="", action="OFFER_ESCALATION", status="offer", rule_id="R-DELAY",
            params={"topic": topic, "requested": BEYOND_TOPICS[topic]["requested"]},
            customer_facing_facts=[
                "Your delay falls exactly on a policy boundary — I can send this to a supervisor "
                "to double-check, if you'd like."
            ],
            assumption_ids=["A-04"],
        )
    ]


# --- R-FARE (A-07, A-12, A-13) -----------------------------------------------


def _fare_quote_decisions(fare_quotes: list[FareQuote]) -> list[Decision]:
    if not fare_quotes:
        return [
            Decision(
                decision_id="", action="HANDOFF", status="escalate", rule_id="R-FARE",
                params={}, customer_facing_facts=["I don't have fare details for that flight — I've handed this to a colleague to look into."],
                assumption_ids=["A-01"],
            )
        ]
    quote = fare_quotes[0]
    return [
        Decision(
            decision_id="", action="QUOTE_FARE_DIFFERENCE", status="offer", rule_id="R-FARE",
            params={"fare_diff_inr": quote.fare_diff_inr},
            customer_facing_facts=[
                "A delay doesn't come with free rebooking, so moving to a different flight is treated as a voluntary change.",
                f"The flight you asked about has a fare difference of ₹{quote.fare_diff_inr}.",
                "You can pay the difference, or request a waiver of it from a supervisor.",
            ],
            assumption_ids=["A-12", "A-13"],
        )
    ]


def _fare_handoff_decision(fare_quotes: list[FareQuote]) -> Decision:
    diff = fare_quotes[0].fare_diff_inr if fare_quotes else None
    return Decision(
        decision_id="", action="HANDOFF", status="escalate", rule_id="R-FARE",
        params={"fare_diff_inr": diff},
        customer_facing_facts=["I've handed this to a colleague to process the paid flight change for you."],
        assumption_ids=["A-13"],
    )


def _fare_waiver_escalate_decision(fare_quotes: list[FareQuote]) -> Decision:
    diff = fare_quotes[0].fare_diff_inr if fare_quotes else None
    return Decision(
        decision_id="", action="ESCALATE", status="escalate", rule_id="R-FARE",
        params={"fare_diff_inr": diff},
        customer_facing_facts=["I've sent the fare waiver request to a supervisor for approval."],
        assumption_ids=["A-07"],
    )


# --- R-BEYOND (upgrade / full-night hotel / under-5h hotel / lounge) -------

BEYOND_TOPICS = {
    "request_upgrade": {
        "requested": "A complimentary upgrade",
        "blocked_by": "Gold/Platinum tier gives priority rebooking, not extra compensation — an upgrade isn't part of the policy.",
    },
    "request_full_night_hotel": {
        "requested": "A full night's hotel stay",
        "blocked_by": "Hotel coverage is limited to the delayed hours, not a full night's stay.",
    },
    "request_hotel_under_5h": {
        "requested": "Hotel accommodation",
        "blocked_by": "Hotel accommodation only applies to delays over 5 hours.",
    },
    "request_lounge": {
        "requested": "Lounge access",
        "blocked_by": "Lounge access isn't part of your delay's compensation band.",
    },
    "delay_band_boundary": {
        "requested": "A review of a boundary-delay compensation band",
        "blocked_by": "This delay falls exactly on a policy boundary, so the lower band applies by default.",
    },
}


def _explain_ineligible_decision(topic: str) -> Decision:
    info = BEYOND_TOPICS[topic]
    return Decision(
        decision_id="", action="EXPLAIN_INELIGIBLE", status="decline", rule_id="R-BEYOND",
        params={"topic": topic}, customer_facing_facts=[info["blocked_by"]], assumption_ids=[],
    )


def _offer_escalation_decision(topic: str, requested_text: str) -> Decision:
    return Decision(
        decision_id="", action="OFFER_ESCALATION", status="offer", rule_id="R-BEYOND",
        params={"topic": topic, "requested": requested_text},
        customer_facing_facts=["I can send this to a supervisor to review, if you'd like."],
        assumption_ids=[],
    )


def _escalate_beyond_decision(topic: str, requested_text: str) -> Decision:
    return Decision(
        decision_id="", action="ESCALATE", status="escalate", rule_id="R-BEYOND",
        params={"topic": topic, "requested": requested_text},
        customer_facing_facts=["I've sent this to a supervisor for review."], assumption_ids=[],
    )


def _already_denied_decision(topic: str) -> Decision:
    info = BEYOND_TOPICS.get(topic, {"blocked_by": "This isn't something I can approve."})
    return Decision(
        decision_id="", action="EXPLAIN_INELIGIBLE", status="decline", rule_id="R-BEYOND",
        params={"topic": topic},
        customer_facing_facts=[f"A supervisor already reviewed this and it wasn't approved. {info['blocked_by']}"],
        assumption_ids=[],
    )


def _decline_decision(topic: str) -> Decision:
    return Decision(
        decision_id="", action="EXPLAIN_INELIGIBLE", status="decline", rule_id="R-BEYOND",
        params={"topic": topic}, customer_facing_facts=["No problem — let me know if there's anything else."],
        assumption_ids=[],
    )


def _beyond(ctx: SessionContext, topic: str, requested_text: str) -> list[Decision]:
    """Explains first; only escalates once the customer insists or accepts."""
    resolved = ctx.resolved_topics.get(topic)
    if resolved == "denied":
        return [_already_denied_decision(topic)]
    if resolved == "approved":
        return []
    if topic in ctx.pending_offers:
        del ctx.pending_offers[topic]
        return [_escalate_beyond_decision(topic, requested_text)]
    ctx.pending_offers[topic] = {"rule_id": "R-BEYOND", "requested": requested_text}
    return [_explain_ineligible_decision(topic), _offer_escalation_decision(topic, requested_text)]


# --- R-LEGAL / R-PRIVACY / R-NONAIRLINE --------------------------------------


def _legal_decision() -> Decision:
    return Decision(
        decision_id="", action="ESCALATE", status="escalate", rule_id="R-LEGAL",
        params={}, customer_facing_facts=["I've escalated this to a specialist supervisor right away."],
        assumption_ids=[],
    )


def _privacy_decision() -> Decision:
    return Decision(
        decision_id="", action="REFUSE_PRIVACY", status="decline", rule_id="R-PRIVACY",
        params={}, customer_facing_facts=["I can only discuss your own booking."], assumption_ids=[],
    )


def _nonairline_escalate_decision() -> Decision:
    return Decision(
        decision_id="", action="ESCALATE", status="escalate", rule_id="R-NONAIRLINE",
        params={}, customer_facing_facts=["This wasn't caused by the airline, so I've sent it to a human to review."],
        assumption_ids=[],
    )


# --- intent dispatch ----------------------------------------------------------


def _handle_intent(
    intent: Intent,
    customer: Customer,
    bookings: list[Booking],
    fare_quotes: list[FareQuote],
    ctx: SessionContext,
    existing_types: set[str],
) -> list[Decision]:
    t = intent.type

    if t in ("greeting", "general_question", "other"):
        return []

    if t == "other_passenger_query":
        return [_privacy_decision()]

    if t in ("status_query", "compensation_query"):
        booking = _target_booking(bookings, intent.quote)
        out: list[Decision] = []
        if t == "status_query":
            out.append(status_decision(booking))
        if booking.status == "cancelled":
            if not ({"REBOOK_REQUEST", "REFUND"} & existing_types) and "refund" not in ctx.pending_confirmations:
                out.append(cancel_offer_decision())
        elif booking.status == "delayed":
            out.extend(_delay_decisions_if_new(booking, existing_types))
            hours = delay_hours(booking)
            if hours in (3.0, 5.0):
                out.extend(_offer_delay_boundary_review(ctx))
        already = _already_handled_decision(booking, existing_types)
        if already:
            out.append(already)
        return out

    if t == "rebook":
        booking = find_cancelled_leg(bookings)
        if booking is None:
            return [_not_cancelled_decision(bookings, "free rebooking")]
        if "REBOOK_REQUEST" in existing_types:
            already = _already_handled_decision(booking, existing_types)
            return [already] if already else []
        ctx.cancel_choice = "rebook"
        return [_rebook_request_decision(customer, booking)]

    if t == "refund":
        booking = find_cancelled_leg(bookings)
        if booking is None:
            return [_not_cancelled_decision(bookings, "a refund")]
        if "REFUND" in existing_types:
            already = _already_handled_decision(booking, existing_types)
            return [already] if already else []
        ctx.cancel_choice = "refund"
        return _confirm_refund_decisions(bookings, ctx)

    if t == "refund_different_method":
        return [_refund_method_escalate_decision(intent.details.refund_method_mentioned)]

    if t == "request_upgrade":
        return _beyond(ctx, "request_upgrade", intent.quote)

    if t == "request_full_night_hotel":
        return _beyond(ctx, "request_full_night_hotel", intent.quote)

    if t == "request_hotel":
        booking = find_delayed_leg(bookings)
        hours = delay_hours(booking) if booking else None
        if hours is not None and hours > 5:
            return _delay_decisions_if_new(booking, existing_types)
        return _beyond(ctx, "request_hotel_under_5h", intent.quote)

    if t == "request_lounge":
        if "GRANT_LOUNGE" in existing_types:
            return []
        booking = find_delayed_leg(bookings)
        hours = delay_hours(booking) if booking else None
        if hours is not None and 3 < hours <= 5:
            return _delay_decisions_if_new(booking, existing_types)
        return _beyond(ctx, "request_lounge", intent.quote)

    if t == "request_fare_waiver":
        return [_fare_waiver_escalate_decision(fare_quotes)]

    if t == "change_flight_higher_fare":
        return _fare_quote_decisions(fare_quotes)

    if t == "change_return_flight":
        return [_return_leg_escalate_decision(bookings)]

    if t == "missed_flight":
        return [_nonairline_escalate_decision()]

    return []


# --- entry points -------------------------------------------------------------


def evaluate(
    customer: Customer,
    bookings: list[Booking],
    fare_quotes: list[FareQuote],
    understanding: Understanding,
    session_ctx: SessionContext,
    existing_actions: list[ActionRecord],
) -> EvaluationResult:
    ctx = session_ctx.model_copy(deep=True)
    existing_types = {a.type for a in existing_actions}
    decisions: list[Decision] = []

    if understanding.legal_threat or understanding.formal_complaint:
        decisions.append(_legal_decision())

    for intent in understanding.intents:
        details = intent.details
        if details.other_pnr_or_name:
            own_pnr = bookings[0].pnr if bookings else ""
            ref = details.other_pnr_or_name.strip().lower()
            if ref not in (own_pnr.strip().lower(), customer.name.strip().lower()):
                decisions.append(_privacy_decision())
                continue
        decisions.extend(_handle_intent(intent, customer, bookings, fare_quotes, ctx, existing_types))

    return EvaluationResult(decisions=_assign_ids(decisions), session_ctx=ctx)


def resolve_choice(
    choice_id: str,
    customer: Customer,
    bookings: list[Booking],
    fare_quotes: list[FareQuote],
    session_ctx: SessionContext,
) -> EvaluationResult:
    """Handles POST /api/choice. A button click is already structured, so
    this never calls the LLM."""
    ctx = session_ctx.model_copy(deep=True)
    parts = choice_id.split(":")
    kind = parts[0]

    if kind == "cancel":
        booking = find_cancelled_leg(bookings)
        if booking is None:
            raise ValueError("no cancelled leg for this choice")
        if parts[1] == "rebook":
            ctx.cancel_choice = "rebook"
            decisions = [_rebook_request_decision(customer, booking)]
        else:
            ctx.cancel_choice = "refund"
            decisions = _confirm_refund_decisions(bookings, ctx)
        return EvaluationResult(decisions=_assign_ids(decisions), session_ctx=ctx)

    if kind == "refund" and parts[1] == "confirm":
        ctx.pending_confirmations.pop("refund", None)
        refund = _refund_decision(bookings)
        decisions = [refund] if refund else []
        return EvaluationResult(decisions=_assign_ids(decisions), session_ctx=ctx)

    if kind == "hotel":
        booking = find_delayed_leg(bookings)
        if parts[1] == "accept" and booking is not None:
            decisions = [_book_hotel_decision(booking)]
        else:
            decisions = [_decline_hotel_decision()]
        return EvaluationResult(decisions=_assign_ids(decisions), session_ctx=ctx)

    if kind == "fare":
        if parts[1] == "pay":
            decisions = [_fare_handoff_decision(fare_quotes)]
        else:
            decisions = [_fare_waiver_escalate_decision(fare_quotes)]
        return EvaluationResult(decisions=_assign_ids(decisions), session_ctx=ctx)

    if kind == "beyond":
        topic, action = parts[1], parts[2]
        ctx.pending_offers.pop(topic, None)
        if action == "accept":
            decisions = [_escalate_beyond_decision(topic, BEYOND_TOPICS[topic]["requested"])]
        else:
            decisions = [_decline_decision(topic)]
        return EvaluationResult(decisions=_assign_ids(decisions), session_ctx=ctx)

    raise ValueError(f"unknown choice_id: {choice_id!r}")
