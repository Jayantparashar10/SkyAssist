"""Validator tests: a reply that adds an undecided amount, time, date,
invented flight number, upgrade, "full night" or waiver must be rejected;
a reply that only restates the decisions must pass. Pure, no LLM, no DB.
"""

from __future__ import annotations

from backend.agent.validator import validate_reply
from backend.schemas import Decision

VOUCHER = Decision(
    decision_id="d_01", action="ISSUE_MEAL_VOUCHER", status="execute", rule_id="R-DELAY",
    params={"amount_inr": 500, "flight": "SK-118", "hours": 4},
    customer_facing_facts=["₹500 meal voucher for your 4h delay on SK-118."],
    assumption_ids=["A-02"],
)
LOUNGE = Decision(
    decision_id="d_02", action="GRANT_LOUNGE", status="execute", rule_id="R-DELAY",
    params={"flight": "SK-118", "hours": 4},
    customer_facing_facts=["Lounge access for the duration of your wait."],
    assumption_ids=[],
)
REBOOK_REQUEST = Decision(
    decision_id="d_01", action="REBOOK_REQUEST", status="execute", rule_id="R-REBOOK",
    params={"route": "Delhi → Goa", "window": "within 24 hours", "charge": "none"},
    customer_facing_facts=["We've submitted a request to rebook you on the next available Delhi → Goa flight within 24 hours, at no charge."],
    assumption_ids=["A-01", "A-10"],
)
INFORM_RETURN_LEG = Decision(
    decision_id="d_02", action="INFORM_RETURN_LEG", status="inform", rule_id="R-RETURN",
    params={"flight": None},
    customer_facing_facts=["Your Goa → Delhi flight on Fri 25 Sep stays booked."],
    assumption_ids=["A-06"],
)

DECISIONS = [VOUCHER, LOUNGE]
OWN_FLIGHTS = {"SK-118"}


def test_valid_reply_restating_decided_facts_passes():
    reply = "I understand this has been frustrating. ₹500 meal voucher for your 4h delay on SK-118. Lounge access for the duration of your wait."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is True


def test_reply_with_undecided_amount_rejected():
    reply = "I've also credited ₹1000 to your account."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is False


def test_reply_with_invented_flight_number_rejected():
    reply = "You're rebooked on SK-999."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is False


def test_reply_stating_customers_own_flight_number_passes():
    reply = "Your flight SK-118 is delayed. ₹500 meal voucher for your 4h delay on SK-118."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is True


def test_reply_with_undecided_upgrade_word_rejected():
    reply = "I've also arranged a free upgrade for you."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is False


def test_reply_with_undecided_full_night_rejected():
    reply = "I've booked a full night's stay for you."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is False


def test_reply_with_undecided_waiver_rejected():
    reply = "I've decided to waive the fare difference."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is False


def test_reply_with_undecided_hours_rejected():
    reply = "Your delay was 6h so here's a voucher."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is False


def test_reply_with_undecided_time_of_day_rejected():
    reply = "Your flight now departs at 23:59."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is False


def test_reply_stating_decided_time_of_day_passes():
    status = Decision(
        decision_id="d_01", action="STATUS", status="execute", rule_id="R-STATUS",
        params={}, customer_facing_facts=["SK-118 is delayed 4h, from 07:10 to 11:10."], assumption_ids=[],
    )
    reply = "SK-118 is delayed 4h, from 07:10 to 11:10."
    assert validate_reply(reply, [status], OWN_FLIGHTS) is True


def test_reply_with_invented_refund_date_rejected():
    reply = "Your refund will be processed by 2 October."
    assert validate_reply(reply, DECISIONS, OWN_FLIGHTS) is False


def test_reply_stating_decided_return_leg_date_passes():
    reply = "Your Goa → Delhi flight on Fri 25 Sep stays booked."
    assert validate_reply(reply, [INFORM_RETURN_LEG], OWN_FLIGHTS) is True


def test_reply_never_names_a_flight_number_for_rebook_request():
    # REBOOK_REQUEST's own facts never contain a flight number (A-01), so
    # a reply that sticks to those facts naturally has none to reject.
    reply = "We've submitted a request to rebook you on the next available Delhi → Goa flight within 24 hours, at no charge."
    assert validate_reply(reply, [REBOOK_REQUEST], OWN_FLIGHTS) is True


def test_sensitive_word_allowed_when_actually_decided():
    escalate = Decision(
        decision_id="d_01", action="ESCALATE", status="escalate", rule_id="R-BEYOND",
        params={"topic": "request_upgrade"},
        customer_facing_facts=["I've sent your upgrade request to a supervisor for review."],
        assumption_ids=[],
    )
    reply = "I've sent your upgrade request to a supervisor for review."
    assert validate_reply(reply, [escalate], OWN_FLIGHTS) is True


def test_waiver_word_allowed_when_fare_quote_mentions_it():
    quote = Decision(
        decision_id="d_01", action="QUOTE_FARE_DIFFERENCE", status="offer", rule_id="R-FARE",
        params={"fare_diff_inr": 2000},
        customer_facing_facts=["You can pay the difference, or request a waiver of it from a supervisor."],
        assumption_ids=["A-12", "A-13"],
    )
    reply = "You can pay the difference, or request a waiver of it from a supervisor."
    assert validate_reply(reply, [quote], OWN_FLIGHTS) is True
