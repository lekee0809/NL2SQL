"""Validate and optionally import product/customer aliases from a CSV file.

CSV columns: source_id, entity_type, entity_id, alias. Dry-run is the default;
use --apply to write to the configured PostgreSQL database.
"""

import argparse
import csv
from pathlib import Path

import psycopg
from psycopg import sql as pg_sql

from app.config import settings
from app.entity_aliases import ENTITY_TABLES, normalize_alias


def read_aliases(path: Path) -> list[tuple[str, str, str, int, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"source_id", "entity_type", "entity_id", "alias"}
        if set(reader.fieldnames or []) != expected:
            raise ValueError(f"CSV 必须包含且只包含字段：{', '.join(sorted(expected))}")
        records = []
        seen = set()
        for line_number, row in enumerate(reader, start=2):
            source_id = (row["source_id"] or settings.default_source_id).strip()
            field = (row["entity_type"] or "").strip()
            alias = (row["alias"] or "").strip()
            key = normalize_alias(alias)
            if (field not in ENTITY_TABLES or source_id != settings.default_source_id
                    or not key or len(alias) > 200):
                raise ValueError(f"第 {line_number} 行的数据源、实体类型或别名无效")
            try:
                entity_id = int(row["entity_id"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"第 {line_number} 行的实体 ID 无效") from exc
            if entity_id < 1:
                raise ValueError(f"第 {line_number} 行的实体 ID 必须大于零")
            record = (source_id, field, key, entity_id, alias)
            if record[:4] not in seen:
                records.append(record)
                seen.add(record[:4])
        return records


def _import_with_connection(conn, records, apply):
    missing = []
    for field, table in ENTITY_TABLES.items():
        ids = sorted({item[3] for item in records if item[1] == field})
        if not ids:
            continue
        statement = pg_sql.SQL("SELECT id FROM {table} WHERE id = ANY(%s)").format(
            table=pg_sql.Identifier(table)
        )
        found = {row["id"] if isinstance(row, dict) else row[0]
                 for row in conn.execute(statement, (ids,)).fetchall()}
        missing.extend(f"{field}:{item}" for item in ids if item not in found)
    if missing:
        raise ValueError("别名指向不存在的实体 ID：" + ", ".join(missing[:10]))
    if not apply:
        return {"validated": len(records), "inserted": 0, "dry_run": True}
    inserted = 0
    for start in range(0, len(records), 1000):
        batch = records[start:start + 1000]
        columns = [list(column) for column in zip(*batch)]
        rows = conn.execute(
            "INSERT INTO entity_aliases(source_id, entity_type, alias_key, entity_id, alias) "
            "SELECT * FROM UNNEST(%s::text[], %s::text[], %s::text[], %s::integer[], %s::text[]) "
            "ON CONFLICT DO NOTHING RETURNING id",
            columns,
        ).fetchall()
        inserted += len(rows)
    return {"validated": len(records), "inserted": inserted, "dry_run": False}


def import_aliases(records: list[tuple[str, str, str, int, str]], apply: bool = False,
                   connection: psycopg.Connection | None = None) -> dict:
    if connection is not None:
        with connection.transaction():
            return _import_with_connection(connection, records, apply)
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL 未配置")
    with psycopg.connect(settings.database_url) as conn:
        with conn.transaction():
            return _import_with_connection(conn, records, apply)


def main() -> None:
    parser = argparse.ArgumentParser(description="导入商品/客户别名，默认只验证不写入")
    parser.add_argument("--file", type=Path, required=True, help="UTF-8 CSV 路径")
    parser.add_argument("--apply", action="store_true", help="通过验证后写入数据库")
    args = parser.parse_args()
    print(import_aliases(read_aliases(args.file), apply=args.apply))


if __name__ == "__main__":
    main()
