import pytest

from app.query_spec import DIMENSIONS, METRICS, QuerySpec, compile_query, query_spec_json_schema
from app.sql_guard import validate_readonly_sql


def make_spec(**changes):
    data = {
        "metrics": ["sales_amount"],
        "dimensions": ["product"],
        "filters": [],
        "order_by": [{"field": "sales_amount", "direction": "desc"}],
        "limit": 3,
        "comparison": None,
    }
    data.update(changes)
    return QuerySpec.model_validate(data)


def test_compiler_builds_joins_grouping_and_paid_filter():
    compiled = compile_query(make_spec())
    assert "FROM orders o" in compiled.sql
    assert "JOIN order_items oi" in compiled.sql
    assert "JOIN products p" in compiled.sql
    assert "GROUP BY 1" in compiled.sql
    assert "ORDER BY 2 DESC" in compiled.sql
    assert compiled.params == ("PAID",)
    validated = validate_readonly_sql(compiled.sql)
    assert "%s" in validated
    assert "SELECT" in validated


def test_filter_values_are_parameters_not_sql_text():
    spec = make_spec(filters=[{
        "field": "product", "operator": "contains", "value": "茶' OR TRUE --", "values": []
    }])
    compiled = compile_query(spec)
    assert "OR TRUE" not in compiled.sql
    assert compiled.params[0] == "%茶' OR TRUE --%"


def test_year_filter_uses_date_range():
    spec = make_spec(filters=[{
        "field": "order_date", "operator": "year", "value": "2025", "values": []
    }])
    compiled = compile_query(spec)
    assert "o.created_at >= %s AND o.created_at < %s" in compiled.sql
    assert str(compiled.params[0]) == "2025-01-01"
    assert str(compiled.params[1]) == "2026-01-01"


def test_explicit_status_replaces_default_paid_filter():
    spec = make_spec(filters=[{
        "field": "order_status", "operator": "eq", "value": "REFUNDED", "values": []
    }])
    assert compile_query(spec).params == ("REFUNDED",)


def test_order_field_must_be_selected():
    with pytest.raises(ValueError):
        make_spec(order_by=[{"field": "region", "direction": "asc"}])


def test_invalid_metric_is_rejected_before_compilation():
    with pytest.raises(ValueError):
        make_spec(metrics=["drop_table"])


def test_order_amount_rejects_product_grain_to_prevent_double_counting():
    with pytest.raises(ValueError, match="订单粒度"):
        make_spec(
            metrics=["order_amount"],
            order_by=[{"field": "order_amount", "direction": "desc"}],
        )


def test_model_schema_ids_are_generated_from_business_dictionary():
    schema = query_spec_json_schema()
    assert set(schema["properties"]["metrics"]["items"]["enum"]) == set(METRICS)
    assert set(schema["properties"]["dimensions"]["items"]["enum"]) == set(DIMENSIONS)
    assert set(schema["$defs"]["FilterSpec"]["properties"]["field"]["enum"]) == set(DIMENSIONS)
