from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceInfo(StrictModel):
    id: str
    name: str
    dialect: Literal["postgresql", "mysql", "sqlserver", "oracle", "sqlite"]
    database: str


class ColumnMeta(StrictModel):
    id: str
    name: str
    ordinal_position: int
    native_type: str
    canonical_type: Literal["string", "integer", "decimal", "boolean", "date", "datetime", "json", "binary", "other"]
    nullable: bool
    default: str | None
    description: str | None
    business_names: list[str] = Field(default_factory=list)
    sensitivity: Literal["normal", "internal", "sensitive", "restricted"] = "normal"
    sample_values: list[str] = Field(default_factory=list)


class ForeignKeyMeta(StrictModel):
    id: str
    name: str
    columns: list[str]
    referenced_schema: str
    referenced_table: str
    referenced_columns: list[str]


class IndexMeta(StrictModel):
    name: str
    definition: str


class TableMeta(StrictModel):
    id: str
    schema_name: str
    name: str
    description: str | None
    business_names: list[str] = Field(default_factory=list)
    estimated_rows: int | None
    columns: list[ColumnMeta]
    primary_key: list[str]
    foreign_keys: list[ForeignKeyMeta]
    indexes: list[IndexMeta]


class UnifiedCatalog(StrictModel):
    catalog_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    source: SourceInfo
    tables: list[TableMeta]


class RagDocument(StrictModel):
    id: str
    document_type: Literal["table", "column", "relationship", "metric", "dimension", "value_dictionary"]
    source_id: str
    title: str
    text: str
    metadata: dict[str, Any]


class TableOverride(StrictModel):
    description: str | None = None
    business_names: list[str] = Field(default_factory=list)


class ColumnOverride(StrictModel):
    description: str | None = None
    business_names: list[str] = Field(default_factory=list)
    sensitivity: Literal["normal", "internal", "sensitive", "restricted"] = "normal"


class CatalogOverrides(StrictModel):
    override_version: Literal["1.0"] = "1.0"
    tables: dict[str, TableOverride] = Field(default_factory=dict)
    columns: dict[str, ColumnOverride] = Field(default_factory=dict)
