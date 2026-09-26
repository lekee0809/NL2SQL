"""Small, capped model smoke test against the isolated scale database."""

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from time import perf_counter

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo


ROOT = Path(__file__).resolve().parent.parent

QUESTIONS = [
    ("regional_top_products", "2025年华东地区销售额最高的三个商品",
     "SELECT p.name, sum(oi.quantity*oi.unit_price) AS amount FROM orders o "
     "JOIN customers c ON c.id=o.customer_id "
     "JOIN order_items oi ON oi.order_id=o.id JOIN products p ON p.id=oi.product_id "
     "WHERE o.status='PAID' AND c.region='华东' "
     "AND o.created_at >= DATE '2025-01-01' AND o.created_at < DATE '2026-01-01' "
     "GROUP BY p.name ORDER BY amount DESC, p.name LIMIT 3"),
    ("high_id_product", "商品09000的销量是多少",
     "SELECT sum(oi.quantity) FROM orders o JOIN order_items oi ON oi.order_id=o.id "
     "WHERE o.status='PAID' AND oi.product_id=9000"),
    ("customer_alias", "客户昵称-000001-01今年有多少订单",
     "SELECT count(*) FROM orders o WHERE o.status='PAID' AND o.customer_id=1 "
     "AND o.created_at >= DATE '2026-01-01' AND o.created_at < DATE '2027-01-01'"),
    ("monthly_trend", "去年每月销售额趋势",
     "SELECT date_trunc('month',o.created_at)::date, sum(oi.quantity*oi.unit_price) "
     "FROM orders o JOIN order_items oi ON oi.order_id=o.id "
     "WHERE o.status='PAID' AND o.created_at >= DATE '2025-01-01' "
     "AND o.created_at < DATE '2026-01-01' GROUP BY 1 ORDER BY 1"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="大库端到端冒烟测试，最多四次模型调用")
    parser.add_argument("--database", default="nl2sql_scale_20260927")
    parser.add_argument("--max-api-calls", type=int, default=4)
    parser.add_argument("--case", action="append", choices=[item[0] for item in QUESTIONS])
    parser.add_argument("--output", default="scale-live-20260927.json")
    args = parser.parse_args()
    if not re.fullmatch(r"nl2sql_scale_[a-z0-9_]{4,40}", args.database):
        parser.error("只允许 nl2sql_scale_ 前缀的隔离库")
    if not 1 <= args.max_api_calls <= 4:
        parser.error("模型调用上限必须在 1 到 4 之间")
    selected = [item for item in QUESTIONS if not args.case or item[0] in args.case]
    if len(selected) > args.max_api_calls:
        parser.error("选择的案例数超过模型调用上限")
    if Path(args.output).name != args.output or not args.output.endswith(".json"):
        parser.error("output 必须是单个 JSON 文件名")
    output = ROOT / "eval" / "reports" / args.output
    if output.exists():
        parser.error("报告已存在，拒绝覆盖")

    load_dotenv(ROOT / ".env")
    base_url = os.environ.get("DATABASE_URL")
    if not base_url:
        parser.error("DATABASE_URL 未配置")
    scale_url = make_conninfo(base_url, dbname=args.database)
    os.environ["DATABASE_URL"] = scale_url

    from app.conversation import apply_query_spec_patch, parse_local_patch
    from app.llm import generate_query_spec
    from app.main import execute_query_spec

    results = []
    usage = Counter()
    first_spec = None
    calls = 0
    with psycopg.connect(scale_url) as gold_conn:
        gold_conn.execute("SET TRANSACTION READ ONLY")
        for name, question, gold_sql in selected:
            expected = gold_conn.execute(gold_sql).fetchall()
            started = perf_counter()
            calls += 1
            spec = None
            try:
                spec = generate_query_spec(question, use_retrieval=True,
                                           usage_callback=usage.update)
                actual_result = execute_query_spec(spec, question)
                actual = [tuple(row.values()) for row in actual_result["rows"]]
                passed = actual == expected
                if name == "regional_top_products":
                    first_spec = spec
                results.append({"case": name, "passed": passed,
                                "row_count": len(actual),
                                "latency_ms": round((perf_counter()-started)*1000, 1),
                                "query_spec": spec.model_dump(),
                                "resolved_spec": actual_result["query_spec"],
                                "error_type": None if passed else "GoldMismatch"})
            except Exception as exc:
                results.append({"case": name, "passed": False,
                                "latency_ms": round((perf_counter()-started)*1000, 1),
                                "query_spec": spec.model_dump() if spec is not None else None,
                                "error_type": type(exc).__name__, "error": str(exc)[:300]})
            print(f"{'PASS' if results[-1]['passed'] else 'FAIL'} {name}", flush=True)

        if first_spec is not None:
            started = perf_counter()
            try:
                patch = parse_local_patch("改成华南，只看前五个", first_spec)
                merged, _ = apply_query_spec_patch(first_spec, patch)
                result = execute_query_spec(merged, "改成华南，只看前五个")
                expected = gold_conn.execute(
                    "SELECT p.name, sum(oi.quantity*oi.unit_price) AS amount FROM orders o "
                    "JOIN customers c ON c.id=o.customer_id "
                    "JOIN order_items oi ON oi.order_id=o.id JOIN products p ON p.id=oi.product_id "
                    "WHERE o.status='PAID' AND c.region='华南' "
                    "AND o.created_at >= DATE '2025-01-01' AND o.created_at < DATE '2026-01-01' "
                    "GROUP BY p.name ORDER BY amount DESC, p.name LIMIT 5"
                ).fetchall()
                actual = [tuple(row.values()) for row in result["rows"]]
                passed = actual == expected and merged.limit == 5
                results.append({"case": "local_follow_up", "passed": passed,
                                "row_count": len(actual), "model_calls": 0,
                                "latency_ms": round((perf_counter()-started)*1000, 1),
                                "query_spec": merged.model_dump(),
                                "resolved_spec": result["query_spec"],
                                "error_type": None if passed else "GoldMismatch"})
            except Exception as exc:
                results.append({"case": "local_follow_up", "passed": False,
                                "model_calls": 0, "error_type": type(exc).__name__})
            print(f"{'PASS' if results[-1]['passed'] else 'FAIL'} local_follow_up", flush=True)

    report = {"database": args.database, "model_call_limit": args.max_api_calls,
              "model_calls": calls, "token_usage": dict(usage),
              "passed": sum(item["passed"] for item in results), "total": len(results),
              "results": results}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: report[key] for key in
                      ("passed", "total", "model_calls", "token_usage")}, ensure_ascii=False))
    print(output)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
