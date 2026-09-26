"""Conservative semantic corrections for model-produced query plans."""

import re

from .query_spec import QuerySpec


REGION_NAMES = {"华东", "华北", "华南", "西南", "西北"}
EXPLICIT_FUZZY = re.compile(r"(?:地区|区域)(?:名|名称)?(?:中)?(?:包含|含有|带有|模糊)|(?:包含|含有|带有)(?:.{0,6})(?:地区|区域)")
RELATIVE_TIME_REPAIRS = {
    ("order_year", "last_year"): "去年",
    ("order_year", "this_year"): "今年",
    ("order_month", "last_month"): "上个月",
    ("order_month", "this_month"): "本月",
    ("order_quarter", "last_quarter"): "上季度",
    ("order_quarter", "this_quarter"): "本季度",
}


def normalize_query_spec(spec: QuerySpec, question: str) -> tuple[QuerySpec, list[dict[str, str]]]:
    """Correct only unambiguous model mistakes supported by the user's wording."""
    fuzzy_region = bool(EXPLICIT_FUZZY.search(question))
    data = spec.model_dump()
    changes: list[dict[str, str]] = []
    for item in data["filters"]:
        relative_value = item["value"].strip().lower()
        expected_phrase = RELATIVE_TIME_REPAIRS.get((item["field"], relative_value))
        if item["operator"] == "eq" and expected_phrase and expected_phrase in question:
            old_field = item["field"]
            item.update(field="order_date", operator=relative_value, value="", values=[])
            changes.append({
                "field": "order_date", "from": f"{old_field} eq {relative_value}",
                "to": relative_value, "reason": "问题明确使用相对时间",
            })
            continue
        if fuzzy_region:
            continue
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
