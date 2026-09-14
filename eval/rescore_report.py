import json
import os
import argparse
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

from run_eval import semantic_match

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def execute(conn, sql):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return cur.fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "validation"], default="dev")
    args = parser.parse_args()
    cases = {case["id"]: case for case in json.loads((ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))}
    path = ROOT / "eval" / "reports" / f"{args.split}-latest.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    changed_questions = {"basic_008", "aggregate_009"} if args.split == "dev" else set()
    selected_path = ROOT / "eval" / "reports" / f"{args.split}-selected-latest.json"
    reruns = {}
    if selected_path.exists():
        selected = json.loads(selected_path.read_text(encoding="utf-8"))
        reruns = {result["id"]: result for result in selected["results"]}
    report["results"] = [reruns.get(result["id"], result) for result in report["results"]]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        for result in report["results"]:
            case = cases[result["id"]]
            if result["id"] in changed_questions and result["id"] not in reruns:
                result["needs_rerun"] = True
                result["passed"] = False
                continue
            result.pop("needs_rerun", None)
            if result.get("status") != 200 or not result.get("sql"):
                continue
            predicted = execute(conn, result["sql"])
            expected = execute(conn, case["gold_sql"])
            passed, strict = semantic_match(predicted, expected, case["ordered"])
            result.update(passed=passed, strict_passed=strict, error=None if passed else "结果与 gold SQL 不一致")
    valid = [result for result in report["results"] if not result.get("needs_rerun")]
    report["passed"] = sum(r["passed"] for r in valid)
    report["strict_passed"] = sum(r.get("strict_passed", r["passed"]) for r in valid)
    pending = sorted(changed_questions - set(reruns))
    report["pending_rerun"] = pending
    report["execution_accuracy"] = round(report["passed"] / len(valid), 4)
    report["strict_execution_accuracy"] = round(report["strict_passed"] / len(valid), 4)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rescored={len(valid)} passed={report['passed']} pending={len(pending)} accuracy={report['execution_accuracy']:.1%}")


if __name__ == "__main__":
    main()
