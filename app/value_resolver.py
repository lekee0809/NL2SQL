import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Callable

from .database import fetch_distinct_text_values
from .entity_aliases import EntityMatch, lookup_alias, lookup_name
from .query_spec import DIMENSIONS, QuerySpec


# 只有这些后端白名单字段会进入值解析；表名和列名不能由模型或用户指定。
VALUE_SOURCES = {
    "product": ("products", "name"),
    "category": ("products", "category"),
    "region": ("customers", "region"),
    "customer": ("customers", "name"),
    "payment_method": ("payments", "method"),
    "order_status": ("orders", "status"),
}


@dataclass(frozen=True)
class Resolution:
    field: str
    requested: str
    resolved: str
    score: float
    method: str


class NeedsClarification(ValueError):
    def __init__(self, filter_index: int, field: str, requested: str, candidates: list[dict], spec: QuerySpec):
        self.filter_index = filter_index
        self.field = field
        self.requested = requested
        self.candidates = candidates
        self.spec = spec
        super().__init__(f'“{requested}”存在多个可能值，请选择一个')

    def detail(self) -> dict:
        return {
            "type": "needs_clarification",
            "message": str(self),
            "filter_index": self.filter_index,
            "field": self.field,
            "requested": self.requested,
            "candidates": self.candidates,
            "query_spec": self.spec.model_dump(),
        }


def _normalize(value: str, field: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    normalized = normalized.replace("_", "")
    if field == "region":
        normalized = re.sub(r"(地区|区域)$", "", normalized)
    return normalized


def _score(requested: str, candidate: str, field: str) -> float:
    left, right = _normalize(requested, field), _normalize(candidate, field)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    # “15号测试商品”与“测试商品_15”具有相同名称主体和编号。
    if field in {"product", "customer"}:
        left_numbers = re.findall(r"\d+", left)
        right_numbers = re.findall(r"\d+", right)
        left_text = re.sub(r"\d+号?", "", left)
        right_text = re.sub(r"\d+号?", "", right)
        if left_numbers == right_numbers and left_numbers and left_text == right_text:
            return 1.0
    ratio = SequenceMatcher(None, left, right).ratio()
    if left in right or right in left:
        ratio = max(ratio, min(len(left), len(right)) / max(len(left), len(right)) + 0.12)
    return min(ratio, 0.99)


def _resolve_alias(requested: str, field: str, candidates: tuple[str, ...]) -> str | None:
    aliases = DIMENSIONS[field].get("value_aliases", {})
    requested_key = _normalize(requested, field)
    for alias, target in aliases.items():
        if _normalize(alias, field) == requested_key and target in candidates:
            return target
    return None


@lru_cache(maxsize=32)
def _load_candidates(field: str) -> tuple[str, ...]:
    table, column = VALUE_SOURCES[field]
    return tuple(fetch_distinct_text_values(table, column))


def clear_candidate_cache() -> None:
    _load_candidates.cache_clear()


def infer_entity_dimensions(
    question: str,
    loader: Callable[[str], tuple[str, ...]] = _load_candidates,
) -> set[str]:
    """只在本地判断问题是否包含商品/客户实体，不返回或外发候选值。"""
    dimensions: set[str] = set()
    for field in ("product", "customer"):
        normalized_question = _normalize(question, field)
        for candidate in loader(field):
            normalized_candidate = _normalize(candidate, field)
            if len(normalized_candidate) < 4:
                continue
            match = SequenceMatcher(None, normalized_question, normalized_candidate).find_longest_match()
            if (
                normalized_candidate in normalized_question
                or (match.size >= 4 and match.size / len(normalized_candidate) >= 0.45)
            ):
                dimensions.add(field)
                break
    return dimensions


def resolve_query_spec(
    spec: QuerySpec,
    loader: Callable[[str], tuple[str, ...]] = _load_candidates,
    alias_lookup: Callable[[str, str], list[EntityMatch]] | None = None,
    name_lookup: Callable[[str, str], list[EntityMatch]] | None = None,
) -> tuple[QuerySpec, list[Resolution]]:
    if alias_lookup is None and loader is _load_candidates:
        alias_lookup = lookup_alias
    if name_lookup is None and loader is _load_candidates:
        name_lookup = lookup_name
    data = spec.model_dump()
    resolutions: list[Resolution] = []
    for index, item in enumerate(spec.filters):
        if item.operator != "eq" or item.field not in VALUE_SOURCES or not item.value.strip():
            continue
        if item.entity_id is not None:
            continue  # A previously confirmed entity stays pinned across follow-ups.
        requested = item.value.strip()
        if item.field in {"product", "customer"} and alias_lookup is not None:
            matches = alias_lookup(item.field, requested)
            if len(matches) > 1:
                candidates = [{"value": match.value, "entity_id": match.entity_id, "score": 1.0}
                              for match in matches]
                raise NeedsClarification(index, item.field, requested, candidates, spec)
            if len(matches) == 1:
                match = matches[0]
                data["filters"][index].update(value=match.value, entity_id=match.entity_id)
                resolutions.append(Resolution(item.field, requested, match.value, 1.0, "entity_alias"))
                continue
        if item.field in {"product", "customer"} and name_lookup is not None:
            matches = name_lookup(item.field, requested)
            if len(matches) > 1:
                raise NeedsClarification(index, item.field, requested, [
                    {"value": match.value, "entity_id": match.entity_id, "score": 1.0}
                    for match in matches
                ], spec)
            if len(matches) == 1:
                match = matches[0]
                data["filters"][index].update(value=match.value, entity_id=match.entity_id)
                continue
        loaded = loader(item.field)
        alias_target = _resolve_alias(requested, item.field, loaded)
        if alias_target is not None:
            data["filters"][index]["value"] = alias_target
            if item.field in {"product", "customer"} and name_lookup is not None:
                matches = name_lookup(item.field, alias_target)
                if len(matches) > 1:
                    raise NeedsClarification(index, item.field, requested, [
                        {"value": match.value, "entity_id": match.entity_id, "score": 1.0}
                        for match in matches
                    ], spec)
                if matches:
                    data["filters"][index]["entity_id"] = matches[0].entity_id
            resolutions.append(Resolution(
                field=item.field,
                requested=requested,
                resolved=alias_target,
                score=1.0,
                method="alias",
            ))
            continue
        ranked = sorted(
            ((candidate, _score(requested, candidate, item.field)) for candidate in loaded),
            key=lambda pair: (-pair[1], pair[0]),
        )
        if not ranked:
            raise ValueError(f"字段 {item.field} 暂无可匹配的数据值")
        best_value, best_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        if best_score == 1.0 or (best_score >= 0.86 and best_score - runner_up >= 0.08):
            if item.field in {"product", "customer"} and name_lookup is not None:
                matches = name_lookup(item.field, best_value)
                if len(matches) > 1:
                    raise NeedsClarification(index, item.field, requested, [
                        {"value": match.value, "entity_id": match.entity_id, "score": round(best_score, 3)}
                        for match in matches
                    ], spec)
                if matches:
                    data["filters"][index]["entity_id"] = matches[0].entity_id
            data["filters"][index]["value"] = best_value
            if requested != best_value:
                resolutions.append(Resolution(
                    field=item.field,
                    requested=requested,
                    resolved=best_value,
                    score=round(best_score, 3),
                    method="exact" if best_score == 1.0 else "fuzzy",
                ))
            continue
        candidates = [
            {"value": value, "score": round(score, 3)}
            for value, score in ranked[:5]
            if score >= 0.35
        ]
        if not candidates:
            raise ValueError(f'数据库中找不到与“{requested}”接近的值')
        raise NeedsClarification(index, item.field, requested, candidates, spec)
    return QuerySpec.model_validate(data), resolutions
