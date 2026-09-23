"""Large, isolated alias lookup test using PostgreSQL temporary tables only."""

import argparse
import json
from statistics import median, quantiles
from time import perf_counter

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.entity_aliases import lookup_alias
from scripts.import_entity_aliases import import_aliases


def _index_nodes(plan: dict) -> list[str]:
    names = [plan.get("Index Name", "")]
    for child in plan.get("Plans", []):
        names.extend(_index_nodes(child))
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="临时表大规模别名测试；不修改正式数据、不调用模型")
    parser.add_argument("--aliases", type=int, default=100_000)
    parser.add_argument("--lookups", type=int, default=500)
    args = parser.parse_args()
    if not 10_000 <= args.aliases <= 1_000_000 or not 10 <= args.lookups <= 5000:
        parser.error("aliases 必须在 10000–1000000，lookups 必须在 10–5000")
    if not settings.database_url:
        parser.error("DATABASE_URL 未配置")

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout = 120000")
            conn.execute("CREATE TEMP TABLE products(id integer PRIMARY KEY, name text NOT NULL) ON COMMIT DROP")
            conn.execute("INSERT INTO products VALUES (1, '标准版'), (2, '专业版')")
            conn.execute(
                "CREATE TEMP TABLE entity_aliases("
                "id bigserial PRIMARY KEY, source_id text, entity_type text, "
                "alias_key text, entity_id integer, alias text, "
                "UNIQUE(source_id, entity_type, alias_key, entity_id)"
                ") ON COMMIT DROP"
            )
            conn.execute(
                "INSERT INTO entity_aliases(source_id, entity_type, alias_key, entity_id, alias) "
                "SELECT %s, 'product', 'alias' || i, 1 + i %% 2, 'Alias ' || i "
                "FROM generate_series(1, %s) AS i",
                (settings.default_source_id, args.aliases),
            )
            conn.execute(
                "INSERT INTO entity_aliases(source_id, entity_type, alias_key, entity_id, alias) VALUES "
                "(%s, 'product', 'sharedname', 1, 'shared name'), "
                "(%s, 'product', 'sharedname', 2, 'shared name')",
                (settings.default_source_id, settings.default_source_id),
            )
            conn.execute(
                "CREATE INDEX alias_benchmark_lookup "
                "ON entity_aliases(source_id, entity_type, alias_key)"
            )
            conn.execute("ANALYZE entity_aliases")
            for number in (1, args.aliases // 2, args.aliases):
                matches = lookup_alias("product", f"alias{number}", connection=conn)
                assert len(matches) == 1 and matches[0].entity_id == 1 + number % 2
            ambiguous = lookup_alias("product", "shared name", connection=conn)
            assert {match.entity_id for match in ambiguous} == {1, 2}
            record = (settings.default_source_id, "product", "officekeyboard", 1, "office keyboard")
            assert import_aliases([record], apply=False, connection=conn)["inserted"] == 0
            assert import_aliases([record], apply=True, connection=conn)["inserted"] == 1
            assert import_aliases([record], apply=True, connection=conn)["inserted"] == 0
            assert lookup_alias("product", "office keyboard", connection=conn)[0].entity_id == 1

            statement = (
                "SELECT a.entity_id, e.name AS value FROM entity_aliases a "
                "JOIN products e ON e.id = a.entity_id "
                "WHERE a.source_id = %s AND a.entity_type = %s AND a.alias_key = %s "
                "ORDER BY a.entity_id LIMIT 21"
            )
            plan = conn.execute(
                "EXPLAIN (FORMAT JSON) " + statement,
                (settings.default_source_id, "product", f"alias{args.aliases}"),
            ).fetchone()["QUERY PLAN"][0]["Plan"]
            durations = []
            for number in range(args.lookups):
                key = f"alias{1 + (number * 9973) % args.aliases}"
                start = perf_counter()
                assert len(lookup_alias("product", key, connection=conn)) == 1
                durations.append((perf_counter() - start) * 1000)
            report = {
                "alias_count": args.aliases + 3,
                "lookups": args.lookups,
                "median_ms": round(median(durations), 3),
                "p95_ms": round(quantiles(durations, n=20)[18], 3),
                "indexed_plan": bool([name for name in _index_nodes(plan) if name]),
                "ambiguous_alias_candidates": len(ambiguous),
                "storage": "temporary tables, dropped on transaction completion",
                "model_calls": 0,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
