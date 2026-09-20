"""Deterministic reply builder. Pure, no LLM.

Used when respond.py's LLM-written reply fails validator.py twice in a row
(an LLM-provider rate limit or outage, or a reply the validator can't
reconcile with the decisions). Built directly from the same Decision
objects the LLM would have seen, so it can never say more than was
actually decided.
"""

from __future__ import annotations

from backend.agent.policy import status_decision
from backend.schemas import Booking, Customer, Decision, Sentiment

_EMPATHY = {
    "frustrated": "I understand this has been frustrating.",
    "angry": "I'm sorry for the trouble this has caused.",
}


def build_reply(decisions: list[Decision], customer_name: str, sentiment: Sentiment) -> str:
    first_name = customer_name.strip().split()[0] if customer_name.strip() else ""
    lines: list[str] = []

    empathy = _EMPATHY.get(sentiment)
    if empathy:
        lines.append(f"{empathy[:-1]}, {first_name}." if first_name else empathy)

    asked = False
    for d in decisions:
        if d.action == "ASK":
            question = d.params.get("question")
            if question and not asked:
                lines.append(str(question))
                asked = True
            continue
        lines.extend(d.customer_facing_facts)

    if not lines:
        return "Thanks for reaching out — let me look into that for you."
    return " ".join(lines)


def build_welcome_message(customer: Customer, bookings: list[Booking]) -> str:
    """The first message the customer sees right after logging in, before
    they've typed anything. Deterministic, not LLM-written — it only ever
    restates the customer's own real booking status (the same fact
    R-STATUS would give in reply to a status_query), so there's nothing
    here that needs validating against a decision.
    """
    first_name = customer.name.strip().split()[0] if customer.name.strip() else ""
    greeting = f"Hi {first_name}, thanks for reaching out." if first_name else "Thanks for reaching out."

    disrupted = next((b for b in bookings if b.status in ("cancelled", "delayed")), None)
    if disrupted is None:
        return f"{greeting} How can I help you today?"

    status_fact = status_decision(disrupted).customer_facing_facts[0]
    return f"{greeting} {status_fact} How can I help you today?"
