import json
from pathlib import Path

from eval.run_plan_eval import compare_specs


ROOT = Path(__file__).resolve().parent


def main():
    cases = {
        case["id"]: case
        for case in json.loads((ROOT / "advanced_cases.json").read_text(encoding="utf-8"))
    }
    path = ROOT / "reports" / "query-spec-retrieval-selected-latest.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    for result in report["results"]:
        if result.get("actual_spec") is None:
            continue
        expected = cases[result["id"]]["expected_spec"]
        comparison = compare_specs(expected, result["actual_spec"])
        result["expected_spec"] = expected
        result["checks"] = comparison["checks"]
        result["passed"] = comparison["passed"]
    report["passed"] = sum(item["passed"] for item in report["results"])
    report["rescored_without_api_calls"] = True
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"passed={report['passed']}/{report['total']} api_calls_added=0")


if __name__ == "__main__":
    main()
