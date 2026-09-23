import argparse
import json
import time
from collections import Counter
from pathlib import Path

from app.llm import generate_query_spec
from app.query_spec import TIME_FIELDS


ROOT = Path(__file__).resolve().parent


def equivalent_fields(expected: set[str], actual: set[str]) -> bool:
    missing = expected - actual
    extra = actual - expected
    if missing <= TIME_FIELDS and extra <= TIME_FIELDS:
        return True
    return not missing and not extra


def canonical_filter(item: dict) -> tuple:
    """Normalize only genuinely equivalent time representations.

    Values and operators are intentionally part of the signature.  The old
    scorer compared field names only, which allowed invalid values such as
    ``2025-15`` to pass as long as the model picked a time field.
    """
    field = item["field"]
    operator = item["operator"]
    value = item.get("value", "")
    values = tuple(item.get("values", []))

    if field in TIME_FIELDS:
        if operator == "year" or (operator == "eq" and field == "order_year"):
            return ("time", "year", value, ())
        if operator == "calendar_month" or (operator == "eq" and field == "order_month"):
            return ("time", "calendar_month", value, ())
        if operator == "calendar_quarter" or (operator == "eq" and field == "order_quarter"):
            return ("time", "calendar_quarter", value.upper(), ())
        return ("time", operator, value, values)

    if operator == "in":
        values = tuple(sorted(values))
    return (field, operator, value, values)


def compare_specs(expected: dict, actual: dict) -> dict:
    expected_filters = Counter(canonical_filter(item) for item in expected["filters"])
    actual_filters = Counter(canonical_filter(item) for item in actual["filters"])
    checks = {
        "metrics": set(expected["metrics"]) == set(actual["metrics"]),
        "dimensions": equivalent_fields(set(expected["dimensions"]), set(actual["dimensions"])),
        "filters": expected_filters == actual_filters,
        "comparison": expected["comparison"] == actual["comparison"],
        "order_by": expected["order_by"] == actual["order_by"],
        "limit": expected["limit"] == actual["limit"],
    }
    return {"passed": all(checks.values()), "checks": checks}


def main():
    parser = argparse.ArgumentParser(description="少量调用模型评估检索模式 QuerySpec")
    parser.add_argument("--id", action="append", required=True, dest="case_ids")
    parser.add_argument(
        "--max-api-calls", type=int, default=5,
        help="本次允许的模型调用硬上限（默认 5，最大 10）",
    )
    parser.add_argument(
        "--output", default="query-spec-retrieval-selected-latest.json",
        help="写入 eval/reports 下的报告文件名",
    )
    args = parser.parse_args()
    selected = set(args.case_ids)
    if not 1 <= args.max_api_calls <= 10:
        parser.error("--max-api-calls 必须在 1 到 10 之间")
    if len(selected) > args.max_api_calls:
        parser.error(f"选择了 {len(selected)} 条，超过模型调用上限 {args.max_api_calls}")
    output_name = Path(args.output)
    if output_name.name != args.output or output_name.suffix != ".json":
        parser.error("--output 必须是单个 .json 文件名")
    cases = json.loads((ROOT / "advanced_cases.json").read_text(encoding="utf-8"))
    cases = [case for case in cases if case["id"] in selected]
    if len(cases) != len(selected):
        raise SystemExit("包含不存在的 case id")
    results = []
    for case in cases:
        started = time.perf_counter()
        try:
            actual = generate_query_spec(case["question"], use_retrieval=True).model_dump()
            comparison = compare_specs(case["expected_spec"], actual)
            result = {
                "id": case["id"], "question": case["question"],
                "passed": comparison["passed"], "checks": comparison["checks"],
                "expected_spec": case["expected_spec"], "actual_spec": actual,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1), "error": None,
            }
        except Exception as exc:
            result = {
                "id": case["id"], "question": case["question"], "passed": False,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(exc),
            }
        results.append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'} {case['id']} {result['latency_ms']}ms")
    report = {
        "mode": "retrieval",
        "api_calls": len(results),
        "total": len(results),
        "passed": sum(item["passed"] for item in results),
        "results": results,
    }
    output = ROOT / "reports" / output_name
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"passed={report['passed']}/{report['total']} api_calls={report['api_calls']}")
    print(output)


if __name__ == "__main__":
    main()
