ALTER TABLE automatic_attempts ADD COLUMN checked_at DOUBLE PRECISION NOT NULL DEFAULT 0;
CREATE INDEX automatic_attempts_fair_queue ON automatic_attempts(checked_at,created_at,id)
 WHERE state IN ('queued','importing','investigating','waiting','deferred');
ALTER TABLE automatic_incidents ADD COLUMN event_time_known BOOLEAN NOT NULL DEFAULT TRUE;
CREATE TABLE automatic_attempt_imports(
 attempt_id TEXT NOT NULL REFERENCES automatic_attempts(id),
 import_id TEXT NOT NULL REFERENCES sentry_imports(id),
 PRIMARY KEY(attempt_id,import_id));
WITH RECURSIVE history(attempt_id,import_id,user_id,connection_id) AS (
 SELECT a.id,s.id,a.user_id,s.connection_id FROM automatic_attempts a
 JOIN sentry_imports s ON s.id=a.import_id AND s.user_id=a.user_id
 UNION
 SELECT h.attempt_id,previous.id,h.user_id,h.connection_id FROM history h
 JOIN sentry_imports current ON current.id=h.import_id
 JOIN sentry_imports previous ON previous.id=current.retry_of
  AND previous.user_id=h.user_id AND previous.connection_id=h.connection_id
)
INSERT INTO automatic_attempt_imports SELECT attempt_id,import_id FROM history;
CREATE TABLE automatic_notifications(
 dedupe_key TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES automatic_incidents(id),
 attempt_id TEXT NOT NULL REFERENCES automatic_attempts(id),kind TEXT NOT NULL,
 episode TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL,
 expires DOUBLE PRECISION NOT NULL,next_attempt DOUBLE PRECISION NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'pending',last_error TEXT);
CREATE INDEX automatic_notifications_pending ON automatic_notifications(state,next_attempt);
