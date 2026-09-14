import json
from pathlib import Path

from app.llm import build_prompt_catalog
from app.query_spec import TIME_FIELDS


ROOT = Path(__file__).resolve().parent


def compact_size(value: dict) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def main():
    cases = json.loads((ROOT / "advanced_cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        if "expected_spec" not in case:
            continue
        full = build_prompt_catalog(case["question"], use_retrieval=False)
        subset = build_prompt_catalog(case["question"], use_retrieval=True)
        full_chars, subset_chars = compact_size(full), compact_size(subset)
        expected = case["expected_retrieval"]
        missing_dimensions = set(expected["dimensions"]) - set(subset["dimensions"])
        if missing_dimensions & TIME_FIELDS and set(subset["dimensions"]) & TIME_FIELDS:
            missing_dimensions -= TIME_FIELDS
        covered = set(expected["metrics"]) <= set(subset["metrics"]) and not missing_dimensions
        rows.append({
            "id": case["id"], "question": case["question"], "covered": covered,
            "full_chars": full_chars, "retrieved_chars": subset_chars,
            "reduction": round(1 - subset_chars / full_chars, 4),
            "metric_candidates": len(subset["metrics"]),
            "dimension_candidates": len(subset["dimensions"]),
        })
    report = {
        "total": len(rows),
        "covered": sum(row["covered"] for row in rows),
        "average_full_chars": round(sum(row["full_chars"] for row in rows) / len(rows), 1),
        "average_retrieved_chars": round(sum(row["retrieved_chars"] for row in rows) / len(rows), 1),
        "average_reduction": round(sum(row["reduction"] for row in rows) / len(rows), 4),
        "rows": rows,
    }
    output = ROOT / "reports" / "prompt-reduction-latest.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"coverage={report['covered']}/{report['total']}")
    print(f"average_chars={report['average_full_chars']} -> {report['average_retrieved_chars']}")
    print(f"average_reduction={report['average_reduction']:.1%}")
    print(output)


if __name__ == "__main__":
    main()
