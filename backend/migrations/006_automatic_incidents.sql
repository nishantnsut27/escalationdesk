CREATE TABLE sentry_automation(
 connection_id TEXT PRIMARY KEY REFERENCES sentry_connections(id),
 user_id TEXT NOT NULL REFERENCES users(id),enabled BOOLEAN NOT NULL DEFAULT FALSE,
 projects TEXT NOT NULL DEFAULT '[]',initial_email BOOLEAN NOT NULL DEFAULT TRUE,
 result_email BOOLEAN NOT NULL DEFAULT TRUE,updated_at TEXT NOT NULL);
CREATE TABLE automatic_issues(
 connection_id TEXT NOT NULL REFERENCES sentry_connections(id),issue_id TEXT NOT NULL,
 user_id TEXT NOT NULL REFERENCES users(id),project_id TEXT NOT NULL,lifecycle TEXT NOT NULL,
 transition_key TEXT,transition_at TEXT,episode INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(connection_id,issue_id));
CREATE TABLE automatic_incidents(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
 connection_id TEXT NOT NULL REFERENCES sentry_connections(id),issue_id TEXT NOT NULL,
 project_id TEXT NOT NULL,project_name TEXT NOT NULL,title TEXT NOT NULL,
 episode INTEGER NOT NULL,trigger TEXT NOT NULL,triggered_at TEXT NOT NULL,
 received_at DOUBLE PRECISION NOT NULL,resume_until DOUBLE PRECISION NOT NULL,
 collect_after DOUBLE PRECISION NOT NULL,lifecycle TEXT NOT NULL,state TEXT NOT NULL,
 observation TEXT NOT NULL,trace_id TEXT,error TEXT,completed_at TEXT,
 UNIQUE(connection_id,issue_id,episode));
CREATE INDEX automatic_incidents_owner ON automatic_incidents(user_id,triggered_at DESC,id);
CREATE TABLE automatic_attempts(
 id TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES automatic_incidents(id),
 user_id TEXT NOT NULL REFERENCES users(id),number INTEGER NOT NULL,explicit_retry BOOLEAN NOT NULL DEFAULT FALSE,
 state TEXT NOT NULL,created_at TEXT NOT NULL,resume_until DOUBLE PRECISION NOT NULL,
 import_id TEXT REFERENCES sentry_imports(id),investigation_id TEXT UNIQUE REFERENCES investigations(id),
 started_at TEXT,completed_at TEXT,allowance_day DATE,error TEXT,
 UNIQUE(incident_id,number));
CREATE UNIQUE INDEX automatic_attempts_active ON automatic_attempts(incident_id)
 WHERE state IN ('queued','importing','investigating','waiting','deferred');
CREATE INDEX automatic_attempts_pending ON automatic_attempts(state,created_at,id);
CREATE TABLE automatic_daily_allowance(
 user_id TEXT NOT NULL REFERENCES users(id),day DATE NOT NULL,attempts INTEGER NOT NULL CHECK(attempts BETWEEN 0 AND 5),
 PRIMARY KEY(user_id,day));
CREATE TABLE automatic_deliveries(
 id TEXT PRIMARY KEY,body_digest TEXT UNIQUE NOT NULL,request_id TEXT,
 connection_id TEXT NOT NULL REFERENCES sentry_connections(id),user_id TEXT NOT NULL REFERENCES users(id),
 issue_id TEXT NOT NULL,project_id TEXT NOT NULL,action TEXT NOT NULL,
 received_at DOUBLE PRECISION NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0,next_attempt DOUBLE PRECISION NOT NULL,
 incident_id TEXT REFERENCES automatic_incidents(id),error TEXT);
CREATE INDEX automatic_deliveries_pending ON automatic_deliveries(state,next_attempt);
ALTER TABLE email_jobs ADD COLUMN template_version TEXT;
ALTER TABLE email_jobs ADD COLUMN incident_id TEXT REFERENCES automatic_incidents(id);
ALTER TABLE email_jobs ADD COLUMN automatic_attempt_id TEXT REFERENCES automatic_attempts(id);
