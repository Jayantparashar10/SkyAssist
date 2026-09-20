"""Step 1 — Understanding. LLM only: turns a free-text customer message
into structured JSON. It never grants, denies or invents anything —
guards.py and policy.py are the only code allowed to act on what comes out
of here.
"""

from __future__ import annotations

import llm
from schemas import Understanding

SYSTEM_PROMPT = (
    "You convert an airline customer's message into structured JSON. Only extract what "
    "the customer actually said — every intent must include an exact quote (a contiguous "
    "span) copied verbatim from the message. Do not infer facts, amounts, or policy outcomes; "
    "those are decided elsewhere, you only report what was asked.\n\n"
    "A single message often carries more than one distinct ask, typically joined by \"plus\", "
    '"and", "also", or just a comma — extract every one of them as its own intent, each with '
    "its own quote covering only that ask. Do not let the first or most emotional ask crowd out "
    "a second, calmer one later in the same message. For example, "
    '"I\'m furious, I want a full cash refund plus a free upgrade on my return" is TWO intents: '
    'a refund intent (quote: "a full cash refund") and a request_upgrade intent (quote: '
    '"a free upgrade on my return") — never just the first one.\n\n'
    "Intent definitions — pick the single closest one, never a nearby type that only sounds "
    "similar:\n"
    "- status_query: asking what is currently happening with their own booking/flight (is it "
    "delayed, cancelled, what time does it now leave), OR the status of anything they already "
    'asked for (a refund, a rebooking, compensation) — "where is my refund" and "any update on '
    'my rebooking" are status_query, not general_question, even though no new action is being '
    "requested.\n"
    "- compensation_query: asking what they are owed/entitled to for a delay or cancellation "
    '("what do I get", "what am I entitled to"), without naming a specific benefit.\n'
    "- request_upgrade: wants a complimentary upgrade to a better cabin class on the flight "
    "they already hold a seat on. Never about switching to a different flight.\n"
    "- change_flight_higher_fare: wants to move to a DIFFERENT flight than the one booked, and "
    "is open to paying a fare difference. About switching which flight, never about cabin class "
    "or a free/complimentary upgrade.\n"
    "- rebook: explicitly accepting or asking for the airline's free rebooking after a "
    "cancellation.\n"
    "- request_fare_waiver: explicitly asks to have a fare difference waived or not charged.\n"
    "- refund: wants money back for a cancelled flight. A payment method they mention is just a "
    "detail (refund_method_mentioned) — this is NOT refund_different_method unless they "
    "explicitly, separately insist on a method other than the original.\n"
    "- refund_different_method: explicitly insists the refund go to a method other than their "
    "original payment method (a different card, cash in hand, a different account) — not just a "
    'passing mention like "cash refund" inside an ordinary refund ask.\n'
    "- request_hotel: wants hotel accommodation for a delay.\n"
    "- request_full_night_hotel: specifically wants a FULL NIGHT stay, not just coverage for the "
    "delayed hours.\n"
    "- request_lounge: wants lounge access during a delay.\n"
    "- change_return_flight: wants to change, cancel, or get a refund for a leg other than the "
    "one being discussed (e.g. an unaffected return flight).\n"
    "- missed_flight: describes an actual missed or at-risk flight connection (a connecting "
    "flight, layover, onward leg) — never a downstream personal consequence like a missed "
    "meeting or appointment, which gets no intent at all (it is color, not a request; do not "
    "ask a clarifying question about it either).\n"
    "- other_passenger_query: asks about a booking, PNR or person that is not themselves.\n"
    "- general_question / greeting / other: anything else, including small talk and thanks — "
    "never a question about their own booking, flight, or something they already asked for; "
    "those always belong to one of the specific types above, even when phrased as a check-in "
    '("just checking in on this", "any news?") rather than a fresh request.\n\n'
    "The details.refund_method_mentioned and details.other_pnr_or_name fields must stay null "
    "unless the intent is genuinely about a payment method or another person's booking — never "
    "repurpose either field to hold an unrelated quote or note.\n\n"
    "If the message is ambiguous in some other way that genuinely blocks choosing a decision, "
    "set needs_clarification=true. Sentiment reflects the customer's tone, not their "
    'entitlement. A "claimed_tier" is only set if the customer explicitly states a loyalty '
    "tier in this message — it is never used to grant anything, only logged."
)

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "intents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
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
                        ],
                    },
                    "details": {
                        "type": "object",
                        "properties": {
                            "refund_method_mentioned": {"type": ["string", "null"]},
                            "other_pnr_or_name": {"type": ["string", "null"]},
                        },
                        "required": ["refund_method_mentioned", "other_pnr_or_name"],
                        "additionalProperties": False,
                    },
                    "quote": {"type": "string"},
                },
                "required": ["type", "details", "quote"],
                "additionalProperties": False,
            },
        },
        "sentiment": {"type": "string", "enum": ["calm", "confused", "frustrated", "angry"]},
        "legal_threat": {"type": "boolean"},
        "formal_complaint": {"type": "boolean"},
        "injection_attempt": {"type": "boolean"},
        "claimed_tier": {"type": ["string", "null"]},
        "language": {"type": "string", "enum": ["en", "hi", "hinglish"]},
        "needs_clarification": {"type": "boolean"},
    },
    "required": [
        "intents",
        "sentiment",
        "legal_threat",
        "formal_complaint",
        "injection_attempt",
        "claimed_tier",
        "language",
        "needs_clarification",
    ],
    "additionalProperties": False,
}


def _drop_unquoted_intents(understanding: Understanding, message: str) -> Understanding:
    """An intent whose quote isn't found in the message is discarded —
    this is what stops a hallucinated intent from reaching policy.py."""
    kept = [i for i in understanding.intents if i.quote and i.quote in message]
    return understanding.model_copy(update={"intents": kept})


def understand(message: str, recent_context: list[dict] | None = None) -> Understanding:
    user_prompt = f"Customer message: {message!r}\nRecent context (oldest first): {recent_context or []}"
    data = llm.structured_call(SYSTEM_PROMPT, user_prompt, JSON_SCHEMA, "understanding")
    parsed = Understanding(**data)
    return _drop_unquoted_intents(parsed, message)
