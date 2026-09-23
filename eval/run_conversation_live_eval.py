import argparse
import json
import time
from collections import Counter
from pathlib import Path

from app.conversation import apply_query_spec_patch, parse_local_patch
from app.llm import generate_query_spec, generate_query_spec_patch
from app.main import execute_query_spec
from app.query_spec import QuerySpec


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="低调用量多轮联网冒烟测试")
    parser.add_argument("--max-api-calls", type=int, default=5)
    parser.add_argument("--output", default="conversation-live-smoke-latest.json")
    args = parser.parse_args()
    if not 1 <= args.max_api_calls <= 5:
        parser.error("本测试的模型调用上限必须在 1 到 5 之间")
    output = Path(args.output)
    if output.name != args.output or output.suffix != ".json":
        parser.error("--output 必须是单个 .json 文件名")

    api_calls = 0
    usage = Counter()
    results: list[dict] = []

    def record_usage(item: dict[str, int]) -> None:
        usage.update(item)

    def model_spec(question: str) -> QuerySpec:
        nonlocal api_calls
        if api_calls >= args.max_api_calls:
            raise RuntimeError("已达到模型调用硬上限")
        api_calls += 1
        return generate_query_spec(question, use_retrieval=True, usage_callback=record_usage)

    def follow_up(name: str, current: QuerySpec, message: str, expected: callable) -> QuerySpec:
        nonlocal api_calls
        started = time.perf_counter()
        patch = parse_local_patch(message, current)
        source = "local"
        if patch is None:
            if api_calls >= args.max_api_calls:
                raise RuntimeError("已达到模型调用硬上限")
            api_calls += 1
            source = "model"
            patch = generate_query_spec_patch(message, current, usage_callback=record_usage)
        merged, changes = apply_query_spec_patch(current, patch)
        executed = execute_query_spec(merged, message)
        resolved = QuerySpec.model_validate(executed["query_spec"])
        passed = bool(expected(resolved))
        results.append({
            "name": name, "message": message, "source": source, "passed": passed,
            "changes": changes, "query_spec": resolved.model_dump(),
            "row_count": executed["count"],
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        })
        return resolved

    def initial(name: str, question: str, expected: callable) -> QuerySpec:
        started = time.perf_counter()
        spec = model_spec(question)
        executed = execute_query_spec(spec, question)
        resolved = QuerySpec.model_validate(executed["query_spec"])
        results.append({
            "name": name, "message": question, "source": "model", "passed": bool(expected(resolved)),
            "query_spec": resolved.model_dump(), "row_count": executed["count"],
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        })
        return resolved

    try:
        first = initial(
            "initial_product_ranking",
            "2025年华东地区销售额最高的商品",
            lambda spec: (
                spec.metrics == ["sales_amount"]
                and "product" in spec.dimensions
                and any(
                    item.field == "region" and item.operator == "eq" and item.value in {"华东", "华东地区"}
                    for item in spec.filters
                )
            ),
        )
        first = follow_up(
            "local_region_and_limit", first, "改成华南，只看前五个",
            lambda spec: spec.limit == 5 and any(item.field == "region" and item.value == "华南" for item in spec.filters),
        )
        follow_up(
            "model_add_second_group", first, "商品和地区一起分组",
            lambda spec: set(spec.dimensions) == {"product", "region"},
        )

        second = initial(
            "initial_monthly_trend",
            "去年每月销售额趋势",
            lambda spec: spec.metrics == ["sales_amount"] and "order_month" in spec.dimensions,
        )
        second = follow_up(
            "local_add_region", second, "改成华北",
            lambda spec: any(item.field == "region" and item.value == "华北" for item in spec.filters),
        )
        follow_up(
            "model_add_category_group", second, "再加商品类别一起分组",
            lambda spec: {"order_month", "category"} <= set(spec.dimensions),
        )

        initial(
            "initial_entity_query",
            "2025年极光键盘专业版卖出了多少件",
            lambda spec: spec.metrics == ["sold_quantity"] and any(item.field == "product" for item in spec.filters),
        )
    except Exception as exc:  # noqa: BLE001 - report partial progress without retrying
        results.append({"name": "runner", "passed": False, "error": str(exc)})

    report = {
        "api_call_limit": args.max_api_calls,
        "api_calls": api_calls,
        "token_usage": dict(usage),
        "total_steps": len(results),
        "passed": sum(bool(item.get("passed")) for item in results),
        "results": results,
    }
    path = ROOT / "reports" / output
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("api_call_limit", "api_calls", "token_usage", "total_steps", "passed")}, ensure_ascii=False))
    print(path)
    return 0 if report["passed"] == report["total_steps"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
