from __future__ import annotations

from datetime import datetime, timezone
import re
from threading import RLock
from uuid import uuid4
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .query_spec import (
    DIMENSIONS,
    METRICS,
    TIME_FIELDS,
    ComparisonSpec,
    FilterSpec,
    OrderSpec,
    QuerySpec,
)


CHINESE_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
REGIONS = ("华东", "华北", "华南", "西南", "西北")


def _empty_patch_data() -> dict:
    return {
        "reset": False,
        "set_metrics": None,
        "set_dimensions": None,
        "upsert_filters": [],
        "remove_filter_fields": [],
        "set_order_by": None,
        "set_limit": None,
        "clear_limit": False,
        "set_comparison": None,
        "clear_comparison": False,
    }


def _semantic_aliases(items: dict) -> list[tuple[str, str]]:
    aliases: list[tuple[str, str]] = []
    for identifier, item in items.items():
        for alias in [item["label"], *item.get("synonyms", [])]:
            aliases.append((alias, identifier))
    return sorted(aliases, key=lambda pair: len(pair[0]), reverse=True)


METRIC_ALIASES = _semantic_aliases(METRICS)
DIMENSION_ALIASES = _semantic_aliases(DIMENSIONS)


def _parse_count(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    if raw in CHINESE_NUMBERS:
        return CHINESE_NUMBERS[raw]
    if raw == "一百":
        return 100
    match = re.fullmatch(r"([一二三四五六七八九])?十([一二三四五六七八九])?", raw)
    if match:
        tens = CHINESE_NUMBERS.get(match.group(1), 1)
        ones = CHINESE_NUMBERS.get(match.group(2), 0)
        return tens * 10 + ones
    return None


def parse_local_patch(message: str, current: QuerySpec) -> QuerySpecPatch | None:
    """Parse conservative, common follow-ups without spending model tokens.

    A local patch is returned only when every meaningful fragment is consumed.
    Otherwise the caller must fall back to the model.
    """
    text = message.strip()
    if not text:
        return None
    data = _empty_patch_data()
    consumed: list[tuple[int, int]] = []

    def mark(match: re.Match) -> None:
        consumed.append(match.span())

    reset_match = re.search(r"(?:重新查询|重新查|换个问题|新问题)", text)
    if reset_match:
        return None  # A reset needs full intent parsing; let the model build it.

    remove_region = re.search(r"(?:去掉|取消|不要|不限)(?:地区|区域)(?:限制|条件)?", text)
    if remove_region:
        data["remove_filter_fields"].append("region")
        mark(remove_region)
    else:
        region_match = re.search(r"(?:改成|换成|只看|改看)?(华东|华北|华南|西南|西北)(?:地区|区域)?", text)
        if region_match:
            data["upsert_filters"].append({
                "field": "region", "operator": "eq",
                "value": region_match.group(1), "values": [],
            })
            mark(region_match)

    year_match = re.search(r"(?:改成|换成|只看|改看)?(19\d{2}|20\d{2}|21\d{2})年", text)
    if year_match:
        data["remove_filter_fields"].extend(sorted(TIME_FIELDS))
        data["upsert_filters"].append({
            "field": "order_year", "operator": "eq",
            "value": year_match.group(1), "values": [],
        })
        mark(year_match)

    relative_time_patterns = (
        (r"(?:改成|换成|只看|改看)?去年", "last_year"),
        (r"(?:改成|换成|只看|改看)?今年", "this_year"),
        (r"(?:改成|换成|只看|改看)?上个月", "last_month"),
        (r"(?:改成|换成|只看|改看)?本月", "this_month"),
        (r"(?:改成|换成|只看|改看)?上季度", "last_quarter"),
        (r"(?:改成|换成|只看|改看)?本季度", "this_quarter"),
    )
    for pattern, operator in relative_time_patterns:
        relative_match = re.search(pattern, text)
        if relative_match:
            data["remove_filter_fields"].extend(sorted(TIME_FIELDS))
            data["upsert_filters"].append({
                "field": "order_date", "operator": operator,
                "value": "", "values": [],
            })
            mark(relative_match)
            break

    remove_time = re.search(r"(?:去掉|取消|不要|不限)(?:时间|日期|年份)(?:限制|条件)?", text)
    if remove_time:
        data["remove_filter_fields"].extend(sorted(TIME_FIELDS))
        data["clear_comparison"] = True
        mark(remove_time)

    group_patterns = (
        (r"(?:按|每|逐)(?:个)?月(?:份)?(?:看|统计|展示)?", "order_month"),
        (r"(?:按|每|逐)(?:个)?季度(?:看|统计|展示)?", "order_quarter"),
        (r"(?:按|每|逐)(?:个)?年(?:看|统计|展示)?", "order_year"),
    )
    for pattern, dimension in group_patterns:
        group_match = re.search(pattern, text)
        if group_match:
            data["set_dimensions"] = [dimension]
            data["set_order_by"] = [{"field": dimension, "direction": "asc"}]
            mark(group_match)
            break

    limit_match = re.search(r"(?:只看\s*前?|取|前)\s*(\d{1,3}|[一二三四五六七八九十百]{1,3})(?!\d|年|月|日)\s*(?:个|名|条)?", text)
    if limit_match:
        raw = limit_match.group(1)
        limit = _parse_count(raw)
        if limit is None or not 1 <= limit <= 200:
            return None
        data["set_limit"] = limit
        mark(limit_match)
    clear_limit = re.search(r"(?:不限|取消)(?:条数|数量|排名)|(?:显示|查看)?全部", text)
    if clear_limit:
        data["clear_limit"] = True
        mark(clear_limit)

    clear_comparison = re.search(r"(?:不要|取消|去掉)(?:同比|环比|对比)", text)
    if clear_comparison:
        data["clear_comparison"] = True
        mark(clear_comparison)
    elif re.search(r"同比(?:呢|如何|看看)?", text):
        match = re.search(r"同比(?:呢|如何|看看)?", text)
        if len(current.metrics) != 1 or current.dimensions:
            return None
        data["set_comparison"] = {"type": "year_over_year"}
        mark(match)
    elif re.search(r"环比(?:呢|如何|看看)?", text):
        match = re.search(r"环比(?:呢|如何|看看)?", text)
        if len(current.metrics) != 1 or current.dimensions:
            return None
        data["set_comparison"] = {"type": "period_over_period"}
        mark(match)

    metric_cue = re.search(r"(?:改看|改成|换成|看|统计)([^，,。；;]+)", text)
    if metric_cue:
        phrase = metric_cue.group(1)
        for alias, metric_id in METRIC_ALIASES:
            if alias in phrase and metric_id not in current.metrics:
                data["set_metrics"] = [metric_id]
                if any(item.field in current.metrics for item in current.order_by):
                    data["set_order_by"] = []
                mark(metric_cue)
                break

    sort_match = re.search(r"按([^，,。；;]+?)(升序|降序|从高到低|从低到高)", text)
    if sort_match:
        phrase, direction_text = sort_match.groups()
        selected = set(data["set_metrics"] or current.metrics) | set(data["set_dimensions"] or current.dimensions)
        matched_field = next(
            (
                identifier
                for alias, identifier in [*METRIC_ALIASES, *DIMENSION_ALIASES]
                if identifier in selected and alias in phrase
            ),
            None,
        )
        if matched_field is None:
            return None
        direction = "desc" if direction_text in {"降序", "从高到低"} else "asc"
        data["set_order_by"] = [{"field": matched_field, "direction": direction}]
        mark(sort_match)

    if not consumed:
        return None
    mask = list(text)
    for start, end in consumed:
        mask[start:end] = " " * (end - start)
    remainder = "".join(mask)
    remainder = re.sub(r"[\s，,。；;、]+", "", remainder)
    remainder = re.sub(r"^(?:再|然后|并且|并|同时|另外|请|帮我|做)+", "", remainder)
    if remainder:
        return None

    data["remove_filter_fields"] = sorted(set(data["remove_filter_fields"]))
    return QuerySpecPatch.model_validate(data)


class QuerySpecPatch(BaseModel):
    """A model-generated, deterministic update to the current QuerySpec."""

    model_config = ConfigDict(extra="forbid")
    reset: bool
    set_metrics: list[str] | None
    set_dimensions: list[str] | None
    upsert_filters: list[FilterSpec]
    remove_filter_fields: list[str]
    set_order_by: list[OrderSpec] | None
    set_limit: int | None = Field(ge=1, le=200)
    clear_limit: bool
    set_comparison: ComparisonSpec | None
    clear_comparison: bool

    @model_validator(mode="after")
    def validate_patch(self):
        if any(item.entity_id is not None for item in self.upsert_filters):
            raise ValueError("续问不能自行指定实体 ID")
        unknown_metrics = sorted(set(self.set_metrics or []) - set(METRICS))
        unknown_dimensions = sorted(set(self.set_dimensions or []) - set(DIMENSIONS))
        unknown_removed = sorted(set(self.remove_filter_fields) - set(DIMENSIONS))
        if unknown_metrics:
            raise ValueError(f"业务字典中不存在指标：{unknown_metrics}")
        if unknown_dimensions:
            raise ValueError(f"业务字典中不存在维度：{unknown_dimensions}")
        if unknown_removed:
            raise ValueError(f"业务字典中不存在过滤维度：{unknown_removed}")
        if self.set_limit is not None and self.clear_limit:
            raise ValueError("不能同时设置和清空 limit")
        if self.set_comparison is not None and self.clear_comparison:
            raise ValueError("不能同时设置和清空 comparison")
        return self


def query_spec_patch_json_schema() -> dict:
    schema = QuerySpecPatch.model_json_schema()
    schema["properties"]["set_metrics"]["anyOf"][0]["items"]["enum"] = sorted(METRICS)
    schema["properties"]["set_dimensions"]["anyOf"][0]["items"]["enum"] = sorted(DIMENSIONS)
    schema["properties"]["remove_filter_fields"]["items"]["enum"] = sorted(DIMENSIONS)
    schema["$defs"]["FilterSpec"]["properties"]["field"]["enum"] = sorted(DIMENSIONS)
    schema["$defs"]["FilterSpec"]["properties"].pop("entity_id", None)
    schema["$defs"]["FilterSpec"]["required"] = [
        name for name in schema["$defs"]["FilterSpec"].get("required", []) if name != "entity_id"
    ]
    schema["$defs"]["OrderSpec"]["properties"]["field"]["enum"] = sorted(set(METRICS) | set(DIMENSIONS))
    return schema


def apply_query_spec_patch(current: QuerySpec, patch: QuerySpecPatch) -> tuple[QuerySpec, list[str]]:
    """Apply a patch without mutating the previous valid query state."""
    if patch.reset:
        data = {
            "metrics": [], "dimensions": [], "filters": [], "order_by": [],
            "limit": None, "comparison": None,
        }
    else:
        data = current.model_dump()

    changes: list[str] = []
    if patch.reset:
        changes.append("已重置上一轮查询条件")
    if patch.set_metrics is not None:
        data["metrics"] = patch.set_metrics
        changes.append("已更新指标")
    if patch.set_dimensions is not None:
        data["dimensions"] = patch.set_dimensions
        changes.append("已更新分组维度")

    remove_fields = set(patch.remove_filter_fields)
    upsert_fields = {item.field for item in patch.upsert_filters}
    replaced_fields = remove_fields | upsert_fields
    if replaced_fields:
        data["filters"] = [item for item in data["filters"] if item["field"] not in replaced_fields]
    if remove_fields:
        changes.append("已移除过滤条件：" + "、".join(sorted(remove_fields)))
    for item in patch.upsert_filters:
        data["filters"].append(item.model_dump())
    if upsert_fields:
        changes.append("已更新过滤条件：" + "、".join(sorted(upsert_fields)))

    if patch.set_order_by is not None:
        data["order_by"] = [item.model_dump() for item in patch.set_order_by]
        changes.append("已更新排序")
    if patch.clear_limit:
        data["limit"] = None
        changes.append("已取消条数限制")
    elif patch.set_limit is not None:
        data["limit"] = patch.set_limit
        changes.append(f"返回条数已改为 {patch.set_limit}")
    if patch.clear_comparison:
        data["comparison"] = None
        changes.append("已取消时间对比")
    elif patch.set_comparison is not None:
        data["comparison"] = patch.set_comparison.model_dump()
        changes.append("已更新时间对比方式")

    if not changes:
        raise ValueError("没有识别到有效的查询修改")
    return QuerySpec.model_validate(data), changes


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    query_spec: QuerySpec
    last_message: str
    first_message: str | None = None
    turn_count: int = Field(ge=1)
    pending_clarification: dict | None = None
    updated_at: datetime


class SessionConflictError(Exception):
    """The session changed after a caller read it."""


class ConversationStore:
    """Small process-local store; replaceable with Redis without changing routes."""

    def __init__(
        self,
        ttl_seconds: int = 3600,
        clock: Callable[[], datetime] | None = None,
        max_sessions: int = 1000,
    ):
        if ttl_seconds < 1:
            raise ValueError("会话过期时间必须大于 0 秒")
        if max_sessions < 1:
            raise ValueError("最大会话数必须大于 0")
        self._states: dict[str, ConversationState] = {}
        self._lock = RLock()
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _purge_expired_locked(self) -> int:
        now = self._now()
        expired = [
            session_id
            for session_id, state in self._states.items()
            if (now - state.updated_at).total_seconds() >= self._ttl_seconds
        ]
        for session_id in expired:
            del self._states[session_id]
        return len(expired)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_expired_locked()

    def create(
        self,
        spec: QuerySpec,
        message: str,
        pending_clarification: dict | None = None,
    ) -> ConversationState:
        state = ConversationState(
            session_id=uuid4().hex,
            query_spec=spec,
            last_message=message,
            first_message=message,
            turn_count=1,
            pending_clarification=pending_clarification,
            updated_at=self._now(),
        )
        with self._lock:
            self._purge_expired_locked()
            self._states[state.session_id] = state
            while len(self._states) > self._max_sessions:
                oldest = min(self._states.values(), key=lambda item: item.updated_at)
                del self._states[oldest.session_id]
        return state.model_copy(deep=True)

    def get(self, session_id: str) -> ConversationState:
        with self._lock:
            self._purge_expired_locked()
            state = self._states.get(session_id)
            if state is None:
                raise KeyError("会话不存在或已过期")
            return state.model_copy(deep=True)

    def update(
        self,
        session_id: str,
        spec: QuerySpec,
        message: str,
        pending_clarification: dict | None = None,
        expected_turn_count: int | None = None,
    ) -> ConversationState:
        with self._lock:
            self._purge_expired_locked()
            current = self._states.get(session_id)
            if current is None:
                raise KeyError("会话不存在或已过期")
            if expected_turn_count is not None and current.turn_count != expected_turn_count:
                raise SessionConflictError("会话已在其他页面更新，请刷新后重试")
            updated = ConversationState(
                session_id=session_id,
                query_spec=spec,
                last_message=message,
                first_message=current.first_message or current.last_message,
                turn_count=current.turn_count + 1,
                pending_clarification=pending_clarification,
                updated_at=self._now(),
            )
            self._states[session_id] = updated
            return updated.model_copy(deep=True)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            self._purge_expired_locked()
            return self._states.pop(session_id, None) is not None

    def list_recent(self, limit: int = 20) -> list[ConversationState]:
        with self._lock:
            self._purge_expired_locked()
            states = sorted(self._states.values(), key=lambda state: state.updated_at,
                            reverse=True)
            return [state.model_copy(deep=True) for state in states[:limit]]
