import json
from collections import Counter, defaultdict
from pathlib import Path

from app.catalog_retrieval import CatalogRetriever, DEFAULT_DOCUMENTS_PATH
from app.query_spec import TIME_FIELDS


ROOT = Path(__file__).resolve().parent


def main():
    cases = json.loads((ROOT / "advanced_cases.json").read_text(encoding="utf-8"))
    retriever = CatalogRetriever.from_jsonl(DEFAULT_DOCUMENTS_PATH)
    results = []
    for case in cases:
        expected = case.get("expected_retrieval")
        if not expected:
            continue
        retrieved = retriever.search(
            case["question"], limit=8, document_types={"metric", "dimension"}
        )
        retrieved_metrics = [
            item.document.metadata.get("semantic_id") for item in retrieved
            if item.document.document_type == "metric"
        ]
        retrieved_dimensions = [
            item.document.metadata.get("semantic_id") for item in retrieved
            if item.document.document_type == "dimension"
        ]
        missing_metrics = sorted(set(expected["metrics"]) - set(retrieved_metrics))
        expected_dimensions = set(expected["dimensions"])
        retrieved_dimension_set = set(retrieved_dimensions)
        missing_dimensions = expected_dimensions - retrieved_dimension_set
        if missing_dimensions & TIME_FIELDS and retrieved_dimension_set & TIME_FIELDS:
            missing_dimensions -= TIME_FIELDS
        missing_dimensions = sorted(missing_dimensions)
        results.append({
            "id": case["id"], "split": case["split"], "category": case["category"],
            "question": case["question"], "passed": not missing_metrics and not missing_dimensions,
            "expected": expected, "retrieved_metrics": retrieved_metrics,
            "retrieved_dimensions": retrieved_dimensions,
            "missing_metrics": missing_metrics, "missing_dimensions": missing_dimensions,
        })
    passed = sum(item["passed"] for item in results)
    by_category = defaultdict(lambda: Counter(total=0, passed=0))
    for item in results:
        by_category[item["category"]]["total"] += 1
        by_category[item["category"]]["passed"] += int(item["passed"])
    report = {
        "top_k": 8,
        "total": len(results),
        "passed": passed,
        "recall_accuracy": round(passed / len(results), 4),
        "by_category": {key: dict(value) for key, value in by_category.items()},
        "failures": [item for item in results if not item["passed"]],
        "results": results,
    }
    output = ROOT / "reports" / "retrieval-advanced-latest.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"recall@8={passed}/{len(results)} ({report['recall_accuracy']:.1%})")
    print(f"failures={len(report['failures'])}")
    for item in report["failures"]:
        print(f"{item['id']} metrics={item['missing_metrics']} dimensions={item['missing_dimensions']} {item['question']}")
    print(output)


if __name__ == "__main__":
    main()
