import json

import pytest

from app.source_registry import SourceDefinition, SourceRegistry, SourceRegistryFile


def source(**changes):
    values = {
        "id": "analytics_local",
        "name": "演示库",
        "dialect": "postgresql",
        "connection_env": "DATABASE_URL",
        "catalog_dir": "catalog/generated",
        "overrides_file": "catalog/overrides.json",
        "business_dictionary_file": "business_dictionary.json",
    }
    values.update(changes)
    return SourceDefinition.model_validate(values)


def test_registry_loads_versioned_file(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({
        "registry_version": "1.0",
        "sources": [source().model_dump()],
    }), encoding="utf-8")

    registry = SourceRegistry.load(path)

    assert registry.get("analytics_local").dialect == "postgresql"


def test_duplicate_source_ids_are_rejected():
    definition = SourceRegistryFile(sources=[source(), source(name="另一个库")])

    with pytest.raises(ValueError, match="不能重复"):
        SourceRegistry(definition)


def test_connection_url_comes_from_environment(monkeypatch):
    registry = SourceRegistry(SourceRegistryFile(sources=[source()]))
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/demo")

    assert registry.connection_url(source()) == "postgresql://example.invalid/demo"


def test_missing_connection_environment_is_rejected(monkeypatch):
    registry = SourceRegistry(SourceRegistryFile(sources=[source()]))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        registry.connection_url(source())


def test_catalog_directory_cannot_escape_project_catalog():
    registry = SourceRegistry(SourceRegistryFile(sources=[source(catalog_dir="../outside")]))

    with pytest.raises(ValueError, match="路径必须位于"):
        registry.catalog_path(source(catalog_dir="../outside"))


def test_public_status_never_contains_connection_url(monkeypatch):
    registry = SourceRegistry(SourceRegistryFile(sources=[source()]))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@example.invalid/demo")

    status = registry.public_status(source())

    assert status["connection_configured"] is True
    assert "connection_url" not in status
    assert "secret" not in json.dumps(status)
