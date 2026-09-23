from eval.run_plan_eval import compare_specs


def spec(filters):
    return {
        "metrics": ["sales_amount"],
        "dimensions": [],
        "filters": filters,
        "order_by": [],
        "limit": None,
        "comparison": None,
    }


def filter_(field, operator, value="", values=None):
    return {"field": field, "operator": operator, "value": value, "values": values or []}


def test_filter_order_does_not_affect_score():
    expected = spec([
        filter_("product", "eq", "15号测试商品"),
        filter_("order_year", "eq", "2025"),
    ])
    actual = spec(list(reversed(expected["filters"])))
    assert compare_specs(expected, actual)["passed"]


def test_invalid_time_operator_and_value_fail_score():
    expected = spec([filter_("order_year", "eq", "2025")])
    actual = spec([filter_("order_year", "calendar_month", "2025-15")])
    result = compare_specs(expected, actual)
    assert not result["passed"]
    assert not result["checks"]["filters"]


def test_equivalent_year_fields_and_operators_pass():
    expected = spec([filter_("order_year", "eq", "2025")])
    actual = spec([filter_("order_date", "year", "2025")])
    assert compare_specs(expected, actual)["passed"]


def test_in_value_order_does_not_affect_score():
    expected = spec([filter_("region", "in", values=["华东", "华南"])])
    actual = spec([filter_("region", "in", values=["华南", "华东"])])
    assert compare_specs(expected, actual)["passed"]


def test_wrong_entity_value_fails_score():
    expected = spec([filter_("product", "eq", "15号测试商品")])
    actual = spec([filter_("product", "eq", "测试商品")])
    assert not compare_specs(expected, actual)["passed"]
