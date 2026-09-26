import json
import hashlib
import logging
import time
from collections import OrderedDict
from datetime import date
from pathlib import Path
from collections.abc import Callable
from threading import RLock
from openai import OpenAI
from .config import settings
from .query_spec import QuerySpec, compact_catalog, query_spec_json_schema
from .catalog_retrieval import CatalogRetriever
from .value_resolver import infer_entity_dimensions
from .conversation import QuerySpecPatch, query_spec_patch_json_schema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
DICTIONARY = json.loads((ROOT / "business_dictionary.json").read_text(encoding="utf-8"))

INSTRUCTIONS = """你是 PostgreSQL 数据分析助手。
只能输出一条只读 SELECT 或 WITH 查询，不要输出 Markdown、解释或注释。
严格使用给定 Schema 和业务字典；应用指标的默认过滤条件；不确定时不要猜不存在的字段。
查询最多返回 200 行。"""

SPEC_INSTRUCTIONS = """把中文数据问题转换为严格符合 JSON Schema 的查询计划。
只使用业务目录中的 id；不得输出 SQL、表名、列名或解释。

解析优先级：
1. 先保留问题中的完整实体片段，再提取时间和指标；不要拆散商品、客户或机构名称。
2. 只有“年/月/季度/日期/最近”等明确时间词才能触发时间过滤。“号”紧邻商品或客户时属于实体名。
3. 用户给出实体名时用 eq 并原样保留 value；仅明确说“包含/关键词”时用 contains。
4. 学校、学院、公司、客户等完整机构名优先视为 customer，即使名称中含“地区”。
5. “华东/华北/华南/西南/西北”等明确地区名称必须使用 region eq；只有明确说“地区名包含”才用 contains。

过滤契约：单值放 value 且 values=[]；in/between/month_range 使用 values；相对时间除 last_n_days/last_n_months 的数字外 value 为空。仅“YYYY年”使用 order_year eq 或 year，绝不能生成月份。某月用 calendar_month(YYYY-MM)，某季度用 calendar_quarter(YYYY-Qn)，连续月份用 month_range。
“去年/今年/上个月/本月/上季度/本季度”必须使用 order_date 字段和对应的 last_year/this_year/last_month/this_month/last_quarter/this_quarter 操作符，value=""；绝不能写成 order_year eq "last_year" 等字符串值。

边界示例：
- “2025年15号测试商品销售额” = order_year 2025 + product eq “15号测试商品”，不是 2025-15。
- “测试客户20去年的订单数” = customer eq “测试客户20” + last_year。
- “包含华东的客户” = customer contains “华东”，不是 region eq “华东”。

“趋势/逐月/每月/逐季度”按相应时间维度升序。同比用 year_over_year，环比用 period_over_period，并保留本期时间过滤；对比查询只允许一个指标，不带维度和排序。未要求条数时 limit=null。输出前检查实体未被截断、时间格式合法、value/values 与 operator 匹配。"""

PATCH_INSTRUCTIONS = """根据当前 QuerySpec 和用户最新一句话生成增量修改，不要重建未提及的条件。
只输出符合 JSON Schema 的 QuerySpecPatch，不得输出 SQL 或解释。
set_* 为 null 表示保持原值；upsert_filters 按 field 替换同字段旧过滤；remove_filter_fields 只放用户明确要求删除的字段。
“改成/换成”替换对应字段；“再加/只看”追加或更新条件；“去掉/不限”删除；“重新查询/换个问题”才令 reset=true。
未提及的年份、实体、指标、维度、排序和限制必须保留。设置与清空 limit/comparison 不得同时发生。
时间过滤互斥：改为新的年份、月份、季度或相对时间时，必须把所有旧时间字段放入 remove_filter_fields，再通过 upsert_filters 添加新时间条件。
完整实体原样保留，“号”紧邻实体不是日期；同比/环比设置 comparison，并保留当前时间过滤。"""

_MODEL_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()
_MODEL_CACHE_LOCK = RLock()
_MODEL_CACHE_TTL_SECONDS = 600
_MODEL_CACHE_MAX_ENTRIES = 256
_MODEL_LOGGER = logging.getLogger("uvicorn.error")


def clear_model_cache() -> None:
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


def _cache_key(messages: list[dict], response_format: dict) -> str:
    payload = {
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "account": hashlib.sha256(settings.dashscope_api_key.encode()).hexdigest(),
        "messages": messages,
        "response_format": response_format,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _cached_text(
    messages: list[dict],
    response_format: dict,
    validator: Callable[[str], object],
    usage_callback: Callable[[dict[str, int]], None] | None,
    metadata_callback: Callable[[dict], None] | None,
) -> object:
    key = _cache_key(messages, response_format)
    started = time.perf_counter()
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < _MODEL_CACHE_TTL_SECONDS:
            _MODEL_CACHE.move_to_end(key)
        else:
            cached = None
            _MODEL_CACHE.pop(key, None)
    if cached is not None:
        result = validator(cached[1])
        tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        if usage_callback is not None:
            usage_callback(tokens)
        if metadata_callback is not None:
            metadata_callback({"model": settings.llm_model, "cache_hit": True,
                               "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                               "tokens": tokens})
        _MODEL_LOGGER.info("nl2sql model=%s cache_hit=true input_tokens=0 output_tokens=0 latency_ms=%.1f",
                           settings.llm_model, (time.perf_counter() - started) * 1000)
        return result

    client = OpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
    )
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        response_format=response_format,
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("DashScope 没有返回查询计划")
    result = validator(content)
    tokens = _usage_dict(response)
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE[key] = (time.monotonic(), content)
        _MODEL_CACHE.move_to_end(key)
        while len(_MODEL_CACHE) > _MODEL_CACHE_MAX_ENTRIES:
            _MODEL_CACHE.popitem(last=False)
    if usage_callback is not None:
        usage_callback(tokens)
    if metadata_callback is not None:
        metadata_callback({"model": settings.llm_model, "cache_hit": False,
                           "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                           "tokens": tokens})
    _MODEL_LOGGER.info("nl2sql model=%s cache_hit=false input_tokens=%d output_tokens=%d latency_ms=%.1f",
                       settings.llm_model, tokens["input_tokens"], tokens["output_tokens"],
                       (time.perf_counter() - started) * 1000)
    return result


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


def _usage_dict(response) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def generate_query_spec(
    question: str,
    use_retrieval: bool | None = None,
    usage_callback: Callable[[dict[str, int]], None] | None = None,
    metadata_callback: Callable[[dict], None] | None = None,
) -> QuerySpec:
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    messages = [
            {"role": "system", "content": SPEC_INSTRUCTIONS},
            {
                "role": "user",
                "content": (
                    f"日期基准：{date.today().isoformat()}\n问题：{question}\n"
                    f"业务目录：{json.dumps(build_prompt_catalog(question, use_retrieval=use_retrieval), ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
        ]
    response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "query_spec",
                "strict": True,
                "schema": query_spec_json_schema(),
            },
        }
    def validate_model_spec(raw: str) -> QuerySpec:
        spec = QuerySpec.model_validate_json(raw)
        if any(item.entity_id is not None for item in spec.filters):
            raise ValueError("模型不能指定实体 ID")
        return spec

    return _cached_text(messages, response_format, validate_model_spec,
                        usage_callback, metadata_callback)


def generate_query_spec_patch(
    message: str,
    current: QuerySpec,
    usage_callback: Callable[[dict[str, int]], None] | None = None,
    metadata_callback: Callable[[dict], None] | None = None,
) -> QuerySpecPatch:
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    messages = [
            {"role": "system", "content": PATCH_INSTRUCTIONS},
            {
                "role": "user",
                "content": (
                    f"日期基准：{date.today().isoformat()}\n"
                    f"当前 QuerySpec：{json.dumps(current.model_dump(exclude={'filters': {'__all__': {'entity_id'}}}), ensure_ascii=False)}\n"
                    f"最新消息：{message}\n"
                    f"业务目录：{json.dumps(build_prompt_catalog(message), ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
        ]
    response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "query_spec_patch",
                "strict": True,
                "schema": query_spec_patch_json_schema(),
            },
        }
    return _cached_text(messages, response_format, QuerySpecPatch.model_validate_json,
                        usage_callback, metadata_callback)
