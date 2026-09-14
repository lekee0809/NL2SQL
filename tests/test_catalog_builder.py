from datetime import datetime, timezone

import pytest

from app.catalog_builder import apply_catalog_overrides, build_rag_documents, canonical_type
from app.catalog_models import (
    CatalogOverrides,
    ColumnMeta,
    ForeignKeyMeta,
    SourceInfo,
    TableMeta,
    UnifiedCatalog,
)


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        ("text", "string"),
        ("character varying", "string"),
        ("bigint", "integer"),
        ("numeric", "decimal"),
        ("boolean", "boolean"),
        ("date", "date"),
        ("timestamp without time zone", "datetime"),
        ("jsonb", "json"),
        ("bytea", "binary"),
        ("USER-DEFINED", "other"),
    ],
)
def test_postgres_types_are_normalized(native, expected):
    assert canonical_type(native) == expected


def sample_catalog(include_values=True):
    return UnifiedCatalog(
        generated_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        source=SourceInfo(id="demo", name="演示库", dialect="postgresql", database="analytics"),
        tables=[TableMeta(
            id="demo.public.orders",
            schema_name="public",
            name="orders",
            description="订单表",
            estimated_rows=2000,
            columns=[
                ColumnMeta(
                    id="demo.public.orders.id", name="id", ordinal_position=1,
                    native_type="integer", canonical_type="integer", nullable=False,
                    default=None, description="订单编号",
                ),
                ColumnMeta(
                    id="demo.public.orders.status", name="status", ordinal_position=2,
                    native_type="text", canonical_type="string", nullable=False,
                    default=None, description="订单状态",
                    sample_values=["PAID", "REFUNDED"] if include_values else [],
                ),
            ],
            primary_key=["id"],
            foreign_keys=[ForeignKeyMeta(
                id="demo.public.orders.fk.customer", name="orders_customer_fk",
                columns=["customer_id"], referenced_schema="public",
                referenced_table="customers", referenced_columns=["id"],
            )],
            indexes=[],
        )],
    )


def test_rag_documents_are_typed_and_have_stable_ids():
    dictionary = {
        "metrics": {"order_count": {"label": "订单数", "synonyms": ["订单量"], "tables": ["orders"]}},
        "dimensions": {"order_status": {"label": "订单状态", "synonyms": ["状态"], "tables": ["orders"]}},
    }
    documents = build_rag_documents(sample_catalog(), dictionary)
    types = {document.document_type for document in documents}
    assert types == {"table", "column", "relationship", "metric", "dimension", "value_dictionary"}
    assert len({document.id for document in documents}) == len(documents)
    metric = next(document for document in documents if document.document_type == "metric")
    assert metric.metadata["semantic_id"] == "order_count"


def test_value_documents_are_absent_without_explicit_sampling():
    documents = build_rag_documents(sample_catalog(include_values=False))
    assert all(document.document_type != "value_dictionary" for document in documents)


def test_catalog_json_schema_has_versioned_contract():
    schema = UnifiedCatalog.model_json_schema()
    assert schema["properties"]["catalog_version"]["const"] == "1.0"
    assert "tables" in schema["required"]


def test_semantic_overrides_enrich_catalog_and_remove_sensitive_samples():
    overrides = CatalogOverrides.model_validate({
        "override_version": "1.0",
        "tables": {
            "public.orders": {"description": "业务订单", "business_names": ["订单", "交易"]}
        },
        "columns": {
            "public.orders.status": {
                "description": "内部订单状态", "business_names": ["状态"],
                "sensitivity": "sensitive",
            }
        },
    })
    enriched = apply_catalog_overrides(sample_catalog(), overrides)
    assert enriched.tables[0].business_names == ["订单", "交易"]
    status = enriched.tables[0].columns[1]
    assert status.business_names == ["状态"]
    assert status.sample_values == []
    original_status = sample_catalog().tables[0].columns[1]
    assert original_status.sample_values == ["PAID", "REFUNDED"]


def test_unknown_override_target_is_rejected():
    overrides = CatalogOverrides.model_validate({
        "tables": {"public.missing": {"description": "不存在", "business_names": []}},
        "columns": {},
    })
    with pytest.raises(ValueError, match="未知表"):
        apply_catalog_overrides(sample_catalog(), overrides)


def test_rag_text_contains_business_names_and_sensitivity_metadata():
    overrides = CatalogOverrides.model_validate({
        "tables": {"public.orders": {"description": "订单表", "business_names": ["交易订单"]}},
        "columns": {
            "public.orders.status": {
                "description": "订单状态", "business_names": ["交易状态"], "sensitivity": "internal"
            }
        },
    })
    documents = build_rag_documents(apply_catalog_overrides(sample_catalog(), overrides))
    table_doc = next(item for item in documents if item.document_type == "table")
    status_doc = next(item for item in documents if item.id.endswith("status:column"))
    assert "交易订单" in table_doc.text
    assert "交易状态" in status_doc.text
    assert status_doc.metadata["sensitivity"] == "internal"
