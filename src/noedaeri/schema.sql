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
