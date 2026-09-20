-- Data model. Run once against a fresh Neon Postgres database, then
-- `python -m backend.seed` to load customers/bookings/fare quotes.

CREATE TABLE customers (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  last_name     TEXT NOT NULL,
  tier          TEXT NOT NULL CHECK (tier IN ('Silver','Gold','Platinum')),
  email         TEXT,
  phone         TEXT,
  history       TEXT
);

CREATE TABLE bookings (
  id            SERIAL PRIMARY KEY,
  pnr           TEXT NOT NULL,
  customer_id   INT REFERENCES customers(id),
  leg           INT NOT NULL,
  flight_no     TEXT,                 -- NULL for a leg whose flight number isn't given
  origin        TEXT NOT NULL,
  destination   TEXT NOT NULL,
  sched_dep     TIMESTAMPTZ NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('scheduled','cancelled','delayed')),
  new_dep       TIMESTAMPTZ,
  cause         TEXT
);

-- No flight inventory: a fare_quotes row records only the fare difference
-- a customer was told about and where that figure came from (A-01).
CREATE TABLE fare_quotes (
  id            SERIAL PRIMARY KEY,
  pnr           TEXT NOT NULL,
  description   TEXT NOT NULL,
  fare_diff_inr INT NOT NULL,
  source        TEXT NOT NULL
);

CREATE TABLE sessions (
  id            UUID PRIMARY KEY,
  pnr           TEXT NOT NULL,
  state         TEXT NOT NULL,
  context       JSONB NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE messages (
  id            BIGSERIAL PRIMARY KEY,
  session_id    UUID REFERENCES sessions(id),
  role          TEXT NOT NULL CHECK (role IN ('customer','agent','system','supervisor')),
  content       TEXT NOT NULL,
  meta          JSONB,
  created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE actions (
  id            BIGSERIAL PRIMARY KEY,
  session_id    UUID REFERENCES sessions(id),
  pnr           TEXT NOT NULL,
  type          TEXT NOT NULL,
  params        JSONB NOT NULL,
  rule_id       TEXT NOT NULL,
  status        TEXT NOT NULL,        -- issued | submitted | offered | booked | initiated
  created_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (pnr, type)
);

CREATE TABLE escalations (
  id              BIGSERIAL PRIMARY KEY,
  session_id      UUID REFERENCES sessions(id),
  pnr             TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('approval','handoff','immediate')),
  reason          TEXT NOT NULL,
  rule_id         TEXT NOT NULL,
  packet          JSONB NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied','completed')),
  supervisor_note TEXT,
  created_at      TIMESTAMPTZ DEFAULT now(),
  resolved_at     TIMESTAMPTZ
);

CREATE TABLE audit_log (
  id            BIGSERIAL PRIMARY KEY,
  session_id    UUID,
  event         TEXT NOT NULL,
  payload       JSONB NOT NULL,
  prev_hash     TEXT NOT NULL,
  hash          TEXT NOT NULL,
  ts            TIMESTAMPTZ DEFAULT now()
);
