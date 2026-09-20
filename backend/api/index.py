"""FastAPI app entry. Deployed as a Vercel serverless function (this
module exports ``app`` directly); run locally with
``uvicorn backend.api.index:app --reload --port 8000``.

Route handlers stay thin: authenticate, load rows, call ``run_chat_turn`` /
``run_choice_turn`` (the shared pipeline orchestration below), serialize.
Those two functions are plain Python — no FastAPI/HTTP involved — so
``backend/tests/test_scenarios.py`` and ``test_redteam.py`` call them
directly against an in-memory store with a hand-built ``Understanding``,
never touching the network or a real database.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

# backend/.env.local; resolved relative to this file so it loads regardless
# of the working directory `uvicorn` was started from. A no-op if the file
# is absent (e.g. on Vercel, where real env vars are set on the platform).
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

from backend.agent import executor, guards, policy, respond, templates, validator
from backend.agent.understand import understand
from backend.audit import verify_chain
from backend.db import PostgresStore, Store, get_connection
from backend.schemas import (
    ActionRecord,
    Booking,
    Choice,
    Customer,
    Decision,
    Escalation,
    FareQuote,
    Language,
    Sentiment,
    Session,
    Understanding,
)

app = FastAPI(title="SkyAssist backend")

# The browser is only ever supposed to reach this API through the frontend's
# own origin — frontend/next.config.ts proxies /api/* to this service
# server-side, so a legitimate request never carries a cross-origin browser
# Origin header at all. This restricts the one thing that does: a *different*
# website's JS trying to call this API directly from a visitor's browser.
# It does NOT stop a direct curl/script call (CORS is a browser-enforced
# concept, not a server-side access check) — see CLAUDE.md for that caveat
# and the stronger option (a shared-secret header) if it's ever needed.
# Configurable so the deployed frontend origin doesn't have to be hardcoded;
# defaults cover local dev only.
_default_origins = "http://localhost:3000,http://127.0.0.1:3000"
_allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Supervisor-Passcode"],
)


# --- dependencies -------------------------------------------------------------


def get_store():
    with get_connection() as conn:
        yield PostgresStore(conn)


def require_supervisor(x_supervisor_passcode: str = Header(default="")):
    expected = os.environ.get("SUPERVISOR_PASSCODE")
    if not expected or x_supervisor_passcode != expected:
        raise HTTPException(status_code=401, detail="Invalid supervisor passcode")


# --- pipeline orchestration ---------------------------------------------------


def _build_choices(decisions: list[Decision]) -> list[Choice]:
    choices: list[Choice] = []
    for d in decisions:
        if d.status not in ("offer", "confirm"):
            continue
        if d.rule_id == "R-CANCEL" and d.action == "OFFER_OPTIONS":
            choices.append(Choice(choice_id="cancel:rebook", label="Rebook on next available flight"))
            choices.append(Choice(choice_id="cancel:refund", label="Full refund"))
        elif d.action == "CONFIRM_REFUND":
            choices.append(Choice(choice_id="refund:confirm", label="Confirm refund"))
        elif d.action == "OFFER_HOTEL":
            choices.append(Choice(choice_id="hotel:accept", label="Yes, book the hotel"))
            choices.append(Choice(choice_id="hotel:decline", label="No thanks"))
        elif d.action == "OFFER_ESCALATION":
            topic = d.params["topic"]
            choices.append(Choice(choice_id=f"beyond:{topic}:accept", label="Yes, escalate"))
            choices.append(Choice(choice_id=f"beyond:{topic}:decline", label="No thanks"))
        elif d.action == "QUOTE_FARE_DIFFERENCE":
            choices.append(Choice(choice_id="fare:pay", label="Pay the difference"))
            choices.append(Choice(choice_id="fare:waiver", label="Request a waiver"))
    return choices


def _assumptions_applied(decisions: list[Decision]) -> list[str]:
    return sorted({aid for d in decisions for aid in d.assumption_ids})


def _finish_turn(
    store: Store,
    session: Session,
    customer: Customer,
    bookings: list[Booking],
    decisions: list[Decision],
    new_session_ctx,
    sentiment: Sentiment,
    language: Language,
    understanding_for_packet: Understanding,
    existing_before: list[ActionRecord],
    respond_fn: Callable[[list[Decision], str, Sentiment, Language], str],
) -> dict:
    actions, escalations = executor.execute_decisions(
        store, session, customer, bookings, decisions, understanding_for_packet, existing_before
    )
    store.update_session_context(session.id, session.state, new_session_ctx)

    own_flight_numbers = {b.flight_no for b in bookings if b.flight_no}
    reply = respond_fn(decisions, customer.name, sentiment, language)
    if not validator.validate_reply(reply, decisions, own_flight_numbers):
        reply = respond_fn(decisions, customer.name, sentiment, language)
        if not validator.validate_reply(reply, decisions, own_flight_numbers):
            reply = templates.build_reply(decisions, customer.name, sentiment)

    store.add_message(session.id, "agent", reply)
    return {
        "reply": reply,
        "choices": _build_choices(decisions),
        "decisions": decisions,
        "actions": actions,
        "escalations": escalations,
        "assumptions": _assumptions_applied(decisions),
    }


def run_chat_turn(
    store: Store,
    session: Session,
    customer: Customer,
    bookings: list[Booking],
    fare_quotes: list[FareQuote],
    understanding: Understanding,
    raw_message: str,
    respond_fn: Callable[[list[Decision], str, Sentiment, Language], str] = respond.respond,
) -> dict:
    guarded = guards.apply_guards(raw_message, understanding)

    # A tier claimed in chat is always ignored for entitlements (policy.py
    # never reads claimed_tier) but the claim itself is logged; an
    # injection attempt is flagged and logged — the policy engine still
    # only ever acts on real intents.
    if guarded.claimed_tier and guarded.claimed_tier.strip().lower() != customer.tier.lower():
        store.insert_audit(session.id, "claimed_tier_ignored", {"claimed": guarded.claimed_tier, "actual": customer.tier})
    if guarded.injection_attempt:
        store.insert_audit(session.id, "injection_flagged", {"message": raw_message})

    existing_before = store.get_actions_for_pnr(session.pnr)
    result = policy.evaluate(customer, bookings, fare_quotes, guarded, session.context, existing_before)
    return _finish_turn(
        store, session, customer, bookings, result.decisions, result.session_ctx,
        guarded.sentiment, guarded.language, guarded, existing_before, respond_fn,
    )


def run_choice_turn(
    store: Store,
    session: Session,
    customer: Customer,
    bookings: list[Booking],
    fare_quotes: list[FareQuote],
    choice_id: str,
    respond_fn: Callable[[list[Decision], str, Sentiment, Language], str] = respond.respond,
) -> dict:
    # Button clicks skip step 1 (understand.py) — the choice is already structured.
    existing_before = store.get_actions_for_pnr(session.pnr)
    result = policy.resolve_choice(choice_id, customer, bookings, fare_quotes, session.context)
    synthetic = Understanding(sentiment="calm")
    return _finish_turn(
        store, session, customer, bookings, result.decisions, result.session_ctx,
        "calm", "en", synthetic, existing_before, respond_fn,
    )


# --- request/response bodies ---------------------------------------------------


class SessionRequest(BaseModel):
    pnr: str
    last_name: str


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChoiceRequest(BaseModel):
    session_id: str
    choice_id: str


class DecideRequest(BaseModel):
    decision: str  # "approve" | "deny" | "complete"
    note: Optional[str] = None


# --- helpers ---------------------------------------------------------------------


def _load_turn_inputs(store: Store, session_id: str) -> tuple[Session, Customer, list[Booking], list[FareQuote]]:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    customer = store.get_customer_by_pnr(session.pnr)
    bookings = store.get_bookings(session.pnr)
    if customer is None or not bookings:
        raise HTTPException(status_code=404, detail="booking not found")
    fare_quotes = store.get_fare_quotes(session.pnr)
    return session, customer, bookings, fare_quotes


def _collect_assumptions(actions: list[ActionRecord], escalations: list[Escalation]) -> list[str]:
    ids: set[str] = set()
    for a in actions:
        ids.update(a.params.get("assumption_ids") or [])
    for e in escalations:
        for note in e.packet.notes:
            if note.startswith("A-"):
                ids.add(note.split(":")[0].strip())
    return sorted(ids)


# A supervisor's Approve/Complete maps to a concrete action only for the
# rules where the outcome is well-defined; other rules (R-RETURN,
# R-NONAIRLINE, the delay-band-boundary review, ...) have no single correct
# automated action — approving or completing those just posts the note.
_BEYOND_TOPIC_TO_ACTION = {
    "request_upgrade": "GRANT_UPGRADE",
    "request_full_night_hotel": "BOOK_FULL_NIGHT_HOTEL",
    "request_hotel_under_5h": "BOOK_HOTEL_DELAYED_HOURS",
    "request_lounge": "GRANT_LOUNGE",
}


def _apply_escalation_outcome(store: Store, escalation: Escalation, decision: str) -> Optional[ActionRecord]:
    dp = escalation.packet.decision_params
    if decision == "approve" and escalation.rule_id == "R-BEYOND":
        action_type = _BEYOND_TOPIC_TO_ACTION.get(dp.get("topic"))
        if not action_type:
            return None
        return store.upsert_action(
            escalation.session_id, escalation.pnr, action_type,
            {"approved_by_supervisor": True, "topic": dp.get("topic")}, escalation.rule_id,
        )
    if decision == "approve" and escalation.rule_id == "R-FARE":
        return store.upsert_action(
            escalation.session_id, escalation.pnr, "REBOOK_REQUEST",
            {"charge": "none", "waived": True, "fare_diff_inr": dp.get("fare_diff_inr"), "approved_by_supervisor": True},
            escalation.rule_id,
        )
    if decision == "approve" and escalation.rule_id == "R-REFUND-METHOD" and dp.get("requested_method"):
        return store.upsert_action(
            escalation.session_id, escalation.pnr, "REFUND",
            {"amount": "full", "method": dp["requested_method"], "approved_by_supervisor": True},
            escalation.rule_id,
        )
    if decision == "complete" and escalation.rule_id == "R-FARE":
        return store.upsert_action(
            escalation.session_id, escalation.pnr, "REBOOK_REQUEST",
            {"charge": "paid", "fare_diff_inr": dp.get("fare_diff_inr"), "completed_by_human": True},
            escalation.rule_id,
        )
    return None


_VALID_DECISIONS_BY_KIND = {
    "approval": {"approve", "deny"},
    "immediate": {"approve", "deny"},
    "handoff": {"complete"},
}
_STATUS_BY_DECISION = {"approve": "approved", "deny": "denied", "complete": "completed"}


# --- routes ------------------------------------------------------------------------


@app.post("/api/session")
def post_session(body: SessionRequest, store: Store = Depends(get_store)):
    result = store.authenticate(body.pnr.strip(), body.last_name.strip())
    if result is None:
        raise HTTPException(status_code=401, detail="PNR and last name don't match a booking")
    customer, bookings = result
    session = store.create_session(body.pnr.strip())
    store.insert_audit(session.id, "login", {"pnr": body.pnr.strip()})
    store.add_message(session.id, "agent", templates.build_welcome_message(customer, bookings))
    return {
        "session_id": session.id,
        "customer": {
            "name": customer.name,
            "tier": customer.tier,
            "pnr": body.pnr.strip(),
            "history": customer.history,
        },
        "bookings": [b.model_dump() for b in bookings],
    }


@app.post("/api/chat")
def post_chat(body: ChatRequest, store: Store = Depends(get_store)):
    session, customer, bookings, fare_quotes = _load_turn_inputs(store, body.session_id)
    store.add_message(session.id, "customer", body.message)

    raw_understanding = understand(body.message, [m.model_dump() for m in store.get_messages(session.id)[-6:]])
    return run_chat_turn(store, session, customer, bookings, fare_quotes, raw_understanding, body.message)


@app.post("/api/choice")
def post_choice(body: ChoiceRequest, store: Store = Depends(get_store)):
    session, customer, bookings, fare_quotes = _load_turn_inputs(store, body.session_id)
    return run_choice_turn(store, session, customer, bookings, fare_quotes, body.choice_id)


@app.get("/api/session/{session_id}")
def get_session_detail(session_id: str, store: Store = Depends(get_store)):
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = store.get_messages(session_id)
    actions = store.get_actions_for_pnr(session.pnr)
    escalations = store.get_escalations_for_pnr(session.pnr)
    return {
        "messages": [m.model_dump() for m in messages],
        "actions": [a.model_dump() for a in actions],
        "escalations": [e.model_dump() for e in escalations],
        "assumptions": _collect_assumptions(actions, escalations),
    }


@app.get("/api/escalations", dependencies=[Depends(require_supervisor)])
def get_escalations(store: Store = Depends(get_store)):
    all_escalations = store.get_all_escalations()
    pending = [e.model_dump() for e in all_escalations if e.status == "pending"]
    resolved = [e.model_dump() for e in all_escalations if e.status != "pending"]
    return {"pending": pending, "resolved": resolved}


@app.post("/api/escalations/{escalation_id}/decide", dependencies=[Depends(require_supervisor)])
def decide_escalation(escalation_id: int, body: DecideRequest, store: Store = Depends(get_store)):
    escalation = store.get_escalation(escalation_id)
    if escalation is None:
        raise HTTPException(status_code=404, detail="escalation not found")
    if escalation.status != "pending":
        raise HTTPException(status_code=409, detail="escalation already resolved")

    valid_decisions = _VALID_DECISIONS_BY_KIND[escalation.kind]
    if body.decision not in valid_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"a '{escalation.kind}' escalation only accepts: {', '.join(sorted(valid_decisions))}",
        )

    status = _STATUS_BY_DECISION[body.decision]
    updated = store.update_escalation(escalation_id, status, body.note)
    store.insert_audit(escalation.session_id, "escalation_decision", {"id": escalation_id, "decision": body.decision, "note": body.note})

    session = store.get_session(escalation.session_id)
    ctx = session.context
    topic = escalation.packet.decision_params.get("topic")

    if body.decision == "approve":
        _apply_escalation_outcome(store, escalation, body.decision)
        if topic:
            ctx.resolved_topics[topic] = "approved"
        system_text = f"Update on your request to a supervisor: approved. {body.note or ''}".strip()
    elif body.decision == "deny":
        if topic:
            ctx.resolved_topics[topic] = "denied"
        system_text = f"Update on your request to a supervisor: {body.note or 'this could not be approved.'}"
    else:  # complete
        _apply_escalation_outcome(store, escalation, body.decision)
        system_text = f"Update: this has been completed. {body.note or ''}".strip()

    store.update_session_context(session.id, session.state, ctx)
    store.add_message(session.id, "system", system_text)
    return updated.model_dump()


@app.get("/api/audit/verify", dependencies=[Depends(require_supervisor)])
def get_audit_verify(store: Store = Depends(get_store)):
    entries = store.get_all_audit_entries()
    return {"valid": verify_chain(entries), "entries": len(entries)}


@app.post("/api/reset", dependencies=[Depends(require_supervisor)], status_code=204)
def post_reset(store: Store = Depends(get_store)):
    store.reset_and_seed()
    return Response(status_code=204)


@app.get("/api/health")
def get_health(store: Store = Depends(get_store)):
    return {"db": store.ping(), "llm": bool(os.environ.get("GROQ_API_KEY"))}
