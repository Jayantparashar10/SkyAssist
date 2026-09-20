"""Step 5 — reply writer. LLM only: writes the reply from decisions code
has already made. It never sees policy text — only each Decision's
``customer_facing_facts`` and metadata below — so it cannot reinterpret a
rule, only phrase what's already been decided.
"""

from __future__ import annotations

from backend import llm
from backend.schemas import Decision, Language, Sentiment

SYSTEM_PROMPT = (
    "You write a short, polite reply to an airline customer from a list of decisions that "
    "have already been made — you do not decide anything yourself. Use only the facts in "
    "customer_facing_facts; never add an amount, a time, a calendar date, a flight number, or "
    "a promise that isn't there — not even a flight number the customer would expect, since "
    "none may exist beyond what the facts already state. If sentiment is frustrated or angry, "
    "acknowledge it in one sentence before the resolution. Ask a question only when a decision "
    'has status "ask" — never otherwise — and ask at most one, using its "question" field '
    "verbatim or lightly paraphrased, keeping the meaning exactly. When there is a choice or "
    'confirmation (a decision with status "offer" or "confirm"), name the options exactly as '
    "given in the facts — do not invent button labels. Reply in the customer's language "
    "(en/hi/hinglish). Keep the tone warm and direct, like a competent support agent — never "
    "mention rule IDs, assumption IDs, or any internal system name."
)

JSON_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}},
    "required": ["reply"],
    "additionalProperties": False,
}


def _decision_payload(decisions: list[Decision]) -> list[dict]:
    return [
        {
            "rule_id": d.rule_id,
            "status": d.status,
            "facts": d.customer_facing_facts,
            "question": d.params.get("question"),
        }
        for d in decisions
    ]


def respond(decisions: list[Decision], customer_name: str, sentiment: Sentiment, language: Language) -> str:
    user_prompt = (
        f"Customer name: {customer_name}\nSentiment: {sentiment}\nLanguage: {language}\n"
        f"Decisions: {_decision_payload(decisions)}"
    )
    data = llm.structured_call(SYSTEM_PROMPT, user_prompt, JSON_SCHEMA, "reply")
    return data["reply"]
