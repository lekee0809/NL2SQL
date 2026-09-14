from typing import Any
import psycopg
from psycopg import sql as pg_sql
from psycopg.rows import dict_row
from .config import settings


def check_database() -> dict[str, Any]:
    if not settings.database_url:
        return {"ok": False, "error": "DATABASE_URL 未配置"}
    try:
        with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), version()")
                database, version = cur.fetchone()
        return {"ok": True, "database": database, "version": version}
    except psycopg.Error as exc:
        return {"ok": False, "error": str(exc)}


def execute_readonly(sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL 未配置")
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = {settings.statement_timeout_ms}")
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchmany(settings.max_result_rows)


def fetch_distinct_text_values(table: str, column: str, limit: int = 1000) -> list[str]:
    """从后端白名单指定的字段读取候选值，不接受用户提供的标识符。"""
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL 未配置")
    statement = pg_sql.SQL(
        "SELECT DISTINCT {column} FROM {table} "
        "WHERE {column} IS NOT NULL ORDER BY {column} LIMIT %s"
    ).format(column=pg_sql.Identifier(column), table=pg_sql.Identifier(table))
    with psycopg.connect(settings.database_url) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = {settings.statement_timeout_ms}")
            with conn.cursor() as cur:
                cur.execute(statement, (limit,))
                return [str(row[0]) for row in cur.fetchall()]
