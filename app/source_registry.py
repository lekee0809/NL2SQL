import json
import os
import re
from pathlib import Path
from typing import Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .catalog_builder import (
    apply_catalog_overrides,
    build_rag_documents,
    introspect_postgres,
    write_catalog_artifacts,
)
from .catalog_models import CatalogOverrides


ROOT = Path(__file__).resolve().parent.parent
CATALOG_ROOT = ROOT / "catalog"
REGISTRY_PATH = CATALOG_ROOT / "sources.json"


class SourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    dialect: Literal["postgresql"]
    connection_env: str
    enabled: bool = True
    catalog_dir: str
    overrides_file: str | None = None
    business_dictionary_file: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", value):
            raise ValueError("数据源 ID 只能包含小写字母、数字、下划线和短横线")
        return value

    @field_validator("connection_env")
    @classmethod
    def validate_env_name(cls, value: str):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", value):
            raise ValueError("连接环境变量名称格式无效")
        return value


class SourceRegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registry_version: Literal["1.0"] = "1.0"
    sources: list[SourceDefinition] = Field(default_factory=list)


def _safe_project_path(relative_path: str, allowed_root: Path) -> Path:
    candidate = (ROOT / relative_path).resolve()
    root = allowed_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"路径必须位于 {root}")
    return candidate


class SourceRegistry:
    def __init__(self, definition: SourceRegistryFile):
        ids = [source.id for source in definition.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("数据源 ID 不能重复")
        self.definition = definition
        self.sources = {source.id: source for source in definition.sources}

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH):
        return cls(SourceRegistryFile.model_validate_json(path.read_text(encoding="utf-8")))

    def get(self, source_id: str) -> SourceDefinition:
        source = self.sources.get(source_id)
        if source is None:
            raise KeyError(f"数据源不存在：{source_id}")
        return source

    def connection_url(self, source: SourceDefinition) -> str:
        value = os.getenv(source.connection_env, "")
        if not value:
            raise RuntimeError(f"环境变量 {source.connection_env} 未配置")
        return value

    def catalog_path(self, source: SourceDefinition) -> Path:
        return _safe_project_path(source.catalog_dir, CATALOG_ROOT)

    def optional_project_file(self, relative_path: str | None) -> Path | None:
        if not relative_path:
            return None
        return _safe_project_path(relative_path, ROOT)

    def public_status(self, source: SourceDefinition) -> dict:
        output_dir = self.catalog_path(source)
        catalog_path = output_dir / "catalog.json"
        status = {
            "id": source.id,
            "name": source.name,
            "dialect": source.dialect,
            "enabled": source.enabled,
            "connection_configured": bool(os.getenv(source.connection_env)),
            "catalog_ready": catalog_path.exists(),
            "catalog_dir": source.catalog_dir,
        }
        if catalog_path.exists():
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            documents_path = output_dir / "rag_documents.jsonl"
            status.update({
                "generated_at": catalog.get("generated_at"),
                "database": catalog.get("source", {}).get("database"),
                "table_count": len(catalog.get("tables", [])),
                "document_count": len(documents_path.read_text(encoding="utf-8").splitlines())
                if documents_path.exists() else 0,
            })
        return status

    def test_connection(self, source: SourceDefinition) -> dict:
        if not source.enabled:
            raise RuntimeError("数据源已停用")
        with psycopg.connect(self.connection_url(source), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), version()")
                database, version = cur.fetchone()
        return {"ok": True, "database": database, "version": version}

    def scan(
        self,
        source: SourceDefinition,
        include_value_samples: bool = False,
        max_sample_values: int = 20,
    ) -> dict:
        if not source.enabled:
            raise RuntimeError("数据源已停用")
        catalog = introspect_postgres(
            self.connection_url(source), source_id=source.id, source_name=source.name,
            include_value_samples=include_value_samples, max_sample_values=max_sample_values,
        )
        overrides_path = self.optional_project_file(source.overrides_file)
        if overrides_path and overrides_path.exists():
            overrides = CatalogOverrides.model_validate_json(overrides_path.read_text(encoding="utf-8"))
            catalog = apply_catalog_overrides(catalog, overrides)
        dictionary = None
        dictionary_path = self.optional_project_file(source.business_dictionary_file)
        if dictionary_path and dictionary_path.exists():
            dictionary = json.loads(dictionary_path.read_text(encoding="utf-8"))
        documents = build_rag_documents(catalog, dictionary)
        write_catalog_artifacts(catalog, documents, self.catalog_path(source))
        return {
            "ok": True,
            "source_id": source.id,
            "tables": len(catalog.tables),
            "documents": len(documents),
            "included_value_samples": include_value_samples,
        }
