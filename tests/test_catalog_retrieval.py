from app.catalog_models import RagDocument
import pytest

from app.catalog_retrieval import CatalogRetriever, DEFAULT_DOCUMENTS_PATH, _tokens
from app.llm import build_prompt_catalog
from app.query_spec import compact_catalog


def document(identifier, kind, title, text, metadata=None):
    return RagDocument(
        id=identifier, document_type=kind, source_id="demo",
        title=title, text=text, metadata=metadata or {},
    )


def retriever():
    return CatalogRetriever([
        document(
            "metric:sales", "metric", "metric 销售额",
            "销售额。同义词：销售金额，成交额，GMV。涉及表：orders, order_items。",
            {"semantic_id": "sales_amount", "aliases": ["销售额", "销售金额", "成交额", "GMV"]},
        ),
        document(
            "metric:customers", "metric", "metric 客户数",
            "客户数。同义词：购买客户数，用户数。涉及表：orders。",
            {"semantic_id": "customer_count", "aliases": ["客户数", "购买客户数", "用户数"]},
        ),
        document(
            "dimension:payment", "dimension", "dimension 支付方式",
            "支付方式。同义词：付款方式。涉及表：payments。",
            {"semantic_id": "payment_method", "aliases": ["支付方式", "付款方式"]},
        ),
        document(
            "column:region", "column", "字段 public.customers.region",
            "客户所在地区。业务名称：地区，客户地区，区域。",
            {"business_names": ["地区", "客户地区", "区域"]},
        ),
    ])


def test_chinese_text_is_split_into_overlapping_terms():
    tokens = _tokens("去年各地区成交额")
    assert "地区" in tokens
    assert "成交" in tokens


def test_business_alias_retrieves_metric_first():
    results = retriever().search("去年成交额最高的地区", document_types={"metric"})
    assert results[0].document.metadata["semantic_id"] == "sales_amount"
    assert results[0].alias_boost > 0


def test_synonym_retrieves_customer_count():
    results = retriever().search("有多少购买客户", document_types={"metric"})
    assert results[0].document.metadata["semantic_id"] == "customer_count"


def test_document_type_filter_excludes_columns():
    results = retriever().search("地区", document_types={"dimension"})
    assert all(item.document.document_type == "dimension" for item in results)


def test_source_filter_can_exclude_all_documents():
    assert retriever().search("销售额", source_id="other") == []


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ("去年成交额最高的地区", {"sales_amount", "region"}),
        ("按付款渠道统计购买客户数", {"payment_method", "customer_count"}),
        ("每季度售出件数趋势", {"order_quarter", "sold_quantity"}),
        ("各品类平均成交单价", {"category", "avg_unit_price"}),
        ("退款订单的最高订单金额", {"order_status", "max_order_amount"}),
        ("不同区域平均客单价", {"region", "avg_order_amount"}),
        ("每年使用支付宝的支付次数", {"order_year", "payment_method", "payment_count"}),
    ],
)
def test_generated_catalog_recall_on_nontrivial_business_queries(query, expected_ids):
    results = CatalogRetriever.from_jsonl(DEFAULT_DOCUMENTS_PATH).search(
        query, limit=8, document_types={"metric", "dimension"}
    )
    retrieved_ids = {item.document.metadata.get("semantic_id") for item in results}
    assert expected_ids <= retrieved_ids


def test_prompt_catalog_can_fall_back_to_complete_dictionary():
    assert build_prompt_catalog("任意问题", use_retrieval=False) == compact_catalog()


def test_prompt_catalog_reduces_candidates_when_retrieval_is_enabled():
    subset = build_prompt_catalog(
        "去年成交额最高的地区",
        use_retrieval=True,
        retriever=CatalogRetriever.from_jsonl(DEFAULT_DOCUMENTS_PATH),
    )
    assert "sales_amount" in subset["metrics"]
    assert "region" in subset["dimensions"]
    assert len(subset["metrics"]) < len(compact_catalog()["metrics"])
