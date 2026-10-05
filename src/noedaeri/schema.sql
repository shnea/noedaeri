CREATE TABLE IF NOT EXISTS users (
 id uuid PRIMARY KEY, issuer text NOT NULL, subject text NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','revoked')),
 role text NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(issuer, subject)
);
CREATE TABLE IF NOT EXISTS sessions (
 digest text PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id),
 csrf text NOT NULL, expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
 digest text PRIMARY KEY, verifier text NOT NULL, nonce text NOT NULL,
 expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 idempotency_key uuid NOT NULL,
 kind text NOT NULL, service text NOT NULL, title text NOT NULL,
 input jsonb NOT NULL DEFAULT '{}', options jsonb NOT NULL DEFAULT '{}',
 result jsonb, stage text NOT NULL DEFAULT 'waiting',
 status text NOT NULL CHECK(status IN ('uploading','queued','running','succeeded','failed','cancelled','interrupted')),
 input_bytes bigint NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz, expires_at timestamptz,
 lease_token uuid, lease_until timestamptz, worker_id uuid,
 cancel_requested boolean NOT NULL DEFAULT false,
 error_code text, cleanup_state text NOT NULL DEFAULT 'pending',
 result_state text NOT NULL DEFAULT 'none' CHECK(result_state IN ('none','available','expired','cleanup_failed')),
 UNIQUE(owner_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(created_at) WHERE status='queued';
CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner_id, created_at DESC);
CREATE TABLE IF NOT EXISTS workers (
 id uuid PRIMARY KEY, last_seen timestamptz NOT NULL DEFAULT now()
);

-- Additive, repeatable migration for existing installations.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS output_reserved bigint NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS execution_guarded boolean NOT NULL DEFAULT false;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_of uuid REFERENCES jobs(id);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'web';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS terminal_event_id uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS received_at timestamptz;
CREATE TABLE IF NOT EXISTS deliveries (
 id uuid PRIMARY KEY, job_id uuid NOT NULL UNIQUE REFERENCES jobs(id), body text NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','delivered','failed','acknowledged')),
 attempts integer NOT NULL DEFAULT 0, last_http_status integer,
 last_attempt_at timestamptz, next_attempt_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS deliveries_pending ON deliveries(next_attempt_at) WHERE state='pending';
