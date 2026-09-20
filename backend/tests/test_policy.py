"""Policy engine tests: every rule and its boundaries. No LLM, no DB —
policy.py takes typed fixtures and returns Decision objects.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.agent import policy
from backend.schemas import (
    ActionRecord,
    Booking,
    FareQuote,
    Intent,
    IntentDetails,
    SessionContext,
    Understanding,
)
from backend.seed import build_fixtures

CUSTOMERS, BOOKINGS, FARE_QUOTES = build_fixtures()


def customer(pnr: str):
    return CUSTOMERS[pnr]


def bookings(pnr: str) -> list[Booking]:
    return BOOKINGS[pnr]


def fare_quotes(pnr: str) -> list[FareQuote]:
    return FARE_QUOTES.get(pnr, [])


def make_booking(**overrides) -> Booking:
    base = dict(
        id=99, pnr="ZZ0000Z", customer_id=1, leg=1, flight_no="SK-999",
        origin="Delhi", destination="Goa",
        sched_dep=datetime.fromisoformat("2026-09-23T10:00:00+05:30"),
        status="delayed", new_dep=None, cause=None,
    )
    base.update(overrides)
    return Booking(**base)


def intent(type_: str, quote: str = "test", **details) -> Intent:
    return Intent(type=type_, quote=quote, details=IntentDetails(**details))


def understanding(*intents: Intent, **kwargs) -> Understanding:
    return Understanding(intents=list(intents), **kwargs)


EMPTY_CTX = SessionContext()


def run(pnr: str, intents: list[Intent], ctx: SessionContext = EMPTY_CTX, existing: list[ActionRecord] | None = None, **u_kwargs):
    u = understanding(*intents, **u_kwargs)
    return policy.evaluate(customer(pnr), bookings(pnr), fare_quotes(pnr), u, ctx, existing or [])


def delayed_booking(hours: float) -> Booking:
    sched = datetime.fromisoformat("2026-09-23T10:00:00+05:30")
    return make_booking(status="delayed", sched_dep=sched, new_dep=sched + timedelta(hours=hours))


# --- R-DELAY boundaries (2.9h / 3.0h / 3.1h / 5.0h / 5.1h) -------------------


def test_delay_2h9_only_voucher_with_amount():
    decisions = policy.delay_decisions(delayed_booking(2.9))
    assert {d.action for d in decisions} == {"ISSUE_MEAL_VOUCHER"}
    assert decisions[0].params["amount_inr"] == 500


def test_delay_3h0_still_lower_band_a04():
    # A-04: exactly 3h falls in the lower (under-3h) band, with the amount stated.
    decisions = policy.delay_decisions(delayed_booking(3.0))
    assert {d.action for d in decisions} == {"ISSUE_MEAL_VOUCHER"}
    assert decisions[0].params["amount_inr"] == 500


def test_delay_3h1_gets_lounge_no_amount_stated():
    decisions = policy.delay_decisions(delayed_booking(3.1))
    assert {d.action for d in decisions} == {"ISSUE_MEAL_VOUCHER", "GRANT_LOUNGE"}
    voucher = next(d for d in decisions if d.action == "ISSUE_MEAL_VOUCHER")
    assert "amount_inr" not in voucher.params
    assert "500" not in " ".join(voucher.customer_facing_facts)


def test_delay_5h0_still_3_to_5_band_a04():
    # A-04: exactly 5h falls in the lower (3-5h) band, not the hotel band.
    decisions = policy.delay_decisions(delayed_booking(5.0))
    assert {d.action for d in decisions} == {"ISSUE_MEAL_VOUCHER", "GRANT_LOUNGE"}


def test_delay_5h1_gets_hotel_offer_not_lounge():
    # A-03: exclusive tiers — over-5h band is voucher + hotel offer, no lounge.
    decisions = policy.delay_decisions(delayed_booking(5.1))
    assert {d.action for d in decisions} == {"ISSUE_MEAL_VOUCHER", "OFFER_HOTEL"}


def test_delay_6h_exclusive_tier_gets_no_lounge():
    # Explicit case from the testing requirements: a 6h delay, read
    # literally, also satisfies "more than 3 hours" — the exclusive-tier
    # interpretation (A-03) means lounge is NOT added on top of the hotel offer.
    decisions = policy.delay_decisions(delayed_booking(6.0))
    actions = {d.action for d in decisions}
    assert "GRANT_LOUNGE" not in actions
    assert actions == {"ISSUE_MEAL_VOUCHER", "OFFER_HOTEL"}


def test_delay_amount_500_stated_only_under_3h():
    for hours in (1.0, 2.9, 3.0):
        voucher = next(d for d in policy.delay_decisions(delayed_booking(hours)) if d.action == "ISSUE_MEAL_VOUCHER")
        assert voucher.params.get("amount_inr") == 500, f"{hours}h should state the amount"
    for hours in (3.1, 4.0, 5.0, 5.1, 6.0):
        voucher = next(d for d in policy.delay_decisions(delayed_booking(hours)) if d.action == "ISSUE_MEAL_VOUCHER")
        assert "amount_inr" not in voucher.params, f"{hours}h should NOT state the amount"


def test_delay_hotel_offer_not_auto_booked():
    decisions = policy.delay_decisions(delayed_booking(6.0))
    hotel = next(d for d in decisions if d.action == "OFFER_HOTEL")
    assert hotel.status == "offer"


def test_delay_boundary_offers_escalation_review_once():
    ctx = SessionContext()
    result = run("TR1190B", [intent("compensation_query", "what do I get")], ctx=ctx)
    # Arvind is delayed 4h in the seed data, not a boundary case — use a
    # synthetic booking-driven check instead via the internal helper.
    decisions = policy.delay_decisions(delayed_booking(3.0))
    assert not any(d.action == "OFFER_ESCALATION" for d in decisions)  # not part of delay_decisions itself
    boundary_ctx = SessionContext()
    offer = policy._offer_delay_boundary_review(boundary_ctx)
    assert len(offer) == 1
    assert offer[0].action == "OFFER_ESCALATION"
    assert offer[0].rule_id == "R-DELAY"
    assert "A-04" in offer[0].assumption_ids
    assert "delay_band_boundary" in boundary_ctx.pending_offers
    # Offering again does nothing further (offered once).
    assert policy._offer_delay_boundary_review(boundary_ctx) == []


def test_delay_boundary_review_accept_escalates_via_choice():
    ctx = SessionContext(pending_offers={"delay_band_boundary": {"rule_id": "R-DELAY"}})
    result = policy.resolve_choice("beyond:delay_band_boundary:accept", customer("TR1190B"), bookings("TR1190B"), fare_quotes("TR1190B"), ctx)
    assert result.decisions[0].action == "ESCALATE"
    assert "delay_band_boundary" not in result.session_ctx.pending_offers


# --- Scenario 1: Priya Nair (Gold, SK4821X, SK-204 cancelled) -------------


def test_priya_status_query_offers_rebook_or_refund_no_flight_list():
    result = run("SK4821X", [intent("status_query", "what happened to my flight")])
    actions = {d.action for d in result.decisions}
    assert actions == {"STATUS", "OFFER_OPTIONS"}
    offer = next(d for d in result.decisions if d.action == "OFFER_OPTIONS")
    assert offer.rule_id == "R-CANCEL"
    assert offer.status == "offer"
    assert offer.params == {}  # A-01/A-10: no flight, no window, ever computed


def test_priya_asking_about_return_leg_by_route_targets_return_not_cancelled_leg():
    # Regression: "goa to delhi" and "delhi to goa" are different legs on
    # the same PNR — asking about the return must never answer about SK-204.
    result = run("SK4821X", [intent("status_query", "what are the details of upcoming flight goa to delhi")])
    assert len(result.decisions) == 1
    status = result.decisions[0]
    assert status.action == "STATUS"
    assert status.params["status"] == "scheduled"
    facts = " ".join(status.customer_facing_facts)
    assert "Goa" in facts and "Delhi" in facts
    assert "cancelled" not in facts.lower()
    # No cancel-offer should fire for the unaffected leg.
    assert not any(d.action == "OFFER_OPTIONS" for d in result.decisions)


def test_priya_asking_about_outbound_leg_by_route_still_targets_cancelled_leg():
    result = run("SK4821X", [intent("status_query", "what happened to the delhi to goa flight")])
    actions = {d.action for d in result.decisions}
    assert actions == {"STATUS", "OFFER_OPTIONS"}
    status = next(d for d in result.decisions if d.action == "STATUS")
    assert status.params["status"] == "cancelled"


def test_priya_asking_about_leg_by_flight_number_targets_that_leg():
    result = run("SK4821X", [intent("status_query", "any update on SK-204")])
    status = next(d for d in result.decisions if d.action == "STATUS")
    assert status.params["flight"] == "SK-204"
    assert status.params["status"] == "cancelled"


def test_priya_refund_shows_confirmation_and_return_leg_note():
    result = run("SK4821X", [intent("refund", "I want a full cash refund", refund_method_mentioned="cash")])
    actions = [d.action for d in result.decisions]
    assert actions == ["CONFIRM_REFUND", "INFORM_RETURN_LEG"]
    confirm = result.decisions[0]
    assert confirm.status == "confirm"
    assert confirm.params["flight"] == "SK-204"
    assert "cash" not in " ".join(confirm.customer_facing_facts).lower()  # A-05: never names/echoes a method
    inform = result.decisions[1]
    assert inform.rule_id == "R-RETURN"
    assert inform.status == "inform"
    assert "A-06" in inform.assumption_ids
    assert "Goa" in inform.customer_facing_facts[0] and "Delhi" in inform.customer_facing_facts[0]


def test_priya_refund_not_executed_until_confirmed():
    result = run("SK4821X", [intent("refund", "a full cash refund", refund_method_mentioned="cash")])
    assert not any(d.action == "REFUND" for d in result.decisions)


def test_priya_confirming_refund_executes_scoped_to_sk204():
    result = policy.resolve_choice("refund:confirm", customer("SK4821X"), bookings("SK4821X"), fare_quotes("SK4821X"), EMPTY_CTX)
    refund = result.decisions[0]
    assert refund.action == "REFUND"
    assert refund.rule_id == "R-REFUND"
    assert refund.params["flight"] == "SK-204"  # A-11: scoped to the cancelled flight only
    assert refund.params["method"] == "original"
    assert "A-11" in refund.assumption_ids and "A-15" in refund.assumption_ids


def test_priya_insisting_on_cash_specifically_escalates():
    result = run("SK4821X", [intent("refund_different_method", "no, cash specifically", refund_method_mentioned="cash")])
    d = result.decisions[0]
    assert d.action == "ESCALATE"
    assert d.rule_id == "R-REFUND-METHOD"
    assert "A-05" in d.assumption_ids


def test_priya_upgrade_first_ask_explains_then_offers_escalation():
    result = run("SK4821X", [intent("request_upgrade", "a free business-class upgrade")])
    actions = [d.action for d in result.decisions]
    assert actions == ["EXPLAIN_INELIGIBLE", "OFFER_ESCALATION"]
    assert all(d.rule_id == "R-BEYOND" for d in result.decisions)
    assert "request_upgrade" in result.session_ctx.pending_offers


def test_priya_upgrade_insisting_escalates():
    ctx = SessionContext(pending_offers={"request_upgrade": {"rule_id": "R-BEYOND", "requested": "upgrade"}})
    result = run("SK4821X", [intent("request_upgrade", "I still want the upgrade")], ctx=ctx)
    assert len(result.decisions) == 1
    assert result.decisions[0].action == "ESCALATE"
    assert "request_upgrade" not in result.session_ctx.pending_offers


def test_priya_return_leg_change_escalates():
    result = run("SK4821X", [intent("change_return_flight", "cancel my return flight too")])
    d = result.decisions[0]
    assert d.action == "ESCALATE"
    assert d.rule_id == "R-RETURN"
    assert "A-06" in d.assumption_ids


def test_priya_choosing_rebook_never_states_a_flight_number():
    result = policy.resolve_choice("cancel:rebook", customer("SK4821X"), bookings("SK4821X"), fare_quotes("SK4821X"), EMPTY_CTX)
    d = result.decisions[0]
    assert d.action == "REBOOK_REQUEST"
    assert d.rule_id == "R-REBOOK"
    assert d.params["priority_rule_id"] == "R-TIER"  # Gold tier
    import re
    facts_text = " ".join(d.customer_facing_facts)
    assert not re.search(r"\bSK-\d+\b", facts_text)  # A-01: no flight number is ever invented


# --- Scenario 2: Arvind Kulkarni (Silver, TR1190B, SK-118 delayed 4h) ------


def test_arvind_delay_gets_voucher_and_lounge_no_hotel():
    result = run("TR1190B", [intent("compensation_query", "what do I get for this delay")])
    assert {d.action for d in result.decisions} == {"ISSUE_MEAL_VOUCHER", "GRANT_LOUNGE"}


def test_arvind_hotel_request_under_5h_is_beyond_policy():
    result = run("TR1190B", [intent("request_hotel", "I want a hotel since it's such a long delay")])
    actions = [d.action for d in result.decisions]
    assert actions == ["EXPLAIN_INELIGIBLE", "OFFER_ESCALATION"]
    assert all(d.rule_id == "R-BEYOND" for d in result.decisions)


def test_arvind_refund_request_explains_not_cancelled_never_silent():
    # Regression: a delayed (not cancelled) customer asking for a refund as
    # their first message must never produce zero decisions — that leaves
    # the reply-writer with nothing to say and the customer with silence.
    result = run("TR1190B", [intent("refund", "I want a refund")])
    assert len(result.decisions) == 1
    d = result.decisions[0]
    assert d.action == "EXPLAIN_INELIGIBLE"
    assert d.rule_id == "R-CANCEL"
    assert "SK-118" in d.customer_facing_facts[0]
    assert "delayed" in d.customer_facing_facts[0].lower()


def test_arvind_rebook_request_explains_not_cancelled_never_silent():
    result = run("TR1190B", [intent("rebook", "please just rebook me")])
    assert len(result.decisions) == 1
    assert result.decisions[0].action == "EXPLAIN_INELIGIBLE"
    assert result.decisions[0].rule_id == "R-CANCEL"


def test_meher_lounge_request_still_beyond_over_5h_band():
    # Sanity check the fixed request_lounge branch doesn't regress the
    # already-correct over-5h case (no lounge in that band, A-03).
    result = run("WL7742", [intent("request_lounge", "can I get lounge access")])
    assert [d.action for d in result.decisions] == ["EXPLAIN_INELIGIBLE", "OFFER_ESCALATION"]


def test_arvind_lounge_request_as_first_message_grants_it_directly():
    # Regression: Arvind's 4h delay band *includes* lounge access — asking
    # for it directly, before any status/compensation query ever ran in
    # this session, must grant it via R-DELAY, not treat it as an exception.
    result = run("TR1190B", [intent("request_lounge", "can I get lounge access please")])
    assert {d.action for d in result.decisions} == {"ISSUE_MEAL_VOUCHER", "GRANT_LOUNGE"}
    assert all(d.rule_id == "R-DELAY" for d in result.decisions)


def test_meher_hotel_request_as_first_message_grants_offer_directly():
    # Regression: Meher's 6h delay entitles her to a hotel offer — asking
    # for it directly (skipping any prior compensation_query) must not
    # silently do nothing just because the band already "qualifies".
    result = run("WL7742", [intent("request_hotel", "I need a hotel for this delay")])
    assert {d.action for d in result.decisions} == {"ISSUE_MEAL_VOUCHER", "OFFER_HOTEL"}
    assert all(d.rule_id == "R-DELAY" for d in result.decisions)


def test_meher_hotel_request_after_already_granted_is_a_quiet_noop():
    existing = [
        ActionRecord(id=1, session_id="s", pnr="WL7742", type="ISSUE_MEAL_VOUCHER", params={}, rule_id="R-DELAY", status="issued", created_at=datetime.now()),
        ActionRecord(id=2, session_id="s", pnr="WL7742", type="OFFER_HOTEL", params={}, rule_id="R-DELAY", status="offered", created_at=datetime.now()),
    ]
    result = run("WL7742", [intent("request_hotel", "can I get a hotel")], existing=existing)
    assert result.decisions == []


def test_arvind_no_tier_priority_for_silver():
    result = policy.resolve_choice(
        "cancel:rebook",
        customer("SK4821X").model_copy(update={"tier": "Silver"}),
        bookings("SK4821X"), fare_quotes("SK4821X"), EMPTY_CTX,
    )
    assert "priority_rule_id" not in result.decisions[0].params


def test_missed_flight_always_escalates_no_clarifying_question():
    # understand.py is responsible for only emitting missed_flight for a
    # genuine flight-connection issue — by the time policy.py sees it,
    # there's no ambiguity left to ask about.
    result = run("TR1190B", [intent("missed_flight", "I missed my connecting flight because of this")])
    assert len(result.decisions) == 1
    d = result.decisions[0]
    assert d.action == "ESCALATE"
    assert d.rule_id == "R-NONAIRLINE"


# --- Scenario 3: Meher Kaur (Platinum, WL7742, SK-305 delayed 6h) ---------


def test_meher_delay_gets_voucher_and_hotel_offer_no_lounge():
    result = run("WL7742", [intent("compensation_query", "what do I get")])
    assert {d.action for d in result.decisions} == {"ISSUE_MEAL_VOUCHER", "OFFER_HOTEL"}
    hotel = next(d for d in result.decisions if d.action == "OFFER_HOTEL")
    assert hotel.status == "offer"
    assert hotel.params["window"] == "14:00–20:00"
    assert "A-03" in hotel.assumption_ids


def test_meher_accepting_hotel_offer_books_it():
    result = policy.resolve_choice("hotel:accept", customer("WL7742"), bookings("WL7742"), fare_quotes("WL7742"), EMPTY_CTX)
    d = result.decisions[0]
    assert d.action == "BOOK_HOTEL_DELAYED_HOURS"
    assert d.params["window"] == "14:00–20:00"


def test_meher_declining_hotel_offer_books_nothing():
    result = policy.resolve_choice("hotel:decline", customer("WL7742"), bookings("WL7742"), fare_quotes("WL7742"), EMPTY_CTX)
    assert result.decisions[0].action == "EXPLAIN_INELIGIBLE"
    assert result.decisions[0].status == "decline"


def test_meher_full_night_hotel_is_beyond_policy():
    result = run("WL7742", [intent("request_full_night_hotel", "I want a full night's stay")])
    assert [d.action for d in result.decisions] == ["EXPLAIN_INELIGIBLE", "OFFER_ESCALATION"]


def test_meher_lounge_request_over_5h_is_beyond_policy():
    result = run("WL7742", [intent("request_lounge", "can I at least get lounge access")])
    assert [d.action for d in result.decisions] == ["EXPLAIN_INELIGIBLE", "OFFER_ESCALATION"]
    assert result.decisions[0].params["topic"] == "request_lounge"


def test_meher_fare_quote_uses_fare_quotes_table_not_invented():
    result = run("WL7742", [intent("change_flight_higher_fare", "move me to the other flight instead")])
    d = result.decisions[0]
    assert d.action == "QUOTE_FARE_DIFFERENCE"
    assert d.rule_id == "R-FARE"
    assert d.params["fare_diff_inr"] == 2000
    assert "A-12" in d.assumption_ids and "A-13" in d.assumption_ids


def test_meher_choosing_pay_hands_off_not_executed_by_agent():
    # A-13: a paid flight change is not on the allowed-actions list.
    result = policy.resolve_choice("fare:pay", customer("WL7742"), bookings("WL7742"), fare_quotes("WL7742"), EMPTY_CTX)
    d = result.decisions[0]
    assert d.action == "HANDOFF"
    assert d.status == "escalate"
    assert d.rule_id == "R-FARE"


def test_meher_choosing_waiver_escalates_to_supervisor():
    result = policy.resolve_choice("fare:waiver", customer("WL7742"), bookings("WL7742"), fare_quotes("WL7742"), EMPTY_CTX)
    d = result.decisions[0]
    assert d.action == "ESCALATE"
    assert d.rule_id == "R-FARE"
    assert "A-07" in d.assumption_ids


def test_fare_waiver_direct_request_always_escalates_agent_has_no_authority():
    # A-07: the agent never self-waives, regardless of amount.
    result = run("WL7742", [intent("request_fare_waiver", "please just waive it")])
    d = result.decisions[0]
    assert d.action == "ESCALATE"
    assert d.rule_id == "R-FARE"
    assert "A-07" in d.assumption_ids


def test_fare_waiver_escalates_identically_above_and_at_1500():
    quote_1500 = [FareQuote(id=1, pnr="ZZ", description="x", fare_diff_inr=1500, source="test")]
    quote_1501 = [FareQuote(id=2, pnr="ZZ", description="x", fare_diff_inr=1501, source="test")]
    d_1500 = policy._fare_waiver_escalate_decision(quote_1500)
    d_1501 = policy._fare_waiver_escalate_decision(quote_1501)
    assert d_1500.action == d_1501.action == "ESCALATE"
    assert d_1500.rule_id == d_1501.rule_id == "R-FARE"


def test_no_fare_quote_available_hands_off_rather_than_inventing_one():
    result = run("TR1190B", [intent("change_flight_higher_fare", "move me to a different flight")])
    d = result.decisions[0]
    assert d.action == "HANDOFF"
    assert d.rule_id == "R-FARE"


# --- R-PRIVACY / R-LEGAL / idempotency --------------------------------------


def test_other_passenger_query_refused():
    result = run("SK4821X", [intent("other_passenger_query", "what about WL7742's booking", other_pnr_or_name="WL7742")])
    d = result.decisions[0]
    assert d.action == "REFUSE_PRIVACY"
    assert d.rule_id == "R-PRIVACY"


def test_legal_threat_flag_escalates_immediately():
    result = run("SK4821X", [intent("status_query", "hi")], legal_threat=True)
    legal = next(d for d in result.decisions if d.rule_id == "R-LEGAL")
    assert legal.action == "ESCALATE"


def test_already_granted_delay_actions_not_reissued():
    existing = [
        ActionRecord(id=1, session_id="s", pnr="TR1190B", type="ISSUE_MEAL_VOUCHER", params={}, rule_id="R-DELAY", status="issued", created_at=datetime.now()),
        ActionRecord(id=2, session_id="s", pnr="TR1190B", type="GRANT_LOUNGE", params={}, rule_id="R-DELAY", status="issued", created_at=datetime.now()),
    ]
    result = run("TR1190B", [intent("compensation_query", "what did I get again?")], existing=existing)
    assert result.decisions == []


def test_denied_topic_does_not_reescalate_on_repeated_pressure():
    ctx = SessionContext(resolved_topics={"request_upgrade": "denied"})
    result = run("SK4821X", [intent("request_upgrade", "I really want that upgrade")], ctx=ctx)
    assert len(result.decisions) == 1
    assert result.decisions[0].action == "EXPLAIN_INELIGIBLE"
    assert result.decisions[0].status == "decline"


def test_prior_complaint_history_never_changes_entitlement():
    # Meher's history includes a prior complaint resolved with a tier
    # upgrade; that context must not change what R-DELAY grants her.
    assert "1 prior complaint" in customer("WL7742").history
    result = run("WL7742", [intent("compensation_query", "what do I get")])
    assert {d.action for d in result.decisions} == {"ISSUE_MEAL_VOUCHER", "OFFER_HOTEL"}
