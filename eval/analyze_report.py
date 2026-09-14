import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def execute(conn, sql):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return cur.fetchall()


def main():
    cases = {case["id"]: case for case in json.loads((ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))}
    report = json.loads((ROOT / "eval" / "reports" / "dev-latest.json").read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        for result in report["results"]:
            if result["passed"] or not result.get("sql"):
                continue
            case = cases[result["id"]]
            predicted = execute(conn, result["sql"])
            expected = execute(conn, case["gold_sql"])
            print(f"\n{result['id']} {case['question']}")
            print(f"predicted_count={len(predicted)} sample={predicted[:3]}")
            print(f"expected_count={len(expected)} sample={expected[:3]}")


if __name__ == "__main__":
    main()
