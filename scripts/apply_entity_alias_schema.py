"""Apply the additive entity-alias migration to the configured PostgreSQL DB."""

import argparse
from pathlib import Path

import psycopg

from app.config import settings


MIGRATION = Path(__file__).resolve().parent.parent / "db" / "entity_aliases_migration.sql"


def main() -> None:
    parser = argparse.ArgumentParser(description="安装实体别名表；默认只显示目标，不修改数据库")
    parser.add_argument("--apply", action="store_true", help="执行增量迁移")
    args = parser.parse_args()
    if not settings.database_url:
        parser.error("DATABASE_URL 未配置")
    with psycopg.connect(settings.database_url) as conn:
        database = conn.execute("SELECT current_database()").fetchone()[0]
        exists = conn.execute("SELECT to_regclass('public.entity_aliases')").fetchone()[0] is not None
        if not args.apply:
            print({"database": database, "alias_table_exists": exists, "dry_run": True})
            return
        with conn.transaction():
            conn.execute(MIGRATION.read_text(encoding="utf-8"))
        print({"database": database, "alias_table_ready": True, "dry_run": False})


if __name__ == "__main__":
    main()
