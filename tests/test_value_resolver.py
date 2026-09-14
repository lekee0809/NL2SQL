import pytest

from app.query_spec import QuerySpec
from app.query_spec import compile_query
from app.value_resolver import NeedsClarification, infer_entity_dimensions, resolve_query_spec


def spec_for(field: str, value: str) -> QuerySpec:
    return QuerySpec.model_validate({
        "metrics": ["sales_amount"],
        "dimensions": ["product"],
        "filters": [{"field": field, "operator": "eq", "value": value, "values": []}],
        "order_by": [],
        "limit": 10,
        "comparison": None,
    })


def test_normalized_exact_match_is_automatic():
    resolved, changes = resolve_query_spec(
        spec_for("product", "测试商品1"),
        loader=lambda field: ("测试商品_1", "测试商品_2"),
    )
    assert resolved.filters[0].value == "测试商品_1"
    assert changes[0].method == "exact"


def test_region_suffix_is_normalized():
    resolved, _ = resolve_query_spec(
        spec_for("region", "华东地区"),
        loader=lambda field: ("华东", "华北"),
    )
    assert resolved.filters[0].value == "华东"


def test_entity_number_can_appear_before_the_name():
    resolved, changes = resolve_query_spec(
        spec_for("product", "15号测试商品"),
        loader=lambda field: ("测试商品_5", "测试商品_15", "测试商品_25"),
    )
    assert resolved.filters[0].value == "测试商品_15"
    assert changes[0].score == 1.0


def test_ambiguous_entity_requests_clarification():
    with pytest.raises(NeedsClarification) as captured:
        resolve_query_spec(
            spec_for("product", "测试商品"),
            loader=lambda field: ("测试商品_1", "测试商品_2", "测试商品_3"),
        )
    assert len(captured.value.candidates) == 3
    assert captured.value.detail()["type"] == "needs_clarification"


def test_contains_filter_skips_entity_resolution():
    spec = spec_for("product", "测试商品")
    data = spec.model_dump()
    data["filters"][0]["operator"] = "contains"
    resolved, changes = resolve_query_spec(
        QuerySpec.model_validate(data),
        loader=lambda field: (_ for _ in ()).throw(AssertionError("loader should not run")),
    )
    assert resolved.filters[0].value == "测试商品"
    assert changes == []


@pytest.mark.parametrize(
    ("field", "requested", "database_values", "expected"),
    [
        ("payment_method", "支付宝", ("alipay", "bank", "card", "wechat"), "alipay"),
        ("payment_method", "微信支付", ("alipay", "bank", "card", "wechat"), "wechat"),
        ("category", "办公", ("办公用品", "家居用品", "电子产品", "食品"), "办公用品"),
        ("order_status", "已退款", ("PAID", "CANCELLED", "REFUNDED"), "REFUNDED"),
    ],
)
def test_business_aliases_map_to_database_codes(field, requested, database_values, expected):
    resolved, changes = resolve_query_spec(
        spec_for(field, requested),
        loader=lambda _: database_values,
    )
    assert resolved.filters[0].value == expected
    assert changes[0].method == "alias"


def test_resolved_refund_status_does_not_reapply_paid_default():
    resolved, _ = resolve_query_spec(
        spec_for("order_status", "退款"),
        loader=lambda _: ("PAID", "CANCELLED", "REFUNDED"),
    )
    compiled = compile_query(resolved)
    assert compiled.params == ("REFUNDED",)


def test_alias_is_not_used_when_target_is_absent_from_database():
    with pytest.raises(ValueError, match="找不到"):
        resolve_query_spec(
            spec_for("payment_method", "支付宝"),
            loader=lambda _: ("bank", "card", "wechat"),
        )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("2025年极光键盘标准版销售额", {"product"}),
        ("华东地区第一实验学校2025年订单数", {"customer"}),
        ("2025年智慧教学白板销售额", {"product"}),
        ("2025年各地区销售额", set()),
    ],
)
def test_entity_dimensions_are_inferred_locally(question, expected):
    values = {
        "product": ("极光键盘-标准版", "智慧教学白板A1", "智慧教学白板A2"),
        "customer": ("华东地区第一实验学校", "北辰科技公司"),
    }
    assert infer_entity_dimensions(question, loader=lambda field: values[field]) == expected
