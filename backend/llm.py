"""LLM client wrapper (Groq). The only module that talks to the LLM over
the network. Groq's ``chat.completions`` endpoint mirrors the OpenAI shape
closely enough that ``structured_call``'s contract — a system/user prompt
in, a schema-validated dict out — stayed unchanged through two earlier
provider swaps (Cerebras, then OpenRouter); only the client construction
and the request's extra fields differ. Temperature 0, and one retry with
backoff: on a rate limit or outage, one retry, then the caller
(understand.py / respond.py, ultimately api/index.py) falls back to
buttons-only / templates.py. The chat must never show a raw error.
"""

from __future__ import annotations

import json
import os
import time

from groq import Groq

_client: Groq | None = None


def get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=30)
    return _client


def structured_call(system_prompt: str, user_prompt: str, json_schema: dict, schema_name: str, retries: int = 1) -> dict:
    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    client = get_client()
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
                },
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as exc:  # network error, rate limit, malformed JSON — all retried the same way
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"LLM call failed after {retries + 1} attempt(s)") from last_error
