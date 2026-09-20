"""End-to-end scenario tests. LLM is mocked: each turn's Understanding is a
hand-built fixture standing in for step 1 (understand.py never runs), and
the reply-writer is replaced with templates.build_reply so no network call
happens either. Everything else — guards, policy, executor, validator, the
in-memory store — is the real code, run through
api.index.run_chat_turn / run_choice_turn exactly as production would call it.

Asserts the exact actions, escalations (and their kinds), buttons and
final session state for all three required scenarios.
"""

from __future__ import annotations

from agent.templates import build_reply
from api.index import _apply_escalation_outcome, run_chat_turn, run_choice_turn
from schemas import Intent, IntentDetails, Understanding
from tests.fakes import InMemoryStore


def u(*intents: Intent, **kwargs) -> Understanding:
    return Understanding(intents=list(intents), **kwargs)


def intent(type_: str, quote: str, **details) -> Intent:
    return Intent(type=type_, quote=quote, details=IntentDetails(**details))


def login(store: InMemoryStore, pnr: str, last_name: str):
    customer, bookings = store.authenticate(pnr, last_name)
    session = store.create_session(pnr)
    fare_quotes = store.get_fare_quotes(pnr)
    return session, customer, bookings, fare_quotes


FAKE_RESPOND = lambda decisions, name, sentiment, language: build_reply(decisions, name, sentiment)  # noqa: E731


# --- Scenario 1: Priya Nair (Gold, SK4821X, SK-204 cancelled) --------------


def test_scenario_priya_full_flow():
    store = InMemoryStore()
    session, customer, bookings, fare_quotes = login(store, "SK4821X", "Nair")

    # 1) Asks about SK-204 -> status + rebook/refund offer as buttons, no flight list.
    r1 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("status_query", "what happened to my flight")),
        "what happened to my flight", respond_fn=FAKE_RESPOND,
    )
    assert {d.action for d in r1["decisions"]} == {"STATUS", "OFFER_OPTIONS"}
    assert {c.choice_id for c in r1["choices"]} == {"cancel:rebook", "cancel:refund"}
    assert r1["actions"] == []
    session = store.get_session(session.id)

    # 2) "I'm furious, I want a full cash refund" -> confirmation shown
    # (not executed yet), return-leg note included, single confirm button.
    r2 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("refund", "a full cash refund", refund_method_mentioned="cash"), sentiment="angry"),
        "I'm furious, I want a full cash refund", respond_fn=FAKE_RESPOND,
    )
    assert [d.action for d in r2["decisions"]] == ["CONFIRM_REFUND", "INFORM_RETURN_LEG"]
    assert {c.choice_id for c in r2["choices"]} == {"refund:confirm"}
    assert r2["actions"] == []  # not executed until confirmed
    assert "A-06" in r2["assumptions"]
    session = store.get_session(session.id)

    # 3) Confirms -> refund initiated, scoped to SK-204 only (A-11).
    r3 = run_choice_turn(store, session, customer, bookings, fare_quotes, "refund:confirm", respond_fn=FAKE_RESPOND)
    refund_action = r3["actions"][0]
    assert refund_action.type == "REFUND"
    assert refund_action.params["flight"] == "SK-204"
    assert refund_action.params["method"] == "original"
    assert refund_action.status == "initiated"
    session = store.get_session(session.id)

    # 4) "...plus a free business-class upgrade on my return" -> explained
    # as beyond policy, escalation offered.
    r4 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("request_upgrade", "a free business-class upgrade on my return")),
        "plus a free business-class upgrade on my return", respond_fn=FAKE_RESPOND,
    )
    assert [d.action for d in r4["decisions"]] == ["EXPLAIN_INELIGIBLE", "OFFER_ESCALATION"]
    assert {c.choice_id for c in r4["choices"]} == {"beyond:request_upgrade:accept", "beyond:request_upgrade:decline"}
    session = store.get_session(session.id)
    assert "request_upgrade" in session.context.pending_offers

    # She insists -> escalates, kind "approval" (an exception a supervisor judges).
    r5 = run_choice_turn(store, session, customer, bookings, fare_quotes, "beyond:request_upgrade:accept", respond_fn=FAKE_RESPOND)
    assert r5["decisions"][0].action == "ESCALATE"
    assert len(r5["escalations"]) == 1
    upgrade_escalation = r5["escalations"][0]
    assert upgrade_escalation.kind == "approval"
    assert upgrade_escalation.rule_id == "R-BEYOND"
    assert upgrade_escalation.packet.customer.tier == "Gold"
    assert any("Refund initiated" in line for line in upgrade_escalation.packet.already_done)
    session = store.get_session(session.id)

    # 5) Asks to cancel/refund the return leg -> escalated, kind "handoff" (A-06).
    r6 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("change_return_flight", "can you cancel my return flight too")),
        "can you cancel my return flight too", respond_fn=FAKE_RESPOND,
    )
    assert r6["decisions"][0].action == "ESCALATE"
    assert r6["decisions"][0].rule_id == "R-RETURN"
    assert "A-06" in r6["decisions"][0].assumption_ids

    # Final state: exactly one REFUND action, two escalations total.
    final_actions = store.get_actions_for_pnr("SK4821X")
    assert [a.type for a in final_actions] == ["REFUND"]
    all_escalations = store.get_escalations_for_pnr("SK4821X")
    assert {(e.rule_id, e.kind) for e in all_escalations} == {("R-BEYOND", "approval"), ("R-RETURN", "handoff")}

    from audit import verify_chain
    assert verify_chain(store.get_all_audit_entries()) is True


def test_scenario_priya_choosing_rebook_instead_states_no_flight_number():
    store = InMemoryStore()
    session, customer, bookings, fare_quotes = login(store, "SK4821X", "Nair")
    run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("status_query", "what happened")),
        "what happened", respond_fn=FAKE_RESPOND,
    )
    session = store.get_session(session.id)
    r = run_choice_turn(store, session, customer, bookings, fare_quotes, "cancel:rebook", respond_fn=FAKE_RESPOND)
    action = r["actions"][0]
    assert action.type == "REBOOK_REQUEST"
    assert action.status == "submitted"
    assert action.params["priority_rule_id"] == "R-TIER"
    import re
    assert not re.search(r"\bSK-\d+\b", r["reply"])


# --- Scenario 2: Arvind Kulkarni (Silver, TR1190B, SK-118 delayed 4h) ------


def test_scenario_arvind_full_flow():
    store = InMemoryStore()
    session, customer, bookings, fare_quotes = login(store, "TR1190B", "Kulkarni")

    # Frustrated about the delay -> voucher (no amount) + lounge issued directly.
    r1 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("compensation_query", "what do I get for this delay"), sentiment="frustrated"),
        "what do I get for this delay", respond_fn=FAKE_RESPOND,
    )
    assert {d.action for d in r1["decisions"]} == {"ISSUE_MEAL_VOUCHER", "GRANT_LOUNGE"}
    voucher = next(d for d in r1["decisions"] if d.action == "ISSUE_MEAL_VOUCHER")
    assert "amount_inr" not in voucher.params
    assert {a.type for a in r1["actions"]} == {"ISSUE_MEAL_VOUCHER", "GRANT_LOUNGE"}
    session = store.get_session(session.id)

    # "I want a hotel since it's such a long delay" -> beyond policy under 5h.
    r2 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("request_hotel", "I want a hotel since it's such a long delay")),
        "I want a hotel since it's such a long delay", respond_fn=FAKE_RESPOND,
    )
    assert [d.action for d in r2["decisions"]] == ["EXPLAIN_INELIGIBLE", "OFFER_ESCALATION"]
    session = store.get_session(session.id)

    # Insists -> escalates (kind "approval").
    r3 = run_choice_turn(store, session, customer, bookings, fare_quotes, "beyond:request_hotel_under_5h:accept", respond_fn=FAKE_RESPOND)
    assert r3["decisions"][0].action == "ESCALATE"
    escalation = r3["escalations"][0]
    assert escalation.kind == "approval"
    assert any("Meal voucher issued" in line for line in escalation.packet.already_done)
    assert any("Lounge access issued" in line for line in escalation.packet.already_done)

    final_actions = store.get_actions_for_pnr("TR1190B")
    assert {a.type for a in final_actions} == {"ISSUE_MEAL_VOUCHER", "GRANT_LOUNGE"}


# --- Scenario 3: Meher Kaur (Platinum, WL7742, SK-305 delayed 6h) ----------


def test_scenario_meher_full_flow():
    store = InMemoryStore()
    session, customer, bookings, fare_quotes = login(store, "WL7742", "Kaur")

    # Asks what she gets -> voucher (no amount) + hotel OFFERED, not booked (A-03: no lounge).
    r1 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("compensation_query", "what do I get")),
        "what do I get", respond_fn=FAKE_RESPOND,
    )
    assert {d.action for d in r1["decisions"]} == {"ISSUE_MEAL_VOUCHER", "OFFER_HOTEL"}
    hotel_offer = next(d for d in r1["decisions"] if d.action == "OFFER_HOTEL")
    assert hotel_offer.status == "offer"
    assert hotel_offer.params["window"] == "14:00–20:00"
    assert {a.type for a in r1["actions"]} == {"ISSUE_MEAL_VOUCHER", "OFFER_HOTEL"}
    session = store.get_session(session.id)

    # Accepts the hotel -> booked.
    r2 = run_choice_turn(store, session, customer, bookings, fare_quotes, "hotel:accept", respond_fn=FAKE_RESPOND)
    assert r2["actions"][0].type == "BOOK_HOTEL_DELAYED_HOURS"
    assert r2["actions"][0].status == "booked"
    session = store.get_session(session.id)

    # Wants a full night's stay -> beyond policy.
    r3 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("request_full_night_hotel", "I want a full night's hotel stay")),
        "I want a full night's hotel stay", respond_fn=FAKE_RESPOND,
    )
    assert [d.action for d in r3["decisions"]] == ["EXPLAIN_INELIGIBLE", "OFFER_ESCALATION"]
    session = store.get_session(session.id)

    r4 = run_choice_turn(store, session, customer, bookings, fare_quotes, "beyond:request_full_night_hotel:accept", respond_fn=FAKE_RESPOND)
    assert r4["decisions"][0].action == "ESCALATE"
    session = store.get_session(session.id)

    # Move to the other flight, fare diff quoted from fare_quotes (₹2,000), no flight invented.
    r5 = run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("change_flight_higher_fare", "move me to the other flight instead")),
        "move me to the other flight instead", respond_fn=FAKE_RESPOND,
    )
    quote = r5["decisions"][0]
    assert quote.action == "QUOTE_FARE_DIFFERENCE"
    assert quote.params["fare_diff_inr"] == 2000
    assert {c.choice_id for c in r5["choices"]} == {"fare:pay", "fare:waiver"}
    session = store.get_session(session.id)

    # She wants it waived -> agent has no authority (A-07), escalates to a supervisor.
    r6 = run_choice_turn(store, session, customer, bookings, fare_quotes, "fare:waiver", respond_fn=FAKE_RESPOND)
    waiver_decision = r6["decisions"][0]
    assert waiver_decision.action == "ESCALATE"
    assert waiver_decision.rule_id == "R-FARE"
    assert "A-07" in waiver_decision.assumption_ids
    fare_escalation = r6["escalations"][0]
    assert fare_escalation.kind == "approval"
    assert fare_escalation.packet.customer.tier == "Platinum"
    # A-08: hotel already booked, question for the human, not a new rule.
    assert any(note.startswith("A-08") for note in fare_escalation.packet.notes)

    final_actions = store.get_actions_for_pnr("WL7742")
    assert {a.type for a in final_actions} == {"ISSUE_MEAL_VOUCHER", "OFFER_HOTEL", "BOOK_HOTEL_DELAYED_HOURS"}
    all_escalations = store.get_escalations_for_pnr("WL7742")
    assert {e.rule_id for e in all_escalations} == {"R-BEYOND", "R-FARE"}


def test_scenario_meher_choosing_pay_is_a_handoff_not_agent_executed():
    store = InMemoryStore()
    session, customer, bookings, fare_quotes = login(store, "WL7742", "Kaur")
    run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("change_flight_higher_fare", "move me")),
        "move me", respond_fn=FAKE_RESPOND,
    )
    session = store.get_session(session.id)
    r = run_choice_turn(store, session, customer, bookings, fare_quotes, "fare:pay", respond_fn=FAKE_RESPOND)
    d = r["decisions"][0]
    assert d.action == "HANDOFF"
    assert d.status == "escalate"
    escalation = r["escalations"][0]
    assert escalation.kind == "handoff"


# --- Supervisor approval / completion loop ----------------------------------


def test_supervisor_approves_fare_waiver_creates_waived_rebook_request():
    store = InMemoryStore()
    session, customer, bookings, fare_quotes = login(store, "WL7742", "Kaur")
    run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("change_flight_higher_fare", "move me")),
        "move me", respond_fn=FAKE_RESPOND,
    )
    session = store.get_session(session.id)
    r = run_choice_turn(store, session, customer, bookings, fare_quotes, "fare:waiver", respond_fn=FAKE_RESPOND)
    escalation = r["escalations"][0]

    action = _apply_escalation_outcome(store, escalation, "approve")
    assert action.type == "REBOOK_REQUEST"
    assert action.params["waived"] is True
    assert action.params["fare_diff_inr"] == 2000
    assert action.params["charge"] == "none"


def test_supervisor_completes_paid_fare_handoff():
    store = InMemoryStore()
    session, customer, bookings, fare_quotes = login(store, "WL7742", "Kaur")
    run_chat_turn(
        store, session, customer, bookings, fare_quotes,
        u(intent("change_flight_higher_fare", "move me")),
        "move me", respond_fn=FAKE_RESPOND,
    )
    session = store.get_session(session.id)
    r = run_choice_turn(store, session, customer, bookings, fare_quotes, "fare:pay", respond_fn=FAKE_RESPOND)
    escalation = r["escalations"][0]
    assert escalation.kind == "handoff"

    action = _apply_escalation_outcome(store, escalation, "complete")
    assert action.type == "REBOOK_REQUEST"
    assert action.params["charge"] == "paid"
    assert action.params["completed_by_human"] is True
