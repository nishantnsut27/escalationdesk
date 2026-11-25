ALTER TABLE sentry_connections ADD COLUMN provider TEXT NOT NULL DEFAULT 'sentry' CHECK(provider IN ('sentry','datadog'));
ALTER TABLE sentry_connections ADD COLUMN configuration JSONB NOT NULL DEFAULT '{}';
ALTER TABLE sentry_connections ALTER COLUMN installation_id DROP NOT NULL;
ALTER TABLE sentry_connections ADD CONSTRAINT sentry_installation_required CHECK(provider <> 'sentry' OR installation_id IS NOT NULL);
CREATE INDEX connections_provider_owner ON sentry_connections(provider,user_id);
