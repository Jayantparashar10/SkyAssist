"""templates.py tests: the deterministic reply builder and the login
welcome message. Pure, no LLM, no DB.
"""

from __future__ import annotations

from backend.agent.templates import build_reply, build_welcome_message
from backend.schemas import Decision
from backend.seed import build_fixtures

CUSTOMERS, BOOKINGS, _ = build_fixtures()


def test_build_reply_empty_decisions_falls_back_to_generic_line():
    assert build_reply([], "Priya Nair", "calm") == "Thanks for reaching out — let me look into that for you."


def test_build_reply_adds_empathy_for_frustrated_and_angry_only():
    voucher = Decision(
        decision_id="d_01", action="ISSUE_MEAL_VOUCHER", status="execute", rule_id="R-DELAY",
        params={}, customer_facing_facts=["A meal voucher for your delay."], assumption_ids=[],
    )
    calm = build_reply([voucher], "Arvind Kulkarni", "calm")
    angry = build_reply([voucher], "Arvind Kulkarni", "angry")
    assert "sorry" not in calm.lower()
    assert "sorry" in angry.lower()
    assert "A meal voucher for your delay." in calm
    assert "A meal voucher for your delay." in angry


def test_build_reply_uses_only_the_first_ask_question():
    ask1 = Decision(decision_id="d_01", action="ASK", status="ask", rule_id="R-FARE", params={"question": "Which flight?"}, customer_facing_facts=[], assumption_ids=[])
    ask2 = Decision(decision_id="d_02", action="ASK", status="ask", rule_id="R-FARE", params={"question": "Second question?"}, customer_facing_facts=[], assumption_ids=[])
    reply = build_reply([ask1, ask2], "Meher Kaur", "calm")
    assert "Which flight?" in reply
    assert "Second question?" not in reply


def test_welcome_message_for_delayed_booking_states_real_status():
    meher = CUSTOMERS["WL7742"]
    bookings = BOOKINGS["WL7742"]
    msg = build_welcome_message(meher, bookings)
    assert msg.startswith("Hi Meher,")
    assert "SK-305" in msg
    assert "delayed" in msg.lower()
    assert "6h" in msg
    assert "How can I help you today?" in msg


def test_welcome_message_for_cancelled_booking_states_real_status():
    priya = CUSTOMERS["SK4821X"]
    bookings = BOOKINGS["SK4821X"]
    msg = build_welcome_message(priya, bookings)
    assert msg.startswith("Hi Priya,")
    assert "SK-204" in msg
    assert "cancelled" in msg.lower()


def test_welcome_message_never_invents_a_flight_number_not_in_bookings():
    arvind = CUSTOMERS["TR1190B"]
    bookings = BOOKINGS["TR1190B"]
    msg = build_welcome_message(arvind, bookings)
    import re
    flights_mentioned = set(re.findall(r"\b[A-Za-z]{2}-\d{2,4}\b", msg))
    own_flights = {b.flight_no for b in bookings if b.flight_no}
    assert flights_mentioned <= own_flights


def test_welcome_message_for_unaffected_booking_is_generic():
    from backend.schemas import Booking
    from datetime import datetime

    customer = CUSTOMERS["SK4821X"]
    on_time = Booking(
        id=1, pnr="ZZ0000Z", customer_id=customer.id, leg=1, flight_no="SK-999",
        origin="Delhi", destination="Mumbai",
        sched_dep=datetime.fromisoformat("2026-09-23T10:00:00+05:30"),
        status="scheduled", new_dep=None, cause=None,
    )
    msg = build_welcome_message(customer, [on_time])
    assert msg == "Hi Priya, thanks for reaching out. How can I help you today?"
