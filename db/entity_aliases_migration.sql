-- Additive migration for an existing PostgreSQL analytics database.
-- No aliases are inserted; import them separately after checking entity IDs.
CREATE TABLE IF NOT EXISTS entity_aliases (
  id BIGSERIAL PRIMARY KEY,
  source_id TEXT NOT NULL,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('product', 'customer')),
  alias TEXT NOT NULL,
  alias_key TEXT NOT NULL CHECK (length(alias_key) > 0),
  entity_id INTEGER NOT NULL CHECK (entity_id > 0),
  UNIQUE (source_id, entity_type, alias_key, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_alias_lookup
  ON entity_aliases(source_id, entity_type, alias_key);
CREATE INDEX IF NOT EXISTS idx_entity_alias_mention
  ON entity_aliases(source_id, alias_key);
