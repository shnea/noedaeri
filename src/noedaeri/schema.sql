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

CREATE TABLE IF NOT EXISTS raya_policy (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 minimum_keep_seconds integer NOT NULL CHECK(minimum_keep_seconds BETWEEN 0 AND 86400),
 idle_seconds integer NOT NULL CHECK(idle_seconds BETWEEN 1 AND 86400)
);

CREATE TABLE IF NOT EXISTS ai_jobs (
 id uuid PRIMARY KEY,
 owner_id uuid NOT NULL REFERENCES users(id),
 project text NOT NULL DEFAULT 'default',
 environment text NOT NULL DEFAULT 'production',
 request_id text NOT NULL,
 task_type text NOT NULL,
 prompt text NOT NULL,
 input jsonb NOT NULL DEFAULT '{}',
 status text NOT NULL CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
 result jsonb,
 error_code text,
 error_message text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz,
 expires_at timestamptz NOT NULL DEFAULT now() + interval '24 hours',
 UNIQUE(owner_id, project, environment, request_id)
);
CREATE INDEX IF NOT EXISTS ai_jobs_owner_created ON ai_jobs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_jobs_request ON ai_jobs(project, environment, request_id);

CREATE TABLE IF NOT EXISTS ai_usage (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 job_id uuid REFERENCES ai_jobs(id) ON DELETE SET NULL,
 owner_id uuid NOT NULL REFERENCES users(id),
 project text NOT NULL DEFAULT 'default',
 environment text NOT NULL DEFAULT 'production',
 request_id text NOT NULL,
 task_type text NOT NULL,
 provider text NOT NULL,
 model text NOT NULL,
 prompt_tokens integer NOT NULL DEFAULT 0,
 completion_tokens integer NOT NULL DEFAULT 0,
 total_tokens integer NOT NULL DEFAULT 0,
 model_tier text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project, environment, request_id, provider, model)
);
CREATE INDEX IF NOT EXISTS ai_usage_project_env ON ai_usage(project, environment, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_usage_owner ON ai_usage(owner_id, created_at DESC);

