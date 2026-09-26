from types import SimpleNamespace

import pytest

from scripts import build_scale_db


def test_scale_fixture_rejects_non_isolated_database_name():
    with pytest.raises(ValueError, match="nl2sql_scale_"):
        build_scale_db.database_url("analytics")


def test_scale_fixture_refuses_existing_database_before_writes(monkeypatch):
    calls = []

    class ExistingDatabase:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement, params=None):
            calls.append(str(statement))
            return SimpleNamespace(fetchone=lambda: (1,))

    monkeypatch.setattr(build_scale_db, "settings", SimpleNamespace(
        database_url="postgresql://user:password@localhost/analytics",
    ))
    monkeypatch.setattr(build_scale_db.psycopg, "connect", lambda *_, **__: ExistingDatabase())
    with pytest.raises(ValueError, match="已存在"):
        build_scale_db.build("nl2sql_scale_test", 10_000)
    assert len(calls) == 1
    assert calls[0].startswith("SELECT")
