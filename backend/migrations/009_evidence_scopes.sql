CREATE UNIQUE INDEX collections_owner_id ON collections(user_id,id);
CREATE UNIQUE INDEX evidence_owner_id ON evidence(user_id,id);
CREATE TABLE collection_members(
 collection_id TEXT NOT NULL,evidence_id TEXT NOT NULL,user_id TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('anchor','linked','context')),
 justification JSONB NOT NULL,PRIMARY KEY(collection_id,evidence_id),
 FOREIGN KEY(user_id,collection_id) REFERENCES collections(user_id,id),
 FOREIGN KEY(user_id,evidence_id) REFERENCES evidence(user_id,id)
);
CREATE INDEX collection_members_source ON collection_members(evidence_id);
CREATE FUNCTION frozen_collection_membership() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Snapshot membership is immutable'; END IF;
 IF EXISTS(SELECT 1 FROM collections WHERE id=NEW.collection_id AND summary::jsonb->>'frozen'='true')
 THEN RAISE EXCEPTION 'Snapshot is frozen'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER collection_members_frozen BEFORE INSERT OR UPDATE OR DELETE ON collection_members
 FOR EACH ROW EXECUTE FUNCTION frozen_collection_membership();
