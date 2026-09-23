import argparse
import json
from pathlib import Path

from eval.run_plan_eval import compare_specs


ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="不调用模型，重新评分已有 QuerySpec 报告")
    parser.add_argument(
        "--report", default="query-spec-retrieval-selected-latest.json",
        help="eval/reports 下的报告文件名",
    )
    args = parser.parse_args()
    report_name = Path(args.report)
    if report_name.name != args.report or report_name.suffix != ".json":
        parser.error("--report 必须是单个 .json 文件名")
    cases = {
        case["id"]: case
        for case in json.loads((ROOT / "advanced_cases.json").read_text(encoding="utf-8"))
    }
    path = ROOT / "reports" / report_name
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
