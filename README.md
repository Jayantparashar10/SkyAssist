# SkyAssist

A customer-facing agent that handles airline disruptions — cancellations and delays — for three
fixed customers (Priya Nair, Arvind Kulkarni, Meher Kaur). It applies the airline's service rules
in code, uses an LLM only to understand messages and write replies, and hands anything outside its
authority to a human supervisor with a full handoff packet.

Full architecture and spec: [`project.md`](./project.md).

## Live demo

- Customer chat: https://skyassists.vercel.app
- Supervisor console: https://skyassists.vercel.app/supervisor

## How it's built

- **`frontend/`** — Next.js (App Router), TypeScript, Tailwind. Deployed as its own Vercel project.
- **`backend/`** — FastAPI (Python), deployed as its own Vercel project. Talks to an LLM (Groq,
  `openai/gpt-oss-120b`) for language understanding and reply writing only — every policy decision
  (compensation, refunds, escalations) is plain Python, unit-tested without the LLM.
- **Database** — Neon Postgres.

The frontend never talks to the backend directly from the browser; it proxies `/api/*` requests
server-side (see `frontend/next.config.ts`), so no CORS setup is needed for normal use.

## Running locally

```bash
pip install -r backend/requirements.txt
npm install --prefix frontend

cd backend
python -m seed                               # seed the database once
uvicorn api.index:app --reload --port 8000   # terminal 1, still inside backend/
cd .. && npm run dev --prefix frontend       # terminal 2, from the repo root
```

Then open http://localhost:3000. Copy `backend/.env.example` to `backend/.env.local` and fill in
your own values first (see below).

## Environment variables

| Variable | Where | What |
|---|---|---|
| `GROQ_API_KEY` | backend | Groq API key |
| `GROQ_MODEL` | backend | defaults to `openai/gpt-oss-120b` |
| `DATABASE_URL` | backend | Neon Postgres connection string |
| `SIM_NOW` | backend | fixed simulated clock, `2026-09-23T10:00:00+05:30` |
| `SUPERVISOR_PASSCODE` | backend | passcode for `/supervisor` |
| `ALLOWED_ORIGINS` | backend | comma-separated frontend URL(s) allowed to call the API |
| `BACKEND_URL` | frontend | the deployed backend's URL |

## Tests

```bash
cd backend
pytest
```

All policy, guardrail, and scenario tests run without hitting the network or database. Live-LLM
eval is a separate manual script, run from inside `backend/`: `python -m tests.eval_understand`.
