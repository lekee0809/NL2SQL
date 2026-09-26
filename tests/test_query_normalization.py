from app.query_normalization import normalize_query_spec
from app.query_spec import QuerySpec


def spec(operator: str, value: str = "华东") -> QuerySpec:
    return QuerySpec.model_validate({
        "metrics": ["sales_amount"], "dimensions": ["product"],
        "filters": [{"field": "region", "operator": operator, "value": value, "values": []}],
        "order_by": [], "limit": 5, "comparison": None,
    })


def test_named_region_is_normalized_to_exact_match():
    original = spec("contains")
    normalized, changes = normalize_query_spec(original, "2025年华东地区销售额最高的商品")
    assert normalized.filters[0].operator == "eq"
    assert changes[0]["field"] == "region"
    assert original.filters[0].operator == "contains"


def test_explicit_fuzzy_region_is_preserved():
    normalized, changes = normalize_query_spec(spec("contains"), "地区名称包含华东的客户")
    assert normalized.filters[0].operator == "contains"
    assert changes == []


def test_institution_name_does_not_trigger_region_rewrite():
    normalized, changes = normalize_query_spec(spec("contains"), "华东地区第一实验学校的销售额")
    assert normalized.filters[0].operator == "contains"
    assert changes == []


def test_explicit_last_year_repairs_non_numeric_year_value():
    original = QuerySpec.model_validate({
        "metrics": ["sales_amount"], "dimensions": ["order_month"],
        "filters": [{"field": "order_year", "operator": "eq", "value": "last_year", "values": []}],
        "order_by": [{"field": "order_month", "direction": "asc"}],
        "limit": None, "comparison": None,
    })
    normalized, changes = normalize_query_spec(original, "去年每月销售额趋势")
    assert normalized.filters[0].field == "order_date"
    assert normalized.filters[0].operator == "last_year"
    assert normalized.filters[0].value == ""
    assert changes[0]["reason"] == "问题明确使用相对时间"
    assert original.filters[0].value == "last_year"


def test_relative_value_is_not_repaired_without_matching_question():
    original = QuerySpec.model_validate({
        "metrics": ["sales_amount"], "dimensions": [],
        "filters": [{"field": "order_year", "operator": "eq", "value": "last_year", "values": []}],
        "order_by": [], "limit": None, "comparison": None,
    })
    normalized, changes = normalize_query_spec(original, "2025年销售额")
    assert normalized == original
    assert changes == []
