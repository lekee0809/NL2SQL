import pytest
from app.sql_guard import UnsafeSQL, validate_readonly_sql


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders",
    "WITH x AS (SELECT 1 AS n) SELECT * FROM x",
])
def test_allows_readonly(sql):
    assert validate_readonly_sql(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "SELECT 1; DROP TABLE orders",
    "UPDATE orders SET status = 'PAID'",
    "SELECT * FROM orders FOR UPDATE",
])
def test_blocks_writes(sql):
    with pytest.raises(UnsafeSQL):
        validate_readonly_sql(sql)
