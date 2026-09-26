from unittest.mock import patch

from scripts.start_local import preflight


def test_preflight_ready_does_not_expose_connection_string():
    with patch("scripts.start_local.check_database", return_value={"ok": True, "database": "analytics"}), \
         patch("scripts.start_local.settings") as settings, \
         patch("scripts.start_local.SQLiteConversationStore"):
        settings.dashscope_api_key = "secret"
        settings.session_storage = "sqlite"
        ready, storage_ready, messages = preflight()
    assert ready
    assert storage_ready
    assert any("analytics" in message for message in messages)
    assert all("secret" not in message for message in messages)


def test_preflight_reports_missing_services_without_error_details():
    with patch("scripts.start_local.check_database", return_value={"ok": False, "error": "password=secret"}), \
         patch("scripts.start_local.settings") as settings, \
         patch("scripts.start_local.SQLiteConversationStore"):
        settings.dashscope_api_key = ""
        settings.session_storage = "sqlite"
        ready, storage_ready, messages = preflight()
    assert not ready
    assert storage_ready
    assert any("未连接" in message for message in messages)
    assert all("secret" not in message for message in messages)


def test_preflight_detects_unwritable_session_storage():
    with patch("scripts.start_local.check_database", return_value={"ok": True, "database": "analytics"}), \
         patch("scripts.start_local.settings") as settings, \
         patch("scripts.start_local.SQLiteConversationStore", side_effect=OSError("private path")):
        settings.dashscope_api_key = "secret"
        settings.session_storage = "sqlite"
        ready, storage_ready, messages = preflight()
    assert ready
    assert not storage_ready
    assert any("不可用" in message for message in messages)
    assert all("private path" not in message for message in messages)
