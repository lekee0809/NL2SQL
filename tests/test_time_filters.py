from datetime import date

import pytest

from app.query_spec import QuerySpec, compile_query


REFERENCE_DATE = date(2026, 9, 14)


def time_spec(operator: str, value: str = "", values: list[str] | None = None, field: str = "order_date"):
    return QuerySpec.model_validate({
        "metrics": ["sales_amount"],
        "dimensions": [],
        "filters": [{"field": field, "operator": operator, "value": value, "values": values or []}],
        "order_by": [],
        "limit": None,
        "comparison": None,
    })


@pytest.mark.parametrize(
    ("operator", "expected_start", "expected_end"),
    [
        ("this_year", "2026-01-01", "2027-01-01"),
        ("last_year", "2025-01-01", "2026-01-01"),
        ("this_month", "2026-09-01", "2026-10-01"),
        ("last_month", "2026-08-01", "2026-09-01"),
        ("this_quarter", "2026-07-01", "2026-10-01"),
        ("last_quarter", "2026-04-01", "2026-07-01"),
    ],
)
def test_named_relative_periods(operator, expected_start, expected_end):
    compiled = compile_query(time_spec(operator), reference_date=REFERENCE_DATE)
    assert str(compiled.params[0]) == expected_start
    assert str(compiled.params[1]) == expected_end


def test_recent_days_are_inclusive_of_reference_day():
    compiled = compile_query(time_spec("last_n_days", "7"), reference_date=REFERENCE_DATE)
    assert tuple(map(str, compiled.params[:2])) == ("2026-09-08", "2026-09-15")


def test_recent_months_use_a_rolling_range():
    compiled = compile_query(time_spec("last_n_months", "3"), reference_date=REFERENCE_DATE)
    assert tuple(map(str, compiled.params[:2])) == ("2026-06-14", "2026-09-15")


def test_calendar_month_does_not_require_model_to_know_last_day():
    compiled = compile_query(time_spec("calendar_month", "2024-02"), reference_date=REFERENCE_DATE)
    assert tuple(map(str, compiled.params[:2])) == ("2024-02-01", "2024-03-01")


def test_calendar_quarter_crosses_year_correctly():
    compiled = compile_query(time_spec("calendar_quarter", "2025-Q4"), reference_date=REFERENCE_DATE)
    assert tuple(map(str, compiled.params[:2])) == ("2025-10-01", "2026-01-01")


def test_inclusive_month_range_uses_next_month_as_exclusive_end():
    compiled = compile_query(
        time_spec("month_range", values=["2025-01", "2025-03"]),
        reference_date=REFERENCE_DATE,
    )
    assert tuple(map(str, compiled.params[:2])) == ("2025-01-01", "2025-04-01")


@pytest.mark.parametrize(
    ("operator", "value"),
    [("last_n_days", "0"), ("last_n_months", "121"), ("calendar_month", "2025-13")],
)
def test_invalid_relative_ranges_are_rejected(operator, value):
    with pytest.raises(ValueError):
        compile_query(time_spec(operator, value), reference_date=REFERENCE_DATE)


def test_time_operator_cannot_target_region():
    with pytest.raises(ValueError, match="时间维度"):
        compile_query(time_spec("last_year", field="region"), reference_date=REFERENCE_DATE)
