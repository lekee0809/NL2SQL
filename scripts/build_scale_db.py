"""Create an isolated, deterministic PostgreSQL scale fixture; never overwrite a database."""

import argparse
import re
from pathlib import Path
from time import perf_counter

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.config import settings


ROOT = Path(__file__).resolve().parent.parent


def database_url(name: str) -> str:
    if not re.fullmatch(r"nl2sql_scale_[a-z0-9_]{4,40}", name):
        raise ValueError("压测库名称必须以 nl2sql_scale_ 开头，且只包含小写字母、数字和下划线")
    if not settings.database_url:
        raise ValueError("DATABASE_URL 未配置")
    return make_conninfo(settings.database_url, dbname=name)


def build(name: str, orders: int, batch_size: int = 50_000) -> dict:
    target_url = database_url(name)
    if not 10_000 <= orders <= 1_000_000:
        raise ValueError("订单数量须在 1 万到 100 万之间")
    customers, products = 50_000, 10_000
    started = perf_counter()
    with psycopg.connect(settings.database_url, autocommit=True) as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists:
            raise ValueError("同名数据库已存在，拒绝覆盖；请换一个新名称")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))

    with psycopg.connect(target_url) as conn:
        conn.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        conn.execute("INSERT INTO customers(id, name, region) "
                     "SELECT i, '客户' || lpad(i::text, 6, '0'), "
                     "(ARRAY['华东','华北','华南','西南','西北'])[1 + i %% 5] "
                     "FROM generate_series(1, %s) AS i", (customers,))
        conn.execute("INSERT INTO products(id, name, category) "
                     "SELECT i, '商品' || lpad(i::text, 5, '0'), "
                     "(ARRAY['电子产品','办公用品','家居用品','食品'])[1 + i %% 4] "
                     "FROM generate_series(1, %s) AS i", (products,))
        conn.execute("INSERT INTO entity_aliases(source_id,entity_type,alias,alias_key,entity_id) "
                     "SELECT %s,'product','商品昵称-' || lpad(i::text,5,'0') || '-' || lpad(k::text,2,'0'), "
                     "'商品昵称' || lpad(i::text,5,'0') || lpad(k::text,2,'0'), i "
                     "FROM generate_series(1,%s) AS i CROSS JOIN generate_series(1,10) AS k",
                     (settings.default_source_id, products))
        conn.execute("INSERT INTO entity_aliases(source_id,entity_type,alias,alias_key,entity_id) "
                     "SELECT %s,'customer','客户昵称-' || lpad(i::text,6,'0') || '-' || lpad(k::text,2,'0'), "
                     "'客户昵称' || lpad(i::text,6,'0') || lpad(k::text,2,'0'), i "
                     "FROM generate_series(1,%s) AS i CROSS JOIN generate_series(1,2) AS k",
                     (settings.default_source_id, customers))
        conn.execute("INSERT INTO entity_aliases(source_id,entity_type,alias,alias_key,entity_id) "
                     "VALUES (%s,'product','共享商品','共享商品',1),"
                     "(%s,'product','共享商品','共享商品',2)",
                     (settings.default_source_id, settings.default_source_id))
        conn.commit()
        print(f"dimensions ready: customers={customers}, products={products}, aliases=200002", flush=True)

        for start in range(1, orders + 1, batch_size):
            end = min(orders, start + batch_size - 1)
            conn.execute("INSERT INTO orders(id,customer_id,created_at,status,total_amount) "
                         "SELECT i, 1 + ((i::bigint * 7919) %% %s)::int, "
                         "DATE '2023-01-01' + ((i * 37) %% 1461), "
                         "CASE WHEN i %% 10 < 8 THEN 'PAID' WHEN i %% 10 = 8 "
                         "THEN 'CANCELLED' ELSE 'REFUNDED' END, "
                         "((1 + i %% 5) * (20 + i %% 500) + "
                         "(1 + i %% 3) * (15 + i %% 300))::numeric(12,2) "
                         "FROM generate_series(%s,%s) AS i", (customers, start, end))
            conn.execute("INSERT INTO order_items(id,order_id,product_id,quantity,unit_price) "
                         "SELECT 2 * (i - 1) + j, i, "
                         "1 + ((i::bigint * 8191 + j * 131) %% %s)::int, "
                         "CASE WHEN j = 1 THEN 1 + i %% 5 ELSE 1 + i %% 3 END, "
                         "CASE WHEN j = 1 THEN 20 + i %% 500 ELSE 15 + i %% 300 END "
                         "FROM generate_series(%s,%s) AS i "
                         "CROSS JOIN generate_series(1,2) AS j", (products, start, end))
            conn.execute("INSERT INTO payments(id,order_id,paid_at,method) "
                         "SELECT i,i,DATE '2023-01-01' + ((i * 37) %% 1461), "
                         "(ARRAY['card','alipay','wechat','bank'])[1 + i %% 4] "
                         "FROM generate_series(%s,%s) AS i WHERE i %% 10 < 8", (start, end))
            conn.commit()
            print(f"orders loaded: {end}/{orders}", flush=True)

        for table, value in (("customers", customers), ("products", products),
                             ("orders", orders), ("order_items", orders * 2),
                             ("payments", orders)):
            conn.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, true)",
                         (table, value))
        conn.commit()
        conn.execute("ANALYZE")
        conn.commit()
        counts = conn.execute("SELECT "
                              "(SELECT count(*) FROM customers), "
                              "(SELECT count(*) FROM products), "
                              "(SELECT count(*) FROM orders), "
                              "(SELECT count(*) FROM order_items), "
                              "(SELECT count(*) FROM payments), "
                              "(SELECT count(*) FROM entity_aliases)").fetchone()
        size_bytes = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    return {"database": name, "customers": counts[0], "products": counts[1],
            "orders": counts[2], "order_items": counts[3], "payments": counts[4],
            "aliases": counts[5], "size_mb": round(size_bytes / 1024 / 1024, 1),
            "build_seconds": round(perf_counter() - started, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description="构建独立 PostgreSQL 压测库，不覆盖已有库")
    parser.add_argument("--database", default="nl2sql_scale_20260927")
    parser.add_argument("--orders", type=int, default=500_000)
    parser.add_argument("--apply", action="store_true", help="明确创建并填充新数据库")
    args = parser.parse_args()
    database_url(args.database)
    if not 10_000 <= args.orders <= 1_000_000:
        parser.error("订单数量须在 1 万到 100 万之间")
    if not args.apply:
        print(f"DRY RUN: create {args.database} with {args.orders} orders, "
              f"{args.orders * 2} items and 200002 aliases; add --apply to execute")
        return
    print(build(args.database, args.orders))


if __name__ == "__main__":
    main()
