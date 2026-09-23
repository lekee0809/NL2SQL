"""Indexed, source-scoped aliases for high-cardinality product/customer values."""

import re
import unicodedata
from dataclasses import dataclass

import psycopg
from psycopg import sql as pg_sql
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row

from .config import settings


ENTITY_TABLES = {"product": "products", "customer": "customers"}
MAX_MATCHES = 20


@dataclass(frozen=True)
class EntityMatch:
    entity_id: int
    value: str


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized).replace("_", "")


def lookup_alias(field: str, requested: str, source_id: str | None = None,
                 connection: psycopg.Connection | None = None) -> list[EntityMatch]:
    """Return every matching entity, never silently choose one alias target."""
    table = ENTITY_TABLES[field]
    key = normalize_alias(requested)
    if not key or not settings.database_url:
        return []
    statement = pg_sql.SQL(
        "SELECT a.entity_id, e.name AS value FROM entity_aliases a "
        "JOIN {table} e ON e.id = a.entity_id "
        "WHERE a.source_id = %s AND a.entity_type = %s AND a.alias_key = %s "
        "ORDER BY a.entity_id LIMIT %s"
    ).format(table=pg_sql.Identifier(table))
    params = (source_id or settings.default_source_id, field, key, MAX_MATCHES + 1)
    try:
        if connection is not None:
            rows = connection.execute(statement, params).fetchall()
        else:
            with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
                with conn.transaction():
                    conn.execute("SET TRANSACTION READ ONLY")
                    conn.execute(f"SET LOCAL statement_timeout = {settings.statement_timeout_ms}")
                    rows = conn.execute(statement, params).fetchall()
    except UndefinedTable:
        # Existing databases keep their old name/fuzzy lookup until migrated.
        return []
    if len(rows) > MAX_MATCHES:
        raise ValueError("该别名对应过多实体，请补充更具体的名称")
    return [EntityMatch(int(row["entity_id"]), row["value"]) for row in rows]


def lookup_name(field: str, name: str,
                connection: psycopg.Connection | None = None) -> list[EntityMatch]:
    """Resolve a canonical name to stable IDs, including duplicate-name cases."""
    table = ENTITY_TABLES[field]
    if not settings.database_url:
        return []
    statement = pg_sql.SQL(
        "SELECT id AS entity_id, name AS value FROM {table} WHERE name = %s "
        "ORDER BY id LIMIT %s"
    ).format(table=pg_sql.Identifier(table))
    params = (name, MAX_MATCHES + 1)
    if connection is not None:
        rows = connection.execute(statement, params).fetchall()
    else:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(f"SET LOCAL statement_timeout = {settings.statement_timeout_ms}")
                rows = conn.execute(statement, params).fetchall()
    if len(rows) > MAX_MATCHES:
        raise ValueError("同名实体过多，请补充更具体的条件")
    return [EntityMatch(int(row["entity_id"]), row["value"]) for row in rows]
