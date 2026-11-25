ALTER TABLE investigations ADD COLUMN execution_manifest JSONB;
ALTER TABLE investigations ADD COLUMN execution_state JSONB;
CREATE UNIQUE INDEX investigations_owner_id ON investigations(user_id,id);
CREATE TABLE investigation_events(
 investigation_id TEXT NOT NULL,user_id TEXT NOT NULL,sequence BIGINT NOT NULL,
 kind TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),payload JSONB NOT NULL,
 digest TEXT NOT NULL,PRIMARY KEY(investigation_id,sequence),
 FOREIGN KEY(user_id,investigation_id) REFERENCES investigations(user_id,id)
);
CREATE FUNCTION immutable_investigation_event() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Investigation events are append-only'; END $$;
CREATE TRIGGER investigation_event_immutable BEFORE UPDATE OR DELETE ON investigation_events
 FOR EACH ROW EXECUTE FUNCTION immutable_investigation_event();
