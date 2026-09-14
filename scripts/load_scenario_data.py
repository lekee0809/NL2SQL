from pathlib import Path

import psycopg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent


def main():
    load_dotenv(ROOT / ".env")
    from app.config import settings

    if not settings.database_url:
        raise SystemExit("DATABASE_URL 未配置")
    sql = (ROOT / "db" / "seed_scenarios.sql").read_text(encoding="utf-8")
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(sql)
        counts = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM customers WHERE id BETWEEN 1001 AND 1008), "
            "(SELECT COUNT(*) FROM products WHERE id BETWEEN 1001 AND 1010), "
            "(SELECT COUNT(*) FROM orders WHERE id BETWEEN 10001 AND 10020), "
            "(SELECT COUNT(*) FROM order_items WHERE id BETWEEN 20001 AND 20040)"
        ).fetchone()
    print(
        f"scenario customers={counts[0]} products={counts[1]} "
        f"orders={counts[2]} order_items={counts[3]}"
    )


if __name__ == "__main__":
    main()
