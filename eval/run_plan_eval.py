import argparse
import json
import time
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


def compare_specs(expected: dict, actual: dict) -> dict:
    expected_filter_fields = {item["field"] for item in expected["filters"]}
    actual_filter_fields = {item["field"] for item in actual["filters"]}
    checks = {
        "metrics": set(expected["metrics"]) == set(actual["metrics"]),
        "dimensions": equivalent_fields(set(expected["dimensions"]), set(actual["dimensions"])),
        "filter_fields": equivalent_fields(expected_filter_fields, actual_filter_fields),
        "comparison": expected["comparison"] == actual["comparison"],
        "order_by": expected["order_by"] == actual["order_by"],
        "limit": expected["limit"] == actual["limit"],
    }
    return {"passed": all(checks.values()), "checks": checks}


def main():
    parser = argparse.ArgumentParser(description="少量调用模型评估检索模式 QuerySpec")
    parser.add_argument("--id", action="append", required=True, dest="case_ids")
    args = parser.parse_args()
    selected = set(args.case_ids)
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
    output = ROOT / "reports" / "query-spec-retrieval-selected-latest.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"passed={report['passed']}/{report['total']} api_calls={report['api_calls']}")
    print(output)


if __name__ == "__main__":
    main()
