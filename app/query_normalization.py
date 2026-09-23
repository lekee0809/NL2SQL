"""Conservative semantic corrections for model-produced query plans."""

import re

from .query_spec import QuerySpec


REGION_NAMES = {"华东", "华北", "华南", "西南", "西北"}
EXPLICIT_FUZZY = re.compile(r"(?:地区|区域)(?:名|名称)?(?:中)?(?:包含|含有|带有|模糊)|(?:包含|含有|带有)(?:.{0,6})(?:地区|区域)")


def normalize_query_spec(spec: QuerySpec, question: str) -> tuple[QuerySpec, list[dict[str, str]]]:
    """Use exact matching for an explicitly named region, preserving fuzzy intent."""
    if EXPLICIT_FUZZY.search(question):
        return spec, []
    data = spec.model_dump()
    changes: list[dict[str, str]] = []
    for item in data["filters"]:
        if item["field"] != "region" or item["operator"] != "contains":
            continue
        raw = item["value"].strip()
        region = re.sub(r"(?:地区|区域)$", "", raw)
        if region not in REGION_NAMES:
            continue
        if not re.search(re.escape(region) + r"(?:地区|区域)", question):
            continue
        if re.search(re.escape(region) + r"(?:地区|区域)[^，,。\s]{0,12}(?:学校|学院|大学|公司|集团)", question):
            continue
        item["operator"] = "eq"
        changes.append({
            "field": "region",
            "from": "contains",
            "to": "eq",
            "reason": "问题明确指定了地区名称",
        })
    return (QuerySpec.model_validate(data), changes) if changes else (spec, [])
