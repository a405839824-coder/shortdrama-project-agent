"""Closed state equivalences; never infer unseen people or prop transfers."""
from __future__ import annotations


def state_value(value):
    if isinstance(value, dict):
        return {key: state_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [state_value(item) for item in value]
    if isinstance(value, str):
        # Whole-value aliases only: no fuzzy name/position/holder comparison.
        aliases = {"已关闭": "关闭", "关着": "关闭", "闭合": "关闭",
                   "已打开": "打开", "开着": "打开"}
        return aliases.get(value.strip(), value.strip())
    return value


def state_equal(left, right):
    return state_value(left) == state_value(right)
