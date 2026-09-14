import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as pg_sql
from psycopg.rows import dict_row

from .catalog_models import (
    CatalogOverrides,
    ColumnMeta,
    ForeignKeyMeta,
    IndexMeta,
    RagDocument,
    SourceInfo,
    TableMeta,
    UnifiedCatalog,
)


TEXT_TYPES = {"character varying", "character", "text", "citext", "name"}


def canonical_type(data_type: str, udt_name: str = "") -> str:
    value = data_type.lower()
    udt = udt_name.lower()
    if value in TEXT_TYPES or value in {"uuid", "xml"}:
        return "string"
    if value in {"smallint", "integer", "bigint"}:
        return "integer"
    if value in {"numeric", "decimal", "real", "double precision", "money"}:
        return "decimal"
    if value == "boolean":
        return "boolean"
    if value == "date":
        return "date"
    if "timestamp" in value or "time" in value:
        return "datetime"
    if value in {"json", "jsonb"}:
        return "json"
    if value in {"bytea", "bit", "bit varying"}:
        return "binary"
    if value == "array" or udt.startswith("_"):
        return "other"
    return "other"


def apply_catalog_overrides(catalog: UnifiedCatalog, overrides: CatalogOverrides) -> UnifiedCatalog:
    result = catalog.model_copy(deep=True)
    tables = {f"{table.schema_name}.{table.name}": table for table in result.tables}
    columns = {
        f"{table.schema_name}.{table.name}.{column.name}": column
        for table in result.tables for column in table.columns
    }
    unknown_tables = sorted(set(overrides.tables) - set(tables))
    unknown_columns = sorted(set(overrides.columns) - set(columns))
    if unknown_tables or unknown_columns:
        details = []
        if unknown_tables:
            details.append(f"未知表：{unknown_tables}")
        if unknown_columns:
            details.append(f"未知字段：{unknown_columns}")
        raise ValueError("；".join(details))
    for key, override in overrides.tables.items():
        table = tables[key]
        if override.description is not None:
            table.description = override.description
        table.business_names = list(dict.fromkeys(override.business_names))
    for key, override in overrides.columns.items():
        column = columns[key]
        if override.description is not None:
            column.description = override.description
        column.business_names = list(dict.fromkeys(override.business_names))
        column.sensitivity = override.sensitivity
        if override.sensitivity != "normal":
            column.sample_values = []
    return result


TABLES_SQL = """
SELECT n.nspname AS schema_name,
       c.relname AS table_name,
       obj_description(c.oid, 'pg_class') AS description,
       GREATEST(c.reltuples::bigint, 0) AS estimated_rows
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'
ORDER BY n.nspname, c.relname
"""

COLUMNS_SQL = """
SELECT c.table_schema AS schema_name, c.table_name, c.column_name,
       c.ordinal_position, c.data_type, c.udt_name,
       c.is_nullable = 'YES' AS nullable, c.column_default,
       pg_catalog.col_description(pc.oid, pa.attnum) AS description
FROM information_schema.columns c
JOIN pg_catalog.pg_namespace pn ON pn.nspname = c.table_schema
JOIN pg_catalog.pg_class pc ON pc.relnamespace = pn.oid AND pc.relname = c.table_name
JOIN pg_catalog.pg_attribute pa ON pa.attrelid = pc.oid AND pa.attname = c.column_name
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

PRIMARY_KEYS_SQL = """
SELECT ns.nspname AS schema_name, cls.relname AS table_name,
       array_agg(att.attname ORDER BY keys.ordinality) AS columns
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class cls ON cls.oid = con.conrelid
JOIN pg_catalog.pg_namespace ns ON ns.oid = cls.relnamespace
JOIN unnest(con.conkey) WITH ORDINALITY AS keys(attnum, ordinality) ON true
JOIN pg_catalog.pg_attribute att ON att.attrelid = cls.oid AND att.attnum = keys.attnum
WHERE con.contype = 'p'
GROUP BY ns.nspname, cls.relname
"""

FOREIGN_KEYS_SQL = """
SELECT ns.nspname AS schema_name, cls.relname AS table_name, con.conname,
       fns.nspname AS referenced_schema, fcls.relname AS referenced_table,
       array_agg(att.attname ORDER BY src.ordinality) AS columns,
       array_agg(fatt.attname ORDER BY src.ordinality) AS referenced_columns
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class cls ON cls.oid = con.conrelid
JOIN pg_catalog.pg_namespace ns ON ns.oid = cls.relnamespace
JOIN pg_catalog.pg_class fcls ON fcls.oid = con.confrelid
JOIN pg_catalog.pg_namespace fns ON fns.oid = fcls.relnamespace
JOIN unnest(con.conkey) WITH ORDINALITY AS src(attnum, ordinality) ON true
JOIN unnest(con.confkey) WITH ORDINALITY AS dst(attnum, ordinality) ON dst.ordinality = src.ordinality
JOIN pg_catalog.pg_attribute att ON att.attrelid = cls.oid AND att.attnum = src.attnum
JOIN pg_catalog.pg_attribute fatt ON fatt.attrelid = fcls.oid AND fatt.attnum = dst.attnum
WHERE con.contype = 'f'
GROUP BY ns.nspname, cls.relname, con.conname, fns.nspname, fcls.relname
ORDER BY ns.nspname, cls.relname, con.conname
"""

INDEXES_SQL = """
SELECT schemaname AS schema_name, tablename AS table_name,
       indexname, indexdef
FROM pg_catalog.pg_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY schemaname, tablename, indexname
"""


def _sample_low_cardinality_values(conn, table: TableMeta, column: ColumnMeta, max_values: int) -> list[str]:
    if column.canonical_type not in {"string", "boolean"}:
        return []
    statement = pg_sql.SQL(
        "SELECT DISTINCT {column} AS sampled_value FROM {schema}.{table} "
        "WHERE {column} IS NOT NULL ORDER BY {column} LIMIT %s"
    ).format(
        column=pg_sql.Identifier(column.name),
        schema=pg_sql.Identifier(table.schema_name),
        table=pg_sql.Identifier(table.name),
    )
    with conn.cursor() as cur:
        cur.execute(statement, (max_values + 1,))
        values = [str(row["sampled_value"]) for row in cur.fetchall()]
    return values if len(values) <= max_values else []


def introspect_postgres(
    database_url: str,
    source_id: str,
    source_name: str | None = None,
    include_value_samples: bool = False,
    max_sample_values: int = 20,
) -> UnifiedCatalog:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            database_name = conn.execute("SELECT current_database()").fetchone()["current_database"]
            table_rows = conn.execute(TABLES_SQL).fetchall()
            column_rows = conn.execute(COLUMNS_SQL).fetchall()
            pk_rows = conn.execute(PRIMARY_KEYS_SQL).fetchall()
            fk_rows = conn.execute(FOREIGN_KEYS_SQL).fetchall()
            index_rows = conn.execute(INDEXES_SQL).fetchall()

            columns_by_table: dict[tuple[str, str], list[ColumnMeta]] = {}
            for row in column_rows:
                key = (row["schema_name"], row["table_name"])
                columns_by_table.setdefault(key, []).append(ColumnMeta(
                    id=f"{source_id}.{row['schema_name']}.{row['table_name']}.{row['column_name']}",
                    name=row["column_name"],
                    ordinal_position=row["ordinal_position"],
                    native_type=row["data_type"],
                    canonical_type=canonical_type(row["data_type"], row["udt_name"]),
                    nullable=row["nullable"],
                    default=row["column_default"],
                    description=row["description"],
                ))

            pk_by_table = {(row["schema_name"], row["table_name"]): list(row["columns"]) for row in pk_rows}
            fk_by_table: dict[tuple[str, str], list[ForeignKeyMeta]] = {}
            for row in fk_rows:
                key = (row["schema_name"], row["table_name"])
                fk_by_table.setdefault(key, []).append(ForeignKeyMeta(
                    id=f"{source_id}.{row['schema_name']}.{row['table_name']}.fk.{row['conname']}",
                    name=row["conname"],
                    columns=list(row["columns"]),
                    referenced_schema=row["referenced_schema"],
                    referenced_table=row["referenced_table"],
                    referenced_columns=list(row["referenced_columns"]),
                ))
            indexes_by_table: dict[tuple[str, str], list[IndexMeta]] = {}
            for row in index_rows:
                key = (row["schema_name"], row["table_name"])
                indexes_by_table.setdefault(key, []).append(IndexMeta(
                    name=row["indexname"], definition=row["indexdef"]
                ))

            tables: list[TableMeta] = []
            for row in table_rows:
                key = (row["schema_name"], row["table_name"])
                table = TableMeta(
                    id=f"{source_id}.{row['schema_name']}.{row['table_name']}",
                    schema_name=row["schema_name"],
                    name=row["table_name"],
                    description=row["description"],
                    estimated_rows=row["estimated_rows"],
                    columns=columns_by_table.get(key, []),
                    primary_key=pk_by_table.get(key, []),
                    foreign_keys=fk_by_table.get(key, []),
                    indexes=indexes_by_table.get(key, []),
                )
                if include_value_samples:
                    for column in table.columns:
                        column.sample_values = _sample_low_cardinality_values(
                            conn, table, column, max_sample_values
                        )
                tables.append(table)

    return UnifiedCatalog(
        generated_at=datetime.now(timezone.utc),
        source=SourceInfo(
            id=source_id,
            name=source_name or database_name,
            dialect="postgresql",
            database=database_name,
        ),
        tables=tables,
    )


def build_rag_documents(catalog: UnifiedCatalog, business_dictionary: dict[str, Any] | None = None) -> list[RagDocument]:
    source_id = catalog.source.id
    documents: list[RagDocument] = []
    for table in catalog.tables:
        column_summary = ", ".join(
            f"{column.name}({column.canonical_type})" for column in table.columns
        )
        documents.append(RagDocument(
            id=f"{table.id}:table",
            document_type="table",
            source_id=source_id,
            title=f"表 {table.schema_name}.{table.name}",
            text=(
                f"表 {table.schema_name}.{table.name}。业务名称：{', '.join(table.business_names) or '未配置'}。"
                f"{table.description or '暂无业务说明'}。字段：{column_summary}。"
            ),
            metadata={
                "schema": table.schema_name, "table": table.name,
                "primary_key": table.primary_key, "business_names": table.business_names,
            },
        ))
        for column in table.columns:
            documents.append(RagDocument(
                id=f"{column.id}:column",
                document_type="column",
                source_id=source_id,
                title=f"字段 {table.schema_name}.{table.name}.{column.name}",
                text=(
                    f"字段 {column.name}，位于表 {table.schema_name}.{table.name}，"
                    f"类型 {column.canonical_type}（原始类型 {column.native_type}）。"
                    f"业务名称：{', '.join(column.business_names) or '未配置'}。"
                    f"{column.description or '暂无业务说明'}"
                ),
                metadata={
                    "schema": table.schema_name, "table": table.name, "column": column.name,
                    "canonical_type": column.canonical_type, "nullable": column.nullable,
                    "business_names": column.business_names, "sensitivity": column.sensitivity,
                },
            ))
            if column.sample_values:
                documents.append(RagDocument(
                    id=f"{column.id}:values",
                    document_type="value_dictionary",
                    source_id=source_id,
                    title=f"字段值 {table.schema_name}.{table.name}.{column.name}",
                    text=f"字段 {column.name} 的低基数候选值：{', '.join(column.sample_values)}。",
                    metadata={
                        "schema": table.schema_name, "table": table.name,
                        "column": column.name, "values": column.sample_values,
                    },
                ))
        for foreign_key in table.foreign_keys:
            documents.append(RagDocument(
                id=f"{foreign_key.id}:relationship",
                document_type="relationship",
                source_id=source_id,
                title=f"关系 {foreign_key.name}",
                text=(
                    f"{table.schema_name}.{table.name}.{','.join(foreign_key.columns)} 关联到 "
                    f"{foreign_key.referenced_schema}.{foreign_key.referenced_table}."
                    f"{','.join(foreign_key.referenced_columns)}。"
                ),
                metadata={
                    "from_schema": table.schema_name, "from_table": table.name,
                    "from_columns": foreign_key.columns,
                    "to_schema": foreign_key.referenced_schema,
                    "to_table": foreign_key.referenced_table,
                    "to_columns": foreign_key.referenced_columns,
                },
            ))

    if business_dictionary:
        for document_type in ("metrics", "dimensions"):
            singular = "metric" if document_type == "metrics" else "dimension"
            for semantic_id, item in business_dictionary.get(document_type, {}).items():
                value_aliases = item.get("value_aliases", {})
                aliases = list(dict.fromkeys([
                    item.get("label", semantic_id),
                    *item.get("synonyms", []),
                    *item.get("retrieval_terms", []),
                    *value_aliases.keys(),
                    *value_aliases.values(),
                ]))
                documents.append(RagDocument(
                    id=f"{source_id}:{singular}:{semantic_id}",
                    document_type=singular,
                    source_id=source_id,
                    title=f"{singular} {item.get('label', semantic_id)}",
                    text=(
                        f"{item.get('label', semantic_id)}。同义词：{', '.join(aliases)}。"
                        f"涉及表：{', '.join(item.get('tables', []))}。"
                    ),
                    metadata={"semantic_id": semantic_id, "aliases": aliases},
                ))
    return documents


def write_catalog_artifacts(
    catalog: UnifiedCatalog,
    documents: list[RagDocument],
    output_dir: Path,
) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / "catalog.json"
    documents_path = output_dir / "rag_documents.jsonl"
    schema_path = output_dir / "catalog.schema.json"
    overrides_schema_path = output_dir / "overrides.schema.json"
    catalog_path.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    documents_path.write_text(
        "\n".join(document.model_dump_json() for document in documents) + "\n",
        encoding="utf-8",
    )
    schema_path.write_text(
        json.dumps(UnifiedCatalog.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    overrides_schema_path.write_text(
        json.dumps(CatalogOverrides.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return catalog_path, documents_path, schema_path, overrides_schema_path
