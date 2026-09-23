CREATE TABLE IF NOT EXISTS customers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  region TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id),
  created_at DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PAID', 'CANCELLED', 'REFUNDED')),
  total_amount NUMERIC(12,2) NOT NULL CHECK (total_amount >= 0)
);

CREATE TABLE IF NOT EXISTS order_items (
  id SERIAL PRIMARY KEY,
  order_id INT NOT NULL REFERENCES orders(id),
  product_id INT NOT NULL REFERENCES products(id),
  quantity INT NOT NULL CHECK (quantity > 0),
  unit_price NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
  id SERIAL PRIMARY KEY,
  order_id INT NOT NULL REFERENCES orders(id),
  paid_at DATE,
  method TEXT
);

-- Multiple aliases may refer to one entity, and one alias may refer to
-- multiple entities. Lookup must clarify the latter instead of guessing.
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

CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_customers_region ON customers(region);
CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product_id ON order_items(product_id);
