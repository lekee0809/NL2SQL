import json
from collections import Counter
from pathlib import Path

from app.query_spec import DIMENSIONS, METRICS, QuerySpec


ROOT = Path(__file__).resolve().parent


def main():
    cases = json.loads((ROOT / "advanced_cases.json").read_text(encoding="utf-8"))
    ids = [case["id"] for case in cases]
    assert len(cases) == 75
    assert len(ids) == len(set(ids))
    assert Counter(case["split"] for case in cases) == {"dev": 60, "validation": 15}
    assert all(case["difficulty"] in {"medium", "hard"} for case in cases)
    for case in cases:
        if "expected_spec" in case:
            QuerySpec.model_validate(case["expected_spec"])
            retrieval = case["expected_retrieval"]
            assert set(retrieval["metrics"]) <= set(METRICS)
            assert set(retrieval["dimensions"]) <= set(DIMENSIONS)
        elif case["expected_action"] == "query":
            raise AssertionError(f"query case missing expected_spec: {case['id']}")
    print("valid=75")
    print("categories=" + json.dumps(Counter(case["category"] for case in cases), ensure_ascii=False))
    print("actions=" + json.dumps(Counter(case["expected_action"] for case in cases), ensure_ascii=False))


if __name__ == "__main__":
    main()
