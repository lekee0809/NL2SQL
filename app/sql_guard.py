import re
import sqlglot
from sqlglot import exp


class UnsafeSQL(ValueError):
    pass


FORBIDDEN_NODE_KEYS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "copy", "command", "merge", "transaction",
}


def validate_readonly_sql(sql: str) -> str:
    candidate = sql.strip().removeprefix("```sql").removesuffix("```").strip()
    if not candidate:
        raise UnsafeSQL("模型没有返回 SQL")
    if re.search(r"\bFOR\s+(UPDATE|SHARE)\b", candidate, re.I):
        raise UnsafeSQL("禁止使用行锁")
    try:
        statements = sqlglot.parse(candidate, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise UnsafeSQL(f"SQL 无法解析：{exc}") from exc
    if len(statements) != 1:
        raise UnsafeSQL("只允许执行一条 SQL")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise UnsafeSQL("只允许 SELECT/WITH 查询")
    for node in statement.walk():
        if node.key.lower() in FORBIDDEN_NODE_KEYS:
            raise UnsafeSQL(f"检测到禁止操作：{node.key}")
    return statement.sql(dialect="postgres")
