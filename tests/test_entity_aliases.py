import pytest

from app.conversation import QuerySpecPatch, apply_query_spec_patch, query_spec_patch_json_schema
from app.entity_aliases import EntityMatch, normalize_alias
from app.query_spec import QuerySpec, compile_query, query_spec_json_schema
from app.value_resolver import NeedsClarification, resolve_query_spec
from scripts.import_entity_aliases import read_aliases


def make_spec(value="办公室键盘"):
    return QuerySpec.model_validate({
        "metrics": ["sales_amount"], "dimensions": [],
        "filters": [
            {"field": "product", "operator": "eq", "value": value, "values": []},
            {"field": "order_year", "operator": "eq", "value": "2025", "values": []},
        ],
        "order_by": [], "limit": None, "comparison": None,
    })


def empty_patch(**overrides):
    data = {
        "reset": False, "set_metrics": None, "set_dimensions": None,
        "upsert_filters": [], "remove_filter_fields": [], "set_order_by": None,
        "set_limit": None, "clear_limit": False, "set_comparison": None,
        "clear_comparison": False,
    }
    data.update(overrides)
    return QuerySpecPatch.model_validate(data)


def test_alias_normalization_and_unique_id_filter():
    assert normalize_alias("  ＯＦＦＩＣＥ－键盘_ ") == "office键盘"
    resolved, changes = resolve_query_spec(
        make_spec(), loader=lambda field: ("极光键盘-标准版",),
        alias_lookup=lambda field, value: [EntityMatch(37, "极光键盘-标准版")],
    )
    assert resolved.filters[0].entity_id == 37
    assert resolved.filters[0].value == "极光键盘-标准版"
    assert changes[0].method == "entity_alias"
    compiled = compile_query(resolved)
    assert "p.id = %s" in compiled.sql
    assert 37 in compiled.params
    assert "办公室键盘" not in compiled.params


def test_ambiguous_alias_exposes_ids_and_never_guesses():
    with pytest.raises(NeedsClarification) as captured:
        resolve_query_spec(
            make_spec(), loader=lambda field: (),
            alias_lookup=lambda field, value: [
                EntityMatch(37, "极光键盘-标准版"),
                EntityMatch(42, "极光键盘-专业版"),
            ],
        )
    assert [item["entity_id"] for item in captured.value.candidates] == [37, 42]


def test_confirmed_id_survives_follow_up_even_if_name_changes():
    current = make_spec().model_copy(deep=True)
    current.filters[0].entity_id = 37
    current.filters[0].value = "旧商品名称"
    patch = empty_patch(
        remove_filter_fields=["order_year"],
        upsert_filters=[{"field": "order_year", "operator": "eq", "value": "2026", "values": []}],
    )
    merged, _ = apply_query_spec_patch(current, patch)
    assert merged.filters[0].entity_id == 37
    assert merged.filters[0].value == "旧商品名称"
    assert 37 in compile_query(merged).params


def test_model_schemas_hide_backend_entity_ids():
    assert "entity_id" not in query_spec_json_schema()["$defs"]["FilterSpec"]["properties"]
    assert "entity_id" not in query_spec_patch_json_schema()["$defs"]["FilterSpec"]["properties"]
    with pytest.raises(ValueError, match="实体 ID"):
        empty_patch(upsert_filters=[{
            "field": "product", "operator": "eq", "value": "任意商品", "values": [],
            "entity_id": 999,
        }])


def test_canonical_name_can_be_pinned_to_id():
    resolved, _ = resolve_query_spec(
        make_spec("极光键盘-标准版"), loader=lambda field: ("极光键盘-标准版",),
        alias_lookup=lambda field, value: [],
        name_lookup=lambda field, value: [EntityMatch(37, value)],
    )
    assert resolved.filters[0].entity_id == 37


def test_import_csv_deduplicates_without_exposing_values(tmp_path):
    file = tmp_path / "aliases.csv"
    file.write_text(
        "source_id,entity_type,entity_id,alias\n"
        "analytics_local,product,37,办公室键盘\n"
        "analytics_local,product,37,办公室键盘\n"
        "analytics_local,product,42,办公室键盘\n",
        encoding="utf-8",
    )
    records = read_aliases(file)
    assert len(records) == 2
    assert {record[3] for record in records} == {37, 42}
