import json
from datetime import date
from pathlib import Path
from openai import OpenAI
from .config import settings
from .query_spec import QuerySpec, compact_catalog, query_spec_json_schema
from .catalog_retrieval import CatalogRetriever
from .value_resolver import infer_entity_dimensions

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
DICTIONARY = json.loads((ROOT / "business_dictionary.json").read_text(encoding="utf-8"))

INSTRUCTIONS = """你是 PostgreSQL 数据分析助手。
只能输出一条只读 SELECT 或 WITH 查询，不要输出 Markdown、解释或注释。
严格使用给定 Schema 和业务字典；应用指标的默认过滤条件；不确定时不要猜不存在的字段。
查询最多返回 200 行。"""

SPEC_INSTRUCTIONS = """你把中文数据问题转换为 JSON 查询计划。
只能选择目录中存在的 id，不得输出 SQL、表名或列名。
filters 的 value 用于单值；values 用于 in/between，其余情况填空数组。
用户指定某个实体名称时使用 eq，即使名称可能不完整；只有用户明确要求“包含/关键词”时才使用 contains。
“15号商品/15号客户”这类编号紧邻实体词的表达属于实体名称；只有出现月、日、日期等时间语义时才作为日期。
仅出现“YYYY年”时使用 year（或 order_year 的 eq），不要使用 calendar_month；“件”是数量单位，不是月份。
包含学校、学院、公司、客户等完整机构名称时优先作为 customer，即使名称中带有“地区”。
“趋势、逐月、每月、逐季度”需要按对应时间维度升序排列。
时间规则：今年/去年、本月/上月、本季度/上季度使用对应 this_/last_ 运算符；最近N天或N个月使用 last_n_days/last_n_months 且 value 填 N；某月用 calendar_month（YYYY-MM）；某季度用 calendar_quarter（YYYY-Qn）；连续月份用 month_range 并在 values 填起止 YYYY-MM。绝对日期写 YYYY-MM-DD。
用户要求同比时 comparison.type 为 year_over_year；要求环比时为 period_over_period；否则 comparison 为 null。对比时仍必须输出本期时间过滤，例如“去年销售额同比”使用 last_year + year_over_year，“2025年第一季度销售额环比”使用 calendar_quarter(2025-Q1) + period_over_period。当前对比查询只能选择一个指标、不能选择分组维度或排序。
若用户未要求限制行数，limit 填 null。
输出必须严格符合 JSON Schema。"""


def build_prompt_catalog(
    question: str,
    use_retrieval: bool | None = None,
    retriever: CatalogRetriever | None = None,
) -> dict:
    full = compact_catalog()
    enabled = settings.catalog_retrieval_enabled if use_retrieval is None else use_retrieval
    if not enabled:
        return full
    retriever = retriever or CatalogRetriever.from_jsonl()
    results = retriever.search(
        question,
        limit=settings.catalog_retrieval_top_k,
        document_types={"metric", "dimension"},
    )
    metric_ids = {
        item.document.metadata.get("semantic_id") for item in results
        if item.document.document_type == "metric"
    }
    dimension_ids = {
        item.document.metadata.get("semantic_id") for item in results
        if item.document.document_type == "dimension"
    }
    dimension_ids.update(infer_entity_dimensions(question))
    metric_ids &= set(full["metrics"])
    dimension_ids &= set(full["dimensions"])
    if not metric_ids:
        return full
    return {
        "metrics": {key: full["metrics"][key] for key in sorted(metric_ids)},
        "dimensions": {key: full["dimensions"][key] for key in sorted(dimension_ids)},
        "rules": full["rules"],
    }


def generate_sql(question: str) -> str:
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    prompt = (
        f"用户问题：{question}\n\n"
        f"PostgreSQL Schema：\n{SCHEMA}\n\n"
        f"业务字典：\n{json.dumps(DICTIONARY, ensure_ascii=False, indent=2)}"
    )
    client = OpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
    )
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("DashScope 没有返回 SQL")
    return content.strip()


def generate_query_spec(question: str, use_retrieval: bool | None = None) -> QuerySpec:
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    client = OpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
    )
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SPEC_INSTRUCTIONS},
            {
                "role": "user",
                "content": (
                    f"日期基准：{date.today().isoformat()}\n问题：{question}\n"
                    f"业务目录：{json.dumps(build_prompt_catalog(question, use_retrieval=use_retrieval), ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "query_spec",
                "strict": True,
                "schema": query_spec_json_schema(),
            },
        },
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("DashScope 没有返回查询计划")
    return QuerySpec.model_validate_json(content)
