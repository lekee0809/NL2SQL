"""Repeat read-only NL2SQL checks against an isolated synthetic scale database."""

import argparse
import json
import os
import re
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parent.parent


def make_spec(metric, dimensions=(), filters=(), order_by=(), limit=None):
    from app.query_spec import QuerySpec

    return QuerySpec.model_validate({
        "metrics": [metric], "dimensions": list(dimensions),
        "filters": [dict(field=field, operator="eq", value=value, values=[])
                    for field, value in filters],
        "order_by": [dict(field=field, direction=direction) for field, direction in order_by],
        "limit": limit, "comparison": None,
    })


def cases():
    return [
        ("paid_order_count", make_spec("order_count"),
         "SELECT count(*) FROM orders WHERE status='PAID'", None),
        ("paid_sales_amount", make_spec("sales_amount"),
         "SELECT sum(oi.quantity*oi.unit_price) FROM orders o "
         "JOIN order_items oi ON oi.order_id=o.id WHERE o.status='PAID'", None),
        ("paid_order_amount", make_spec("order_amount"),
         "SELECT sum(total_amount) FROM orders WHERE status='PAID'", None),
        ("region_order_count", make_spec("order_count", filters=(("region", "华东"),)),
         "SELECT count(*) FROM orders o JOIN customers c ON c.id=o.customer_id "
         "WHERE o.status='PAID' AND c.region='华东'", None),
        ("monthly_sales_2025", make_spec(
            "sales_amount", dimensions=("order_month",), filters=(("order_year", "2025"),),
            order_by=(("order_month", "asc"),)),
         "SELECT date_trunc('month',o.created_at)::date, sum(oi.quantity*oi.unit_price) "
         "FROM orders o JOIN order_items oi ON oi.order_id=o.id "
         "WHERE o.status='PAID' AND o.created_at >= DATE '2025-01-01' "
         "AND o.created_at < DATE '2026-01-01' GROUP BY 1 ORDER BY 1", None),
        ("category_sales_2025", make_spec(
            "sales_amount", dimensions=("category",), filters=(("order_year", "2025"),),
            order_by=(("category", "asc"),)),
         "SELECT p.category, sum(oi.quantity*oi.unit_price) FROM orders o "
         "JOIN order_items oi ON oi.order_id=o.id JOIN products p ON p.id=oi.product_id "
         "WHERE o.status='PAID' AND o.created_at >= DATE '2025-01-01' "
         "AND o.created_at < DATE '2026-01-01' GROUP BY p.category ORDER BY p.category", None),
        ("product_alias", make_spec(
            "sold_quantity", filters=(("product", "商品昵称-00001-01"),)),
         "SELECT sum(oi.quantity) FROM orders o JOIN order_items oi ON oi.order_id=o.id "
         "WHERE o.status='PAID' AND oi.product_id=1", 1),
        ("customer_alias", make_spec(
            "order_count", filters=(("customer", "客户昵称-000001-01"),)),
         "SELECT count(*) FROM orders o WHERE o.status='PAID' AND o.customer_id=1", 1),
        ("high_id_exact_product", make_spec(
            "sold_quantity", filters=(("product", "商品09000"),)),
         "SELECT sum(oi.quantity) FROM orders o JOIN order_items oi ON oi.order_id=o.id "
         "WHERE o.status='PAID' AND oi.product_id=9000", 9000),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="隔离压测库上的重复只读评测，零模型调用")
    parser.add_argument("--database", default="nl2sql_scale_20260927")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--alias-lookups", type=int, default=200)
    parser.add_argument("--output", default="scale-20260927.json")
    args = parser.parse_args()
    if not re.fullmatch(r"nl2sql_scale_[a-z0-9_]{4,40}", args.database):
        parser.error("只允许 nl2sql_scale_ 前缀的隔离库")
    if not 1 <= args.repeat <= 10:
        parser.error("repeat 必须在 1 到 10 之间")
    if not 10 <= args.alias_lookups <= 1000:
        parser.error("alias-lookups 必须在 10 到 1000 之间")
    if Path(args.output).name != args.output or not args.output.endswith(".json"):
        parser.error("output 必须是单个 JSON 文件名")
    output = ROOT / "eval" / "reports" / args.output
    if output.exists():
        parser.error("报告已存在，拒绝覆盖；请指定新文件名")

    load_dotenv(ROOT / ".env")
    base_url = os.environ.get("DATABASE_URL")
    if not base_url:
        parser.error("DATABASE_URL 未配置")
    scale_url = make_conninfo(base_url, dbname=args.database)
    os.environ["DATABASE_URL"] = scale_url

    from app.catalog_builder import introspect_postgres
    from app.config import settings
    from app.conversation import apply_query_spec_patch, parse_local_patch
    from app.main import execute_query_spec
    from app.value_resolver import NeedsClarification
    from app.entity_aliases import lookup_alias

    checks = []
    durations = []
    with psycopg.connect(scale_url) as gold_conn:
        gold_conn.execute("SET TRANSACTION READ ONLY")
        expected_rows = {name: gold_conn.execute(statement).fetchall()
                         for name, _, statement, _ in cases()}
        for repeat in range(args.repeat):
            for name, spec, _, entity_id in cases():
                started = perf_counter()
                try:
                    result = execute_query_spec(spec, name)
                    actual = [tuple(row.values()) for row in result["rows"]]
                    pinned = (entity_id is None or any(
                        item.get("entity_id") == entity_id
                        for item in result["query_spec"]["filters"]
                    ))
                    passed = actual == expected_rows[name] and pinned
                    error = None if passed else "result or resolved entity ID differs from gold"
                except Exception as exc:
                    passed = False
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = round((perf_counter() - started) * 1000, 1)
                durations.append(elapsed)
                checks.append({"case": name, "repeat": repeat + 1, "passed": passed,
                               "latency_ms": elapsed, "error": error})
                print(f"{'PASS' if passed else 'FAIL'} {name} run={repeat + 1} {elapsed}ms", flush=True)

        base = make_spec("order_count", filters=(("order_year", "2025"), ("region", "华东")))
        patch = parse_local_patch("改成华南，只看前五个", base)
        merged, _ = apply_query_spec_patch(base, patch)
        expected = gold_conn.execute(
            "SELECT count(*) FROM orders o JOIN customers c ON c.id=o.customer_id "
            "WHERE o.status='PAID' AND c.region='华南' "
            "AND o.created_at >= DATE '2025-01-01' AND o.created_at < DATE '2026-01-01'"
        ).fetchone()[0]
        result = execute_query_spec(merged, "改成华南，只看前五个")
        passed = (next(iter(result["rows"][0].values())) == expected
                  and merged.limit == 5
                  and any(item.field == "order_year" for item in merged.filters))
        checks.append({"case": "multi_turn_local_patch", "passed": passed,
                       "model_calls": 0})

        try:
            execute_query_spec(make_spec("sold_quantity", filters=(("product", "共享商品"),)),
                               "共享商品销量")
            passed = False
        except NeedsClarification as exc:
            passed = {item["entity_id"] for item in exc.candidates} == {1, 2}
        checks.append({"case": "ambiguous_alias_clarification", "passed": passed,
                       "model_calls": 0})

        repaired = execute_query_spec(make_spec(
            "sales_amount", dimensions=("order_month",),
            filters=(("order_year", "last_year"),),
            order_by=(("order_month", "asc"),)), "去年每月销售额趋势")
        checks.append({
            "case": "relative_time_repair", "model_calls": 0,
            "passed": ([tuple(row.values()) for row in repaired["rows"]]
                       == expected_rows["monthly_sales_2025"]
                       and bool(repaired["normalizations"])),
        })

    started = perf_counter()
    catalog = introspect_postgres(scale_url, source_id=settings.default_source_id,
                                  include_value_samples=True, max_sample_values=20)
    scan_ms = round((perf_counter() - started) * 1000, 1)
    checks.append({"case": "large_catalog_scan", "passed": len(catalog.tables) == 6,
                   "latency_ms": scan_ms, "table_count": len(catalog.tables)})
    alias_durations = []
    alias_passed = True
    for number in range(args.alias_lookups):
        product_id = 1 + (number * 7919) % 10_000
        started = perf_counter()
        matches = lookup_alias("product", f"商品昵称-{product_id:05d}-01")
        alias_durations.append(round((perf_counter() - started) * 1000, 2))
        if len(matches) != 1 or matches[0].entity_id != product_id:
            alias_passed = False
    checks.append({"case": "indexed_alias_lookup_batch", "passed": alias_passed,
                   "lookups": args.alias_lookups})
    shared_durations = []
    with psycopg.connect(scale_url, row_factory=dict_row) as alias_conn:
        alias_conn.execute("SET TRANSACTION READ ONLY")
        for number in range(args.alias_lookups):
            product_id = 1 + (number * 7919) % 10_000
            started = perf_counter()
            matches = lookup_alias("product", f"商品昵称-{product_id:05d}-01",
                                   connection=alias_conn)
            shared_durations.append(round((perf_counter() - started) * 1000, 3))
            if len(matches) != 1 or matches[0].entity_id != product_id:
                alias_passed = False
    checks.append({"case": "indexed_alias_lookup_shared_connection", "passed": alias_passed,
                   "lookups": args.alias_lookups})
    report = {
        "database": args.database, "repeat": args.repeat, "model_calls": 0,
        "checks": checks, "passed": sum(item["passed"] for item in checks),
        "total": len(checks), "query_median_ms": round(median(durations), 1),
        "query_p95_ms": round(quantiles(durations, n=20)[18], 1) if len(durations) >= 2 else None,
        "catalog_scan_ms": scan_ms,
        "alias_lookup_median_ms": round(median(alias_durations), 2),
        "alias_lookup_p95_ms": round(quantiles(alias_durations, n=20)[18], 2),
        "alias_lookup_shared_p95_ms": round(quantiles(shared_durations, n=20)[18], 3),
    }
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: report[key] for key in
                      ("passed", "total", "model_calls", "query_median_ms", "query_p95_ms",
                       "catalog_scan_ms", "alias_lookup_p95_ms",
                       "alias_lookup_shared_p95_ms")},
                     ensure_ascii=False), flush=True)
    print(output)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
