import pytest
from fastapi import HTTPException

from app import main
from app.conversation import ConversationStore, QuerySpecPatch
from app.session_storage import SQLiteConversationStore
from app.query_spec import QuerySpec
from app.value_resolver import NeedsClarification


def initial_spec() -> QuerySpec:
    return QuerySpec.model_validate({
        "metrics": ["sales_amount"],
        "dimensions": [],
        "filters": [
            {"field": "order_year", "operator": "eq", "value": "2025", "values": []},
            {"field": "region", "operator": "eq", "value": "华东", "values": []},
        ],
        "order_by": [],
        "limit": None,
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


def fake_result(spec: QuerySpec, question: str) -> dict:
    return {
        "question": question,
        "query_spec": spec.model_dump(),
        "resolutions": [],
        "sql": "SELECT 1",
        "parameters": [],
        "rows": [{"value": 1}],
        "count": 1,
        "pipeline": "test",
    }


def test_session_api_creates_then_updates_only_mentioned_filter(monkeypatch):
    monkeypatch.setattr(main, "conversation_store", ConversationStore())
    monkeypatch.setattr(main, "generate_query_spec", lambda _, **kwargs: initial_spec())
    monkeypatch.setattr(main, "execute_query_spec", fake_result)

    created = main.create_session(main.QueryRequest(question="2025年华东销售额"))
    monkeypatch.setattr(
        main,
        "generate_query_spec_patch",
        lambda message, current: (_ for _ in ()).throw(AssertionError("model should not run")),
    )
    continued = main.continue_session(
        created["session_id"], main.QueryRequest(question="改成华南")
    )

    assert continued["turn_count"] == 2
    assert continued["patch_source"] == "local"
    assert continued["changes"] == ["已更新过滤条件：region"]
    filters = continued["query_spec"]["filters"]
    assert {(item["field"], item["value"]) for item in filters} == {
        ("order_year", "2025"), ("region", "华南")
    }


def test_session_api_continues_after_storage_reopen(monkeypatch, tmp_path):
    path = tmp_path / "sessions.sqlite3"
    monkeypatch.setattr(main, "conversation_store", SQLiteConversationStore(path))
    monkeypatch.setattr(main, "generate_query_spec", lambda _, **kwargs: initial_spec())
    monkeypatch.setattr(main, "execute_query_spec", fake_result)
    created = main.create_session(main.QueryRequest(question="2025年华东销售额"))

    monkeypatch.setattr(main, "conversation_store", SQLiteConversationStore(path))
    continued = main.continue_session(
        created["session_id"], main.QueryRequest(question="改成华南")
    )
    assert continued["turn_count"] == 2
    assert continued["patch_source"] == "local"
    assert any(item["field"] == "region" and item["value"] == "华南"
               for item in continued["query_spec"]["filters"])


def test_session_list_returns_only_summaries(monkeypatch, tmp_path):
    store = SQLiteConversationStore(tmp_path / "sessions.sqlite3")
    monkeypatch.setattr(main, "conversation_store", store)
    created = store.create(initial_spec(), "2025年华东销售额")
    store.update(created.session_id, initial_spec(), "换成华南")
    listed = main.list_sessions()
    assert len(listed["sessions"]) == 1
    summary = listed["sessions"][0]
    assert summary["title"] == "2025年华东销售额"
    assert summary["turn_count"] == 2
    assert "query_spec" not in summary
    assert "last_message" not in summary
    with pytest.raises(HTTPException) as error:
        main.list_sessions(51)
    assert error.value.status_code == 422


def test_stale_follow_up_returns_conflict_without_overwriting(monkeypatch, tmp_path):
    store = SQLiteConversationStore(tmp_path / "sessions.sqlite3")
    monkeypatch.setattr(main, "conversation_store", store)
    state = store.create(initial_spec(), "首轮")

    def concurrent_result(spec, question):
        store.update(state.session_id, initial_spec(), "另一标签页已更新", expected_turn_count=1)
        return fake_result(spec, question)

    monkeypatch.setattr(main, "execute_query_spec", concurrent_result)
    with pytest.raises(HTTPException) as error:
        main.continue_session(state.session_id, main.QueryRequest(question="改成华南"))
    assert error.value.status_code == 409
    assert store.get(state.session_id).last_message == "另一标签页已更新"


def test_invalid_follow_up_does_not_overwrite_last_valid_state(monkeypatch):
    store = ConversationStore()
    monkeypatch.setattr(main, "conversation_store", store)
    monkeypatch.setattr(main, "generate_query_spec", lambda _, **kwargs: initial_spec())
    monkeypatch.setattr(main, "execute_query_spec", fake_result)
    created = main.create_session(main.QueryRequest(question="2025年华东销售额"))
    monkeypatch.setattr(
        main,
        "generate_query_spec_patch",
        lambda message, current, **kwargs: patch(
            set_metrics=[], set_dimensions=[], set_order_by=[]
        ),
    )

    with pytest.raises(HTTPException) as captured:
        main.continue_session(created["session_id"], main.QueryRequest(question="清空全部"))
    assert captured.value.status_code == 400
    state = store.get(created["session_id"])
    assert state.turn_count == 1
    assert state.query_spec.metrics == ["sales_amount"]


def test_clarification_resolution_can_continue_into_next_turn(monkeypatch):
    ambiguous = QuerySpec.model_validate({
        "metrics": ["sold_quantity"],
        "dimensions": [],
        "filters": [{
            "field": "product", "operator": "eq", "value": "极光键盘", "values": []
        }],
        "order_by": [],
        "limit": None,
        "comparison": None,
    })
    store = ConversationStore()
    monkeypatch.setattr(main, "conversation_store", store)
    monkeypatch.setattr(main, "generate_query_spec", lambda _, **kwargs: ambiguous)

    def ask_for_clarification(spec, question):
        raise NeedsClarification(
            0,
            "product",
            "极光键盘",
            [
                {"value": "极光键盘-标准版", "score": 0.9},
                {"value": "极光键盘-专业版", "score": 0.88},
            ],
            spec,
        )

    monkeypatch.setattr(main, "execute_query_spec", ask_for_clarification)
    with pytest.raises(HTTPException) as captured:
        main.create_session(main.QueryRequest(question="极光键盘销量"))
    assert captured.value.status_code == 409
    session_id = captured.value.detail["session_id"]
    assert store.get(session_id).pending_clarification is not None

    monkeypatch.setattr(main, "execute_query_spec", fake_result)
    resolved = main.resolve_session(
        session_id, main.SessionResolveRequest(value="极光键盘-专业版")
    )
    assert resolved["turn_count"] == 2
    assert resolved["patch_source"] == "clarification"
    assert store.get(session_id).pending_clarification is None

    monkeypatch.setattr(
        main,
        "generate_query_spec_patch",
        lambda *_: (_ for _ in ()).throw(AssertionError("model should not run")),
    )
    continued = main.continue_session(
        session_id, main.QueryRequest(question="只看前五个")
    )
    assert continued["turn_count"] == 3
    assert continued["patch_source"] == "local"
    assert continued["query_spec"]["limit"] == 5


def test_alias_candidate_id_is_pinned_across_follow_ups(monkeypatch):
    ambiguous = QuerySpec.model_validate({
        "metrics": ["sales_amount"], "dimensions": [],
        "filters": [{"field": "product", "operator": "eq", "value": "办公室键盘", "values": []}],
        "order_by": [], "limit": None, "comparison": None,
    })
    store = ConversationStore()
    monkeypatch.setattr(main, "conversation_store", store)
    monkeypatch.setattr(main, "generate_query_spec", lambda *_args, **_kwargs: ambiguous)

    def ambiguous_result(spec, question):
        raise NeedsClarification(0, "product", "办公室键盘", [
            {"value": "标准版", "entity_id": 37, "score": 1.0},
            {"value": "专业版", "entity_id": 42, "score": 1.0},
        ], spec)

    monkeypatch.setattr(main, "execute_query_spec", ambiguous_result)
    with pytest.raises(HTTPException) as captured:
        main.create_session(main.QueryRequest(question="办公室键盘销售额"))
    session_id = captured.value.detail["session_id"]
    with pytest.raises(HTTPException, match="请选择当前会话"):
        main.resolve_session(session_id, main.SessionResolveRequest(value="专业版", entity_id=999))

    monkeypatch.setattr(main, "execute_query_spec", fake_result)
    resolved = main.resolve_session(session_id, main.SessionResolveRequest(value="专业版", entity_id=42))
    assert resolved["query_spec"]["filters"][0]["entity_id"] == 42
    continued = main.continue_session(session_id, main.QueryRequest(question="只看前五个"))
    assert continued["query_spec"]["filters"][0]["entity_id"] == 42
