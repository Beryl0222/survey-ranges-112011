"""桩号解析与书写。

桩号沿路线向里程增加方向书写。断链处一个道路坐标可以有两个桩号，
所以这里只负责把单个桩号换算成米数，不负责判断唯一性。
"""
from __future__ import annotations

import re

_CHAINAGE = re.compile(r"^\s*K(\d+)\+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def parse_chainage(text: str) -> float:
    """把 K8+000 这样的桩号换成米数；写法不合规时抛出 ValueError。"""
    if not isinstance(text, str):
        raise ValueError("桩号必须是文字")
    match = _CHAINAGE.match(text)
    if not match:
        raise ValueError(f"无法识别的桩号：{text!r}")
    kilometres = int(match.group(1))
    metres = float(match.group(2))
    if metres >= 1000:
        raise ValueError(f"桩号中米数应小于 1000：{text!r}")
    return kilometres * 1000 + metres


def format_chainage(metres: float) -> str:
    """按 K8+000 的写法输出米数，用于展示，不用于回填原文。"""
    whole = int(round(metres))
    kilometres, remainder = divmod(whole, 1000)
    return f"K{kilometres}+{remainder:03d}"
