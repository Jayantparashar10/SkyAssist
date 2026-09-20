"""The assumption ledger (A-01 through A-15).

The data pack this agent runs against leaves real gaps: no flight
inventory, no payment-method data, no holiday calendar, no time-of-day for
"the exercise is set on Wednesday 23 September 2026". Rather than let the
LLM or a hardcoded string quietly fill a gap, every place the code makes a
call about one of these gaps carries the assumption's ID on the resulting
``Decision`` (``assumption_ids``), and that ID is validated against this
ledger (see ``validate_id`` / ``validate_ids``) — a typo in an ID string
fails loudly instead of silently vanishing from the audit trail.

This is the single source of the assumption *text*: the customer-facing UI,
the escalation handoff packet, and ``ASSUMPTIONS.md`` should all read from
here rather than duplicating the wording.
"""

from __future__ import annotations

ASSUMPTIONS: dict[str, str] = {
    "A-01": (
        "No flight inventory is supplied. No flight is invented: a rebooking is submitted as a "
        "request carrying only route, “next available flight within 24 hours”, tier priority "
        "and no charge. Reservations confirms the exact flight."
    ),
    "A-02": (
        "The meal voucher amount (₹500) is stated only for delays under 3 hours. For longer "
        "delays the agent says “a meal voucher”, with no amount."
    ),
    "A-03": (
        "The delay-compensation bands are read as exclusive tiers, each a complete package. A "
        "delay over 5 hours gets a meal voucher and hotel, not lounge access. A request for "
        "something outside the customer's band is escalated."
    ),
    "A-04": (
        "A delay of exactly 3 or exactly 5 hours is not covered by “under 3” / “more than "
        "3” / “more than 5”. The lower band applies, the case is flagged, and escalation is "
        "offered."
    ),
    "A-05": (
        "The original payment method is never named. “Cash refund” is read as money back to "
        "the original method, not a request for a different one. The agent escalates only if the "
        "customer then explicitly asks for a different method."
    ),
    "A-06": (
        "The cancellation rule does not cover the unaffected return leg. The agent confirms it "
        "stays booked; any change, cancellation or refund of it is escalated."
    ),
    "A-07": (
        "Waiving a fare difference is not on the allowed-actions list at any amount. The agent "
        "never waives a fare difference itself, regardless of size."
    ),
    "A-08": (
        "Whether delay compensation stays in place if the customer later moves to a different "
        "flight isn't addressed by the rules. No rule is invented; the question is written into "
        "the handoff notes for a human to resolve."
    ),
    "A-09": (
        "No authentication method is specified. PNR + last name is matched against the database, "
        "with no format or length validation on the PNR (WL7742 is 6 characters; the others are 7)."
    ),
    "A-10": (
        "“Within 24 hours” has no stated reference point. It is not computed; the phrase is "
        "carried into the rebooking request exactly as the rule states it."
    ),
    "A-11": (
        "“Full refund” could mean the cancelled flight or the whole PNR. It is read as scoped "
        "to the cancelled flight only; the return leg is covered separately by A-06."
    ),
    "A-12": (
        "Free rebooking is granted for cancellations only. A delay does not carry a free-rebooking "
        "entitlement, so moving to a different flight during a delay is voluntary and the "
        "fare-difference rule applies."
    ),
    "A-13": (
        "A paid flight change is not on the allowed-actions list. The agent quotes the fare "
        "difference but does not process the change itself — it is handed to a human. A request "
        "to waive the difference goes to a supervisor."
    ),
    "A-14": (
        "The data pack gives the date but no time of day. The simulated clock is fixed at "
        "Wed 23 Sep 2026, 10:00 IST."
    ),
    "A-15": (
        "Computing an exact refund date depends on a holiday calendar that isn't supplied. The "
        "agent states “within 7 business days” and never calculates a date."
    ),
}


def validate_id(assumption_id: str) -> str:
    if assumption_id not in ASSUMPTIONS:
        raise ValueError(f"unknown assumption id: {assumption_id!r}")
    return assumption_id


def validate_ids(assumption_ids: list[str]) -> list[str]:
    return [validate_id(a) for a in assumption_ids]
