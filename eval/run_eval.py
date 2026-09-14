import argparse
import itertools
import json
import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def normalize_scalar(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.hour == value.minute == value.second == value.microsecond == 0:
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        number = Decimal(str(value)).quantize(Decimal("0.000001"))
        return str(number.normalize())
    text = str(value)
    if len(text) == 7 and text[4] == "-" and text[:4].isdigit() and text[5:].isdigit():
        return f"{text}-01"
    return text


def normalize_rows(rows, ordered):
    normalized = [tuple(normalize_scalar(v) for v in row.values()) for row in rows]
    return normalized if ordered else sorted(normalized, key=repr)


def semantic_match(predicted_rows, gold_rows, ordered):
    strict = normalize_rows(predicted_rows, ordered) == normalize_rows(gold_rows, ordered)
    if strict:
        return True, True
    if not gold_rows:
        return len(predicted_rows) == 0, False
    if not predicted_rows or len(predicted_rows) != len(gold_rows):
        return False, False
    predicted_columns = list(predicted_rows[0].keys())
    gold_width = len(gold_rows[0])
    if len(predicted_columns) < gold_width:
        return False, False
    expected = normalize_rows(gold_rows, ordered)
    for columns in itertools.permutations(predicted_columns, gold_width):
        projection = [{column: row.get(column) for column in columns} for row in predicted_rows]
        if normalize_rows(projection, ordered) == expected:
            return True, False
    return False, False


def execute_gold(database_url, sql):
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()


def run_case(client, base_url, database_url, case):
    started = time.perf_counter()
    try:
        response = client.post(f"{base_url}/query", json={"question": case["question"]})
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if case["expected"] in {"blocked", "rejected"}:
            passed = 400 <= response.status_code < 500
            return {"id": case["id"], "passed": passed, "status": response.status_code, "latency_ms": latency_ms, "error": None if passed else "请求未按预期拒绝"}
        if case["expected"] == "clarification":
            detail = response.json().get("detail", {}) if response.content else {}
            candidates = [item.get("value") for item in detail.get("candidates", [])]
            expected_candidates = case.get("expected_candidates", [])
            passed = (
                response.status_code == 409
                and detail.get("type") == "needs_clarification"
                and set(expected_candidates) <= set(candidates)
            )
            return {
                "id": case["id"], "passed": passed, "status": response.status_code,
                "latency_ms": latency_ms, "candidates": candidates,
                "error": None if passed else "未返回预期的澄清候选",
            }
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            return {"id": case["id"], "passed": False, "status": response.status_code, "latency_ms": latency_ms, "error": str(detail)}
        payload = response.json()
        gold_rows = execute_gold(database_url, case["gold_sql"])
        semantic_passed, strict_passed = semantic_match(payload["rows"], gold_rows, case["ordered"])
        return {
            "id": case["id"], "passed": semantic_passed, "strict_passed": strict_passed, "status": 200,
            "latency_ms": latency_ms, "sql": payload.get("sql"),
            "parameters": payload.get("parameters"), "query_spec": payload.get("query_spec"),
            "resolutions": payload.get("resolutions"),
            "predicted_rows": len(payload["rows"]), "expected_rows": len(gold_rows),
            "error": None if semantic_passed else "结果与 gold SQL 不一致",
        }
    except Exception as exc:
        return {"id": case["id"], "passed": False, "status": 0, "latency_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description="智能问数执行准确率评测")
    parser.add_argument("--split", choices=["dev", "validation", "test", "all"], default="dev")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--id", action="append", dest="case_ids", help="只评测指定题目，可重复传入")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--case-file", default="cases.json", help="eval 目录中的测试集文件名")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL 未配置")
    case_path = (ROOT / "eval" / args.case_file).resolve()
    eval_root = (ROOT / "eval").resolve()
    if eval_root not in case_path.parents:
        raise SystemExit("测试集文件必须位于 eval 目录")
    cases = json.loads(case_path.read_text(encoding="utf-8"))
    if args.split != "all":
        cases = [case for case in cases if case["split"] == args.split]
    if args.case_ids:
        selected = set(args.case_ids)
        cases = [case for case in cases if case["id"] in selected]
    if args.limit:
        cases = cases[:args.limit]

    results = []
    with httpx.Client(timeout=90) as client:
        for index, case in enumerate(cases, 1):
            result = run_case(client, args.base_url, database_url, case)
            result.update({"question": case["question"], "split": case["split"], "category": case["category"], "difficulty": case["difficulty"]})
            results.append(result)
            print(f"[{index:02}/{len(cases):02}] {'PASS' if result['passed'] else 'FAIL'} {case['id']} {result['latency_ms']}ms")

    passed = sum(result["passed"] for result in results)
    strict_passed = sum(result.get("strict_passed", result["passed"]) for result in results)
    by_category = defaultdict(lambda: Counter(total=0, passed=0))
    for result in results:
        by_category[result["category"]]["total"] += 1
        by_category[result["category"]]["passed"] += int(result["passed"])
    report = {
        "split": args.split,
        "total": len(results),
        "passed": passed,
        "strict_passed": strict_passed,
        "execution_accuracy": round(passed / len(results), 4) if results else 0,
        "strict_execution_accuracy": round(strict_passed / len(results), 4) if results else 0,
        "average_latency_ms": round(sum(r["latency_ms"] for r in results) / len(results), 1) if results else 0,
        "by_category": dict(by_category),
        "results": results,
    }
    reports = ROOT / "eval" / "reports"
    reports.mkdir(exist_ok=True)
    case_prefix = "" if args.case_file == "cases.json" else f"{case_path.stem}-"
    report_name = f"{case_prefix}{args.split}-selected-latest.json" if args.case_ids else f"{case_prefix}{args.split}-latest.json"
    output = reports / report_name
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n准确率：{passed}/{len(results)} ({report['execution_accuracy']:.1%})")
    print(f"严格准确率：{strict_passed}/{len(results)} ({report['strict_execution_accuracy']:.1%})")
    print(f"报告：{output}")


if __name__ == "__main__":
    main()
