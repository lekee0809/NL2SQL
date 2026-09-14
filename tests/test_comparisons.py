from datetime import date

import pytest

from app.query_spec import QuerySpec, compile_query
from app.sql_guard import validate_readonly_sql


REFERENCE_DATE = date(2026, 9, 14)


def comparison_spec(comparison_type: str, operator: str, value: str = "", values=None, **changes):
    data = {
        "metrics": ["sales_amount"],
        "dimensions": [],
        "filters": [{
            "field": "order_date", "operator": operator, "value": value, "values": values or []
        }],
        "order_by": [],
        "limit": None,
        "comparison": {"type": comparison_type},
    }
    data.update(changes)
    return QuerySpec.model_validate(data)


def test_last_year_year_over_year_compiles_two_safe_periods():
    compiled = compile_query(
        comparison_spec("year_over_year", "last_year"), reference_date=REFERENCE_DATE
    )
    assert tuple(map(str, compiled.params[:2])) == ("2025-01-01", "2026-01-01")
    assert tuple(map(str, compiled.params[3:5])) == ("2024-01-01", "2025-01-01")
    assert compiled.params[2] == compiled.params[5] == "PAID"
    validated = validate_readonly_sql(compiled.sql)
    assert "同比增长率" in validated


def test_calendar_quarter_period_over_period_uses_previous_quarter():
    compiled = compile_query(
        comparison_spec("period_over_period", "calendar_quarter", "2025-Q1"),
        reference_date=REFERENCE_DATE,
    )
    assert tuple(map(str, compiled.params[:2])) == ("2025-01-01", "2025-04-01")
    assert tuple(map(str, compiled.params[3:5])) == ("2024-10-01", "2025-01-01")
    assert "环比增长率" in compiled.sql


def test_three_month_range_compares_with_previous_three_calendar_months():
    compiled = compile_query(
        comparison_spec("period_over_period", "month_range", values=["2025-01", "2025-03"]),
        reference_date=REFERENCE_DATE,
    )
    assert tuple(map(str, compiled.params[:2])) == ("2025-01-01", "2025-04-01")
    assert tuple(map(str, compiled.params[3:5])) == ("2024-10-01", "2025-01-01")


def test_seven_day_period_over_period_uses_previous_seven_days():
    compiled = compile_query(
        comparison_spec("period_over_period", "last_n_days", "7"), reference_date=REFERENCE_DATE
    )
    assert tuple(map(str, compiled.params[:2])) == ("2026-09-08", "2026-09-15")
    assert tuple(map(str, compiled.params[3:5])) == ("2026-09-01", "2026-09-08")


def test_comparison_preserves_non_time_filters_and_parameters_twice():
    spec = comparison_spec("year_over_year", "last_year")
    data = spec.model_dump()
    data["filters"].append({"field": "region", "operator": "eq", "value": "华东", "values": []})
    compiled = compile_query(QuerySpec.model_validate(data), reference_date=REFERENCE_DATE)
    assert compiled.params == (
        date(2025, 1, 1), date(2026, 1, 1), "华东", "PAID",
        date(2024, 1, 1), date(2025, 1, 1), "华东", "PAID",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"metrics": ["sales_amount", "order_count"]},
        {"dimensions": ["region"]},
        {"order_by": [{"field": "sales_amount", "direction": "desc"}]},
        {"filters": []},
    ],
)
def test_unsupported_comparison_shapes_are_rejected(changes):
    with pytest.raises(ValueError):
        comparison_spec("year_over_year", "last_year", **changes)
