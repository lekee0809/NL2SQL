from datetime import datetime, timedelta, timezone

import pytest

from app.conversation import SessionConflictError
from app.query_spec import QuerySpec
from app.session_storage import SQLiteConversationStore


def sample_spec() -> QuerySpec:
    return QuerySpec.model_validate({
        "metrics": ["sales_amount"], "dimensions": [],
        "filters": [{"field": "region", "operator": "eq", "value": "华东", "values": []}],
        "order_by": [], "limit": None, "comparison": None,
    })


def test_sqlite_session_survives_reopening_and_preserves_clarification(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    first = SQLiteConversationStore(path)
    pending = {"filter_index": 0, "candidates": [{"value": "华东"}]}
    created = first.create(sample_spec(), "首轮问题", pending)
    assert path.exists()

    reopened = SQLiteConversationStore(path)
    restored = reopened.get(created.session_id)
    assert restored.query_spec.filters[0].value == "华东"
    assert restored.pending_clarification == pending
    assert restored.first_message == "首轮问题"
    restored.query_spec.filters[0].value = "外部修改"
    assert reopened.get(created.session_id).query_spec.filters[0].value == "华东"

    updated = reopened.update(created.session_id, sample_spec(), "继续查询")
    assert updated.turn_count == 2
    assert updated.first_message == "首轮问题"
    assert updated.pending_clarification is None
    assert first.get(created.session_id).turn_count == 2
    assert first.delete(created.session_id)
    with pytest.raises(KeyError, match="已过期"):
        reopened.get(created.session_id)


def test_sqlite_session_expiry_survives_reopening(tmp_path):
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    clock = [now]
    path = tmp_path / "sessions.sqlite3"
    first = SQLiteConversationStore(path, ttl_seconds=60, clock=lambda: clock[0])
    created = first.create(sample_spec(), "首轮问题")
    clock[0] = now + timedelta(seconds=59)
    assert SQLiteConversationStore(path, ttl_seconds=60, clock=lambda: clock[0]).get(created.session_id)
    clock[0] = now + timedelta(seconds=60)
    reopened = SQLiteConversationStore(path, ttl_seconds=60, clock=lambda: clock[0])
    with pytest.raises(KeyError, match="已过期"):
        reopened.get(created.session_id)
    assert reopened.purge_expired() == 0


def test_sqlite_session_failed_update_does_not_create_state(tmp_path):
    store = SQLiteConversationStore(tmp_path / "sessions.sqlite3")
    with pytest.raises(KeyError):
        store.update("missing", sample_spec(), "未知会话")
    assert not store.delete("missing")


def test_sqlite_session_list_is_recent_and_excludes_expired(tmp_path):
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    clock = [now]
    store = SQLiteConversationStore(tmp_path / "sessions.sqlite3", ttl_seconds=60,
                                    clock=lambda: clock[0])
    older = store.create(sample_spec(), "最早")
    clock[0] += timedelta(seconds=10)
    newer = store.create(sample_spec(), "最新")
    assert [item.session_id for item in store.list_recent(1)] == [newer.session_id]
    assert [item.session_id for item in store.list_recent()] == [newer.session_id, older.session_id]
    clock[0] += timedelta(seconds=50)
    reopened = SQLiteConversationStore(store.path, ttl_seconds=60, clock=lambda: clock[0])
    assert [item.session_id for item in reopened.list_recent()] == [newer.session_id]


def test_sqlite_session_rejects_stale_update_across_instances(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    first = SQLiteConversationStore(path)
    second = SQLiteConversationStore(path)
    state = first.create(sample_spec(), "第一题")
    first.update(state.session_id, sample_spec(), "第二题", expected_turn_count=1)
    with pytest.raises(SessionConflictError):
        second.update(state.session_id, sample_spec(), "旧页面续问", expected_turn_count=1)
    assert second.get(state.session_id).last_message == "第二题"


def test_sqlite_session_caps_saved_sessions(tmp_path):
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    clock = [now]
    path = tmp_path / "sessions.sqlite3"
    store = SQLiteConversationStore(path, max_sessions=2, clock=lambda: clock[0])
    first = store.create(sample_spec(), "第一题")
    clock[0] += timedelta(seconds=1)
    store.create(sample_spec(), "第二题")
    clock[0] += timedelta(seconds=1)
    store.create(sample_spec(), "第三题")
    assert len(SQLiteConversationStore(path, max_sessions=2, clock=lambda: clock[0]).list_recent()) == 2
    with pytest.raises(KeyError):
        store.get(first.session_id)
