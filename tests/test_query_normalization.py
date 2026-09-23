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
