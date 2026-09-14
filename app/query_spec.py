import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parent.parent
DICTIONARY = json.loads((ROOT / "business_dictionary.json").read_text(encoding="utf-8"))
METRICS = DICTIONARY["metrics"]
DIMENSIONS = DICTIONARY["dimensions"]

FilterOperator = Literal[
    "eq", "neq", "contains", "in", "gte", "lte", "between", "year",
    "calendar_month", "calendar_quarter", "month_range", "this_year", "last_year",
    "this_month", "last_month", "this_quarter", "last_quarter", "last_n_days",
    "last_n_months",
]


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    operator: FilterOperator
    value: str
    values: list[str]


class OrderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    direction: Literal["asc", "desc"]


class ComparisonSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["year_over_year", "period_over_period"]


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[str]
    dimensions: list[str]
    filters: list[FilterSpec]
    order_by: list[OrderSpec]
    limit: int | None = Field(ge=1, le=200)
    comparison: ComparisonSpec | None

    @model_validator(mode="after")
    def validate_query_shape(self):
        if not self.metrics and not self.dimensions:
            raise ValueError("至少需要一个指标或维度")
        unknown_metrics = sorted(set(self.metrics) - set(METRICS))
        unknown_dimensions = sorted(set(self.dimensions) - set(DIMENSIONS))
        unknown_filter_fields = sorted({item.field for item in self.filters} - set(DIMENSIONS))
        if unknown_metrics:
            raise ValueError(f"业务字典中不存在指标：{unknown_metrics}")
        if unknown_dimensions:
            raise ValueError(f"业务字典中不存在维度：{unknown_dimensions}")
        if unknown_filter_fields:
            raise ValueError(f"业务字典中不存在过滤维度：{unknown_filter_fields}")
        selected = set(self.metrics) | set(self.dimensions)
        unknown = [item.field for item in self.order_by if item.field not in selected]
        if unknown:
            raise ValueError(f"排序字段必须出现在查询结果中：{unknown}")
        if self.comparison is not None:
            if len(self.metrics) != 1 or self.dimensions:
                raise ValueError("当前时间对比只支持一个指标且不带分组维度")
            if self.order_by:
                raise ValueError("时间对比不需要排序字段")
            time_filters = [item for item in self.filters if item.field in TIME_FIELDS]
            if len(time_filters) != 1:
                raise ValueError("时间对比必须且只能包含一个时间过滤条件")
        product_dimensions = {"product", "category"} & set(self.dimensions)
        if product_dimensions and ({"order_amount", "avg_order_amount", "max_order_amount"} & set(self.metrics)):
            raise ValueError("订单粒度金额不能直接按商品维度拆分，请改用销售额")
        if "payment_method" in self.dimensions and ({"sales_amount", "sold_quantity", "avg_unit_price"} & set(self.metrics)):
            raise ValueError("订单明细指标不能直接按支付方式拆分")
        return self


def query_spec_json_schema() -> dict:
    """从业务字典动态生成模型白名单，避免在 Python 中维护第二份 ID。"""
    schema = QuerySpec.model_json_schema()
    schema["properties"]["metrics"]["items"]["enum"] = sorted(METRICS)
    schema["properties"]["dimensions"]["items"]["enum"] = sorted(DIMENSIONS)
    schema["$defs"]["FilterSpec"]["properties"]["field"]["enum"] = sorted(DIMENSIONS)
    schema["$defs"]["OrderSpec"]["properties"]["field"]["enum"] = sorted(set(METRICS) | set(DIMENSIONS))
    return schema


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: tuple[object, ...]


JOIN_SQL = {
    "order_items": "JOIN order_items oi ON oi.order_id = o.id",
    "products": "JOIN products p ON p.id = oi.product_id",
    "customers": "JOIN customers c ON c.id = o.customer_id",
    "payments": "JOIN payments pay ON pay.order_id = o.id",
}


def compact_catalog() -> dict:
    def entry(item: dict) -> dict:
        result = {"label": item["label"], "synonyms": item.get("synonyms", [])}
        if item.get("value_aliases"):
            result["known_values"] = list(item["value_aliases"])
        return result
    return {
        "metrics": {key: entry(value) for key, value in METRICS.items()},
        "dimensions": {key: entry(value) for key, value in DIMENSIONS.items()},
        "rules": DICTIONARY["rules"],
    }


def _coerce_value(field: str, value: str) -> object:
    value_type = DIMENSIONS[field]["value_type"]
    if value_type == "number":
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field} 需要数字，收到：{value}") from exc
    if value_type == "date":
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} 需要 YYYY-MM-DD 日期，收到：{value}") from exc
    return value


TIME_FIELDS = {"order_date", "order_month", "order_quarter", "order_year"}
RELATIVE_TIME_OPERATORS = {
    "this_year", "last_year", "this_month", "last_month", "this_quarter",
    "last_quarter", "last_n_days", "last_n_months",
}


def _shift_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    month_days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(value.day, month_days[month - 1]))


def _date_range(item: FilterSpec, reference_date: date) -> tuple[date, date] | None:
    if item.operator not in RELATIVE_TIME_OPERATORS | {"calendar_month", "calendar_quarter", "month_range"}:
        return None
    if item.field not in TIME_FIELDS:
        raise ValueError(f"{item.operator} 只能用于时间维度")

    year_start = date(reference_date.year, 1, 1)
    month_start = date(reference_date.year, reference_date.month, 1)
    quarter_month = ((reference_date.month - 1) // 3) * 3 + 1
    quarter_start = date(reference_date.year, quarter_month, 1)
    if item.operator == "this_year":
        return year_start, date(reference_date.year + 1, 1, 1)
    if item.operator == "last_year":
        return date(reference_date.year - 1, 1, 1), year_start
    if item.operator == "this_month":
        return month_start, _shift_months(month_start, 1)
    if item.operator == "last_month":
        return _shift_months(month_start, -1), month_start
    if item.operator == "this_quarter":
        return quarter_start, _shift_months(quarter_start, 3)
    if item.operator == "last_quarter":
        return _shift_months(quarter_start, -3), quarter_start
    if item.operator == "last_n_days":
        try:
            count = int(item.value)
        except ValueError as exc:
            raise ValueError("last_n_days 的 value 必须是天数") from exc
        if not 1 <= count <= 3650:
            raise ValueError("最近天数必须在 1 到 3650 之间")
        return reference_date - timedelta(days=count - 1), reference_date + timedelta(days=1)
    if item.operator == "last_n_months":
        try:
            count = int(item.value)
        except ValueError as exc:
            raise ValueError("last_n_months 的 value 必须是月数") from exc
        if not 1 <= count <= 120:
            raise ValueError("最近月数必须在 1 到 120 之间")
        return _shift_months(reference_date, -count), reference_date + timedelta(days=1)
    if item.operator == "calendar_month":
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", item.value):
            raise ValueError("calendar_month 需要 YYYY-MM")
        start = date.fromisoformat(item.value + "-01")
        return start, _shift_months(start, 1)
    if item.operator == "calendar_quarter":
        match = re.fullmatch(r"(\d{4})-Q([1-4])", item.value.upper())
        if not match:
            raise ValueError("calendar_quarter 需要 YYYY-Q1 至 YYYY-Q4")
        start = date(int(match.group(1)), (int(match.group(2)) - 1) * 3 + 1, 1)
        return start, _shift_months(start, 3)
    if len(item.values) != 2 or not all(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value) for value in item.values):
        raise ValueError("month_range 需要两个 YYYY-MM values")
    start = date.fromisoformat(item.values[0] + "-01")
    end_month = date.fromisoformat(item.values[1] + "-01")
    if start > end_month:
        raise ValueError("month_range 的开始月份不能晚于结束月份")
    return start, _shift_months(end_month, 1)


def _compile_filter(item: FilterSpec, reference_date: date) -> tuple[str, list[object]]:
    expression = DIMENSIONS[item.field]["expression"]
    value_type = DIMENSIONS[item.field]["value_type"]
    date_range = _date_range(item, reference_date)
    if date_range is not None:
        return "o.created_at >= %s AND o.created_at < %s", list(date_range)
    if item.operator == "contains":
        if value_type != "text":
            raise ValueError("contains 只能用于文本维度")
        return f"{expression} ILIKE %s", [f"%{item.value}%"]
    if item.operator == "in":
        if not item.values:
            raise ValueError("in 过滤必须提供 values")
        values = [_coerce_value(item.field, value) for value in item.values]
        return f"{expression} IN ({', '.join(['%s'] * len(values))})", values
    if item.operator == "between":
        if len(item.values) != 2:
            raise ValueError("between 过滤必须提供两个 values")
        values = [_coerce_value(item.field, value) for value in item.values]
        return f"{expression} BETWEEN %s AND %s", values
    if item.operator == "year":
        if item.field not in {"order_date", "order_month", "order_quarter", "order_year"}:
            raise ValueError("year 只能用于时间维度")
        try:
            year = int(item.value)
        except ValueError as exc:
            raise ValueError("year 过滤值必须是四位年份") from exc
        if not 1900 <= year <= 2200:
            raise ValueError("year 超出允许范围")
        return "o.created_at >= %s AND o.created_at < %s", [date(year, 1, 1), date(year + 1, 1, 1)]
    operators = {"eq": "=", "neq": "<>", "gte": ">=", "lte": "<="}
    return f"{expression} {operators[item.operator]} %s", [_coerce_value(item.field, item.value)]


def _comparison_time_range(item: FilterSpec, reference_date: date) -> tuple[date, date]:
    calculated = _date_range(item, reference_date)
    if calculated is not None:
        return calculated
    if item.operator == "year" or (item.operator == "eq" and item.field == "order_year"):
        try:
            year = int(item.value)
        except ValueError as exc:
            raise ValueError("对比年份必须是四位数字") from exc
        return date(year, 1, 1), date(year + 1, 1, 1)
    if item.operator == "eq" and item.field == "order_date":
        day = date.fromisoformat(item.value)
        return day, day + timedelta(days=1)
    if item.operator == "between" and item.field == "order_date" and len(item.values) == 2:
        start, end = map(date.fromisoformat, item.values)
        if start > end:
            raise ValueError("对比日期范围的开始日期不能晚于结束日期")
        return start, end + timedelta(days=1)
    raise ValueError("该时间过滤方式暂不支持同比或环比")


def _previous_period(item: FilterSpec, start: date, end: date, comparison_type: str) -> tuple[date, date]:
    if comparison_type == "year_over_year":
        return _shift_months(start, -12), _shift_months(end, -12)
    if item.operator in {"this_year", "last_year", "year"} or (item.operator == "eq" and item.field == "order_year"):
        months = 12
    elif item.operator in {"this_quarter", "last_quarter", "calendar_quarter"}:
        months = 3
    elif item.operator in {"this_month", "last_month", "calendar_month"}:
        months = 1
    elif item.operator == "month_range":
        first = date.fromisoformat(item.values[0] + "-01")
        last = date.fromisoformat(item.values[1] + "-01")
        months = (last.year - first.year) * 12 + last.month - first.month + 1
    elif item.operator == "last_n_months":
        months = int(item.value)
    else:
        duration = end - start
        return start - duration, start
    return _shift_months(start, -months), _shift_months(end, -months)


def _compile_comparison_query(spec: QuerySpec, reference_date: date) -> CompiledQuery:
    metric_id = spec.metrics[0]
    metric = METRICS[metric_id]
    time_filter = next(item for item in spec.filters if item.field in TIME_FIELDS)
    other_filters = [item for item in spec.filters if item is not time_filter]
    current_start, current_end = _comparison_time_range(time_filter, reference_date)
    previous_start, previous_end = _previous_period(
        time_filter, current_start, current_end, spec.comparison.type
    )

    required_tables = set(metric["tables"]) | {"orders"}
    for item in other_filters:
        required_tables.update(DIMENSIONS[item.field]["tables"])
    if "products" in required_tables:
        required_tables.add("order_items")
    joins = [JOIN_SQL[name] for name in ("order_items", "products", "customers", "payments") if name in required_tables]

    shared_clauses: list[str] = []
    shared_params: list[object] = []
    for item in other_filters:
        clause, params = _compile_filter(item, reference_date)
        shared_clauses.append(clause)
        shared_params.extend(params)
    if not any(item.field == "order_status" for item in other_filters) and metric.get("default_paid_only"):
        shared_clauses.append("o.status = %s")
        shared_params.append("PAID")

    def period_cte(name: str, start: date, end: date) -> tuple[str, list[object]]:
        clauses = ["o.created_at >= %s", "o.created_at < %s", *shared_clauses]
        sql = (
            f"{name} AS (SELECT {metric['expression']} AS value FROM orders o "
            f"{' '.join(joins)} WHERE " + " AND ".join(f"({clause})" for clause in clauses) + ")"
        )
        return sql, [start, end, *shared_params]

    current_sql, current_params = period_cte("current_period", current_start, current_end)
    previous_sql, previous_params = period_cte("previous_period", previous_start, previous_end)
    comparison_label = "上年同期" if spec.comparison.type == "year_over_year" else "上期"
    growth_label = "同比增长率" if spec.comparison.type == "year_over_year" else "环比增长率"
    label = metric["label"]
    sql = (
        f"WITH {current_sql}, {previous_sql}\n"
        f'SELECT c.value AS "本期{label}", p.value AS "{comparison_label}{label}",\n'
        f'ROUND(((c.value - p.value) / NULLIF(p.value, 0) * 100)::numeric, 2) AS "{growth_label}"\n'
        "FROM current_period c CROSS JOIN previous_period p\nLIMIT 1"
    )
    return CompiledQuery(sql=sql, params=tuple(current_params + previous_params))


def compile_query(spec: QuerySpec, reference_date: date | None = None) -> CompiledQuery:
    reference_date = reference_date or date.today()
    if spec.comparison is not None:
        return _compile_comparison_query(spec, reference_date)
    selected_ids = [*spec.dimensions, *spec.metrics]
    select_parts = [
        f'{DIMENSIONS[field]["expression"]} AS "{DIMENSIONS[field]["label"]}"'
        for field in spec.dimensions
    ] + [
        f'{METRICS[field]["expression"]} AS "{METRICS[field]["label"]}"'
        for field in spec.metrics
    ]

    required_tables = {"orders"}
    for field in spec.metrics:
        required_tables.update(METRICS[field]["tables"])
    for field in spec.dimensions:
        required_tables.update(DIMENSIONS[field]["tables"])
    for item in spec.filters:
        required_tables.update(DIMENSIONS[item.field]["tables"])
    if "products" in required_tables:
        required_tables.add("order_items")

    joins = [JOIN_SQL[name] for name in ("order_items", "products", "customers", "payments") if name in required_tables]
    where_parts: list[str] = []
    params: list[object] = []
    for item in spec.filters:
        clause, clause_params = _compile_filter(item, reference_date)
        where_parts.append(clause)
        params.extend(clause_params)
    has_status_filter = any(item.field == "order_status" for item in spec.filters)
    if not has_status_filter and any(METRICS[field].get("default_paid_only") for field in spec.metrics):
        where_parts.append("o.status = %s")
        params.append("PAID")

    distinct = "DISTINCT " if not spec.metrics else ""
    sql_parts = [f"SELECT {distinct}{', '.join(select_parts)}", "FROM orders o", *joins]
    if where_parts:
        sql_parts.append("WHERE " + " AND ".join(f"({part})" for part in where_parts))
    if spec.metrics and spec.dimensions:
        sql_parts.append("GROUP BY " + ", ".join(str(index + 1) for index in range(len(spec.dimensions))))
    if spec.order_by:
        positions = {field: index + 1 for index, field in enumerate(selected_ids)}
        sql_parts.append("ORDER BY " + ", ".join(f"{positions[item.field]} {item.direction.upper()}" for item in spec.order_by))
    sql_parts.append(f"LIMIT {spec.limit or 200}")
    return CompiledQuery(sql="\n".join(sql_parts), params=tuple(params))
