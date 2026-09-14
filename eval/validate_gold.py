import json
import os
import argparse
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def main():
    parser = argparse.ArgumentParser(description="验证测试集中的 gold SQL")
    parser.add_argument("--case-file", default="cases.json")
    args = parser.parse_args()
    case_path = (ROOT / "eval" / args.case_file).resolve()
    if (ROOT / "eval").resolve() not in case_path.parents:
        raise SystemExit("测试集文件必须位于 eval 目录")
    cases = json.loads(case_path.read_text(encoding="utf-8"))
    database_url = os.getenv("DATABASE_URL")
    failures = []
    executed = 0
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            for case in cases:
                if not case["gold_sql"]:
                    continue
                try:
                    with conn.cursor() as cur:
                        cur.execute(case["gold_sql"])
                        cur.fetchall()
                    executed += 1
                except Exception as exc:
                    failures.append({"id": case["id"], "error": str(exc)})
                    conn.rollback()
                    conn.execute("BEGIN READ ONLY")
    print(f"cases={len(cases)} gold_executed={executed} failures={len(failures)}")
    for failure in failures:
        print(f"FAIL {failure['id']}: {failure['error']}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
