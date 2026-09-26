import pytest
from datetime import datetime, timedelta, timezone

from app.conversation import (
    ConversationStore,
    SessionConflictError,
    QuerySpecPatch,
    apply_query_spec_patch,
    parse_local_patch,
    query_spec_patch_json_schema,
)
from app.query_spec import QuerySpec


def current_spec() -> QuerySpec:
    return QuerySpec.model_validate({
        "metrics": ["sales_amount"],
        "dimensions": ["product"],
        "filters": [
            {"field": "order_year", "operator": "eq", "value": "2025", "values": []},
            {"field": "region", "operator": "eq", "value": "华东", "values": []},
        ],
        "order_by": [{"field": "sales_amount", "direction": "desc"}],
        "limit": 10,
        "comparison": None,
    })


def patch(**changes) -> QuerySpecPatch:
    data = {
        "reset": False,
        "set_metrics": None,
        "set_dimensions": None,
        "upsert_filters": [],
        "remove_filter_fields": [],
        "set_order_by": None,
        "set_limit": None,
        "clear_limit": False,
        "set_comparison": None,
        "clear_comparison": False,
    }
    data.update(changes)
    return QuerySpecPatch.model_validate(data)


def test_patch_replaces_one_filter_and_preserves_unmentioned_state():
    updated, changes = apply_query_spec_patch(
        current_spec(),
        patch(upsert_filters=[{
            "field": "region", "operator": "eq", "value": "华南", "values": []
        }]),
    )
    assert [(item.field, item.value) for item in updated.filters] == [
        ("order_year", "2025"), ("region", "华南")
    ]
    assert updated.metrics == ["sales_amount"]
    assert updated.dimensions == ["product"]
    assert updated.limit == 10
    assert changes == ["已更新过滤条件：region"]


def test_patch_can_remove_filter_and_clear_limit():
    updated, _ = apply_query_spec_patch(
        current_spec(), patch(remove_filter_fields=["region"], clear_limit=True)
    )
    assert [item.field for item in updated.filters] == ["order_year"]
    assert updated.limit is None


def test_patch_can_change_grouping_without_losing_filters():
    updated, _ = apply_query_spec_patch(
        current_spec(),
        patch(
            set_dimensions=["order_month"],
            set_order_by=[{"field": "order_month", "direction": "asc"}],
        ),
    )
    assert updated.dimensions == ["order_month"]
    assert {item.field for item in updated.filters} == {"order_year", "region"}


def test_reset_requires_patch_to_build_a_complete_valid_query():
    updated, changes = apply_query_spec_patch(
        current_spec(),
        patch(
            reset=True,
            set_metrics=["order_count"],
            upsert_filters=[{
                "field": "order_date", "operator": "last_month", "value": "", "values": []
            }],
        ),
    )
    assert updated.metrics == ["order_count"]
    assert updated.dimensions == []
    assert [item.field for item in updated.filters] == ["order_date"]
    assert changes[0] == "已重置上一轮查询条件"


def test_invalid_merged_shape_does_not_mutate_current_spec():
    original = current_spec()
    with pytest.raises(ValueError):
        apply_query_spec_patch(
            original,
            patch(set_metrics=[], set_dimensions=[], set_order_by=[]),
        )
    assert original.dimensions == ["product"]
    assert original.order_by[0].field == "sales_amount"


def test_patch_rejects_conflicting_limit_actions():
    with pytest.raises(ValueError, match="同时设置和清空"):
        patch(set_limit=5, clear_limit=True)


def test_empty_patch_is_rejected_before_database_execution():
    with pytest.raises(ValueError, match="没有识别到有效"):
        apply_query_spec_patch(current_spec(), patch())


def test_patch_schema_contains_catalog_whitelists():
    schema = query_spec_patch_json_schema()
    metric_array = next(
        item for item in schema["properties"]["set_metrics"]["anyOf"]
        if item.get("type") == "array"
    )
    assert "sales_amount" in metric_array["items"]["enum"]
    assert "region" in schema["properties"]["remove_filter_fields"]["items"]["enum"]


def test_conversation_store_returns_copies_and_counts_turns():
    store = ConversationStore()
    created = store.create(current_spec(), "2025年华东商品销售额")
    fetched = store.get(created.session_id)
    fetched.query_spec.filters[1].value = "被外部修改"
    assert store.get(created.session_id).query_spec.filters[1].value == "华东"

    updated = store.update(created.session_id, current_spec(), "改成华南")
    assert updated.turn_count == 2
    assert updated.first_message == "2025年华东商品销售额"
    assert store.delete(created.session_id)
    with pytest.raises(KeyError):
        store.get(created.session_id)


def test_memory_store_lists_recent_sessions_without_exposing_mutable_state():
    store = ConversationStore()
    older = store.create(current_spec(), "第一题")
    newer = store.create(current_spec(), "第二题")
    listed = store.list_recent()
    assert {item.session_id for item in listed} == {older.session_id, newer.session_id}
    listed[0].query_spec.metrics.clear()
    assert store.get(listed[0].session_id).query_spec.metrics


def test_memory_store_rejects_stale_update():
    store = ConversationStore()
    state = store.create(current_spec(), "第一题")
    store.update(state.session_id, current_spec(), "第二题", expected_turn_count=1)
    with pytest.raises(SessionConflictError):
        store.update(state.session_id, current_spec(), "过期续问", expected_turn_count=1)
    assert store.get(state.session_id).last_message == "第二题"


def test_memory_store_caps_saved_sessions():
    store = ConversationStore(max_sessions=2)
    first = store.create(current_spec(), "第一题")
    store.create(current_spec(), "第二题")
    store.create(current_spec(), "第三题")
    assert len(store.list_recent()) == 2
    with pytest.raises(KeyError):
        store.get(first.session_id)


def test_conversation_store_expires_inactive_sessions():
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    clock_value = [now]
    store = ConversationStore(ttl_seconds=60, clock=lambda: clock_value[0])
    created = store.create(current_spec(), "初始问题")
    clock_value[0] = now + timedelta(seconds=59)
    assert store.get(created.session_id).session_id == created.session_id
    clock_value[0] = now + timedelta(seconds=60)
    with pytest.raises(KeyError, match="已过期"):
        store.get(created.session_id)


def test_store_purge_reports_number_of_expired_sessions():
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    clock_value = [now]
    store = ConversationStore(ttl_seconds=10, clock=lambda: clock_value[0])
    store.create(current_spec(), "问题一")
    store.create(current_spec(), "问题二")
    clock_value[0] = now + timedelta(seconds=10)
    assert store.purge_expired() == 2


def test_local_patch_combines_region_and_top_k_without_model():
    parsed = parse_local_patch("改成华南，只看前五个", current_spec())
    assert parsed is not None
    updated, _ = apply_query_spec_patch(current_spec(), parsed)
    assert next(item.value for item in updated.filters if item.field == "region") == "华南"
    assert updated.limit == 5
    assert next(item.value for item in updated.filters if item.field == "order_year") == "2025"


def test_local_patch_removes_region_filter():
    parsed = parse_local_patch("去掉地区限制", current_spec())
    updated, _ = apply_query_spec_patch(current_spec(), parsed)
    assert [item.field for item in updated.filters] == ["order_year"]


def test_local_patch_replaces_all_old_time_filters_with_year():
    data = current_spec().model_dump()
    data["filters"][0] = {
        "field": "order_date", "operator": "last_year", "value": "", "values": []
    }
    current = QuerySpec.model_validate(data)
    parsed = parse_local_patch("改成2024年", current)
    updated, _ = apply_query_spec_patch(current, parsed)
    time_filters = [item for item in updated.filters if item.field.startswith("order_")]
    assert [(item.field, item.operator, item.value) for item in time_filters] == [
        ("order_year", "eq", "2024")
    ]


def test_local_patch_changes_grouping_and_order_together():
    parsed = parse_local_patch("按月份看", current_spec())
    updated, _ = apply_query_spec_patch(current_spec(), parsed)
    assert updated.dimensions == ["order_month"]
    assert updated.order_by[0].field == "order_month"
    assert updated.order_by[0].direction == "asc"


def test_local_patch_falls_back_when_any_meaningful_fragment_is_unknown():
    assert parse_local_patch("改成华南并预测明年趋势", current_spec()) is None


def test_local_patch_can_clear_limit():
    parsed = parse_local_patch("不限条数", current_spec())
    updated, _ = apply_query_spec_patch(current_spec(), parsed)
    assert updated.limit is None


def test_relative_time_and_comparison_replace_fixed_year_together():
    data = current_spec().model_dump()
    data["dimensions"] = []
    data["order_by"] = []
    current = QuerySpec.model_validate(data)
    parsed = parse_local_patch("换成去年并做同比", current)
    assert parsed is not None
    updated, _ = apply_query_spec_patch(current, parsed)
    time_filters = [item for item in updated.filters if item.field in {
        "order_date", "order_month", "order_quarter", "order_year"
    }]
    assert [(item.field, item.operator) for item in time_filters] == [
        ("order_date", "last_year")
    ]
    assert updated.comparison.type == "year_over_year"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("改成华东", {"region": "华东"}),
        ("改成华北", {"region": "华北"}),
        ("换成华南地区", {"region": "华南"}),
        ("只看西南区域", {"region": "西南"}),
        ("改看西北", {"region": "西北"}),
        ("前3个", {"limit": 3}),
        ("只看前十个", {"limit": 10}),
        ("取8条", {"limit": 8}),
        ("只看5个", {"limit": 5}),
        ("前一百个", {"limit": 100}),
        ("按月份看", {"dimensions": ["order_month"]}),
        ("每季度统计", {"dimensions": ["order_quarter"]}),
        ("逐年展示", {"dimensions": ["order_year"]}),
        ("改成2024年", {"year": "2024"}),
        ("只看2023年", {"year": "2023"}),
        ("换成2026年", {"year": "2026"}),
        ("去掉地区限制", {"removed": "region"}),
        ("取消时间条件", {"removed_time": True}),
        ("不限条数", {"limit": None}),
        ("改成华南，只看前五个", {"region": "华南", "limit": 5}),
        ("改成2024年，同时只看前3个", {"year": "2024", "limit": 3}),
        ("按销售额降序", {"order": ("sales_amount", "desc")}),
        ("按商品升序", {"order": ("product", "asc")}),
        ("改看订单数", {"metrics": ["order_count"]}),
        ("改看客户数", {"metrics": ["customer_count"]}),
        ("改成华南并预测明年趋势", None),
        ("只看华东和华南", None),
        ("顺便分析原因", None),
        ("重新查询订单数", None),
        ("同比呢", None),
    ],
)
def test_local_follow_up_scenario_matrix(message, expected):
    parsed = parse_local_patch(message, current_spec())
    if expected is None:
        assert parsed is None
        return
    assert parsed is not None
    updated, _ = apply_query_spec_patch(current_spec(), parsed)
    filters = {item.field: item for item in updated.filters}
    if "region" in expected:
        assert filters["region"].value == expected["region"]
    if "limit" in expected:
        assert updated.limit == expected["limit"]
    if "dimensions" in expected:
        assert updated.dimensions == expected["dimensions"]
    if "year" in expected:
        assert filters["order_year"].value == expected["year"]
    if expected.get("removed"):
        assert expected["removed"] not in filters
    if expected.get("removed_time"):
        assert not set(filters) & {"order_date", "order_month", "order_quarter", "order_year"}
    if "order" in expected:
        assert (updated.order_by[0].field, updated.order_by[0].direction) == expected["order"]
    if "metrics" in expected:
        assert updated.metrics == expected["metrics"]


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (["改成华南", "只看前五个", "改成2024年"], {"region": "华南", "limit": 5, "year": "2024"}),
        (["改成华北", "按月份看", "前3个"], {"region": "华北", "limit": 3, "dimensions": ["order_month"]}),
        (["去掉地区限制", "改成2023年", "前8个"], {"no_region": True, "limit": 8, "year": "2023"}),
        (["改看订单数", "改成华南", "只看前十个"], {"metrics": ["order_count"], "region": "华南", "limit": 10}),
        (["按销售额升序", "改成华北", "前4个"], {"order": ("sales_amount", "asc"), "region": "华北", "limit": 4}),
        (["不限条数", "改成西南", "改成2024年"], {"region": "西南", "limit": None, "year": "2024"}),
        (["每季度统计", "改成华南", "前6个"], {"dimensions": ["order_quarter"], "region": "华南", "limit": 6}),
        (["改成2024年", "取消时间条件", "改成华北"], {"region": "华北", "no_time": True}),
        (["改成华南", "去掉地区限制", "前7个"], {"no_region": True, "limit": 7}),
        (["前3个", "只看前8个", "改成华北"], {"region": "华北", "limit": 8}),
        (["逐年展示", "改看订单数", "改成华南"], {"dimensions": ["order_year"], "metrics": ["order_count"], "region": "华南"}),
        (["改看客户数", "按客户数降序", "前5个"], {"metrics": ["customer_count"], "order": ("customer_count", "desc"), "limit": 5}),
        (["改成西南", "按月份看", "前9个"], {"region": "西南", "dimensions": ["order_month"], "limit": 9}),
        (["改成2023年", "改成西北", "按销售额降序"], {"year": "2023", "region": "西北", "order": ("sales_amount", "desc")}),
        (["取消时间条件", "不限条数", "改成华南"], {"no_time": True, "limit": None, "region": "华南"}),
        (["每季度统计", "改成2024年", "改成华北"], {"dimensions": ["order_quarter"], "year": "2024", "region": "华北"}),
        (["改成华东", "改成2024年", "去掉地区限制"], {"no_region": True, "year": "2024"}),
        (["只看前十个", "改看销量", "按销量降序"], {"metrics": ["sold_quantity"], "order": ("sold_quantity", "desc"), "limit": 10}),
    ],
)
def test_three_turn_local_conversations(messages, expected):
    state = current_spec()
    for message in messages:
        parsed = parse_local_patch(message, state)
        assert parsed is not None, message
        state, _ = apply_query_spec_patch(state, parsed)

    filters = {item.field: item for item in state.filters}
    if "region" in expected:
        assert filters["region"].value == expected["region"]
    if expected.get("no_region"):
        assert "region" not in filters
    if "limit" in expected:
        assert state.limit == expected["limit"]
    if "year" in expected:
        assert filters["order_year"].value == expected["year"]
    if expected.get("no_time"):
        assert not set(filters) & {"order_date", "order_month", "order_quarter", "order_year"}
    if "dimensions" in expected:
        assert state.dimensions == expected["dimensions"]
    if "metrics" in expected:
        assert state.metrics == expected["metrics"]
    if "order" in expected:
        assert (state.order_by[0].field, state.order_by[0].direction) == expected["order"]
