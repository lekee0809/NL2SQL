"""Read-only local benchmark for catalog retrieval and entity candidate loading.

Duplicating the current documents measures algorithmic scaling, not production
accuracy or end-to-end database latency. No model API calls or database writes.
"""

import argparse
import json
from statistics import median
from time import perf_counter

from app.catalog_retrieval import CatalogRetriever, DEFAULT_DOCUMENTS_PATH
from app.value_resolver import _load_candidates, clear_candidate_cache


def elapsed_ms(function) -> float:
    start = perf_counter()
    function()
    return round((perf_counter() - start) * 1000, 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="只读检索与候选值加载基准；不调用模型")
    parser.add_argument("--multiplier", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--skip-database", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.multiplier <= 1000 or not 1 <= args.repeats <= 100:
        parser.error("multiplier 必须为 1–1000，repeats 必须为 1–100")

    source = CatalogRetriever.from_jsonl(DEFAULT_DOCUMENTS_PATH)
    retriever = CatalogRetriever(source.documents * args.multiplier)
    question = "2025年华东地区商品销售额"
    retriever.search(question, limit=8)
    durations = [elapsed_ms(lambda: retriever.search(question, limit=8))
                 for _ in range(args.repeats)]
    report = {
        "document_count": len(retriever.documents),
        "synthetic_multiplier": args.multiplier,
        "retrieval_median_ms": round(median(durations), 2),
        "retrieval_max_ms": max(durations),
        "repeats": args.repeats,
        "note": "复制目录文档仅测检索算法；不代表真实大库或检索准确率",
    }
    if not args.skip_database:
        report["candidate_fields"] = {}
        for field in ("product", "customer", "category", "region"):
            clear_candidate_cache()
            start = perf_counter()
            values = _load_candidates(field)
            report["candidate_fields"][field] = {
                "loaded_count": len(values),
                "cold_load_ms": round((perf_counter() - start) * 1000, 2),
                "may_be_truncated": len(values) >= 1000,
            }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
