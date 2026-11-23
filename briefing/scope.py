"""图面范围、横向单元以及它们与文字事实的相交。

图上的起止是项目道路坐标；文字里的桩号要先用 locate 换成坐标才能比。
横向按半幅/路肩记录：没写不等于全幅，更不等于路肩已覆盖。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:至|到|－|-|—)\s*(\d+(?:\.\d+)?)")

UNITS = ("主线北半幅", "主线南半幅", "北肩", "南肩")


@dataclass
class DrawingScope:
    低: float
    高: float
    横向: list
    原文: str
    备注: list = field(default_factory=list)

    def as_dict(self):
        return {
            "低坐标": self.低,
            "高坐标": self.高,
            "横向": self.横向,
            "原文": self.原文,
            "备注": self.备注,
        }


def parse_drawing_scope(text):
    match = RANGE.search(text or "")
    if not match:
        raise ValueError(f"图面范围无法识别：{text!r}")
    first, second = float(match.group(1)), float(match.group(2))
    low, high = sorted((first, second))
    units = []
    notes = []
    if "主线" in text:
        units.extend(["主线北半幅", "主线南半幅"])
    if "北肩" in text:
        units.append("北肩")
    if "南肩" in text:
        units.append("南肩")
    if "全幅" in text:
        units.extend(UNITS)
    if "路肩" in text and "北肩" not in text and "南肩" not in text:
        notes.append("图面只写“路肩”未分南北，按两侧路肩分别核对")
        units.extend(["北肩", "南肩"])
    if not units:
        units.extend(UNITS)
        notes.append("图面未写主线或路肩，按全部横向单元分别核对")
    seen = set()
    units = [unit for unit in units if not (unit in seen or seen.add(unit))]
    return DrawingScope(low, high, units, text, notes)


def fact_units(laterals):
    """把文字事实的横向词换成可比对的单元集合。

    返回 (units, notes)。横向未说明时列全部单元，但必须同时保留
    “不能据此认定已覆盖”的警示。
    """
    units = []
    notes = []
    for item in laterals or []:
        if item in UNITS:
            units.append(item)
        elif item == "北半幅":
            units.append("主线北半幅")
        elif item == "南半幅":
            units.append("主线南半幅")
        elif item == "主线":
            units.extend(["主线北半幅", "主线南半幅"])
        elif item == "全幅":
            units.extend(UNITS)
        elif item in ("路肩（未分侧）", "路肩"):
            units.extend(["北肩", "南肩"])
            notes.append("原文只写路肩未分南北，两侧都要核对")
        elif item == "两端衔接":
            continue
    if not units:
        if laterals and all(item in ("两端衔接",) for item in laterals):
            return [], notes
        units = list(UNITS)
        notes.append("横向未说明，按全部单元分别核对但不能据此认定已覆盖")
    seen = set()
    return [unit for unit in units if not (unit in seen or seen.add(unit))], notes


def overlap(low_a, high_a, low_b, high_b):
    low, high = max(low_a, low_b), min(high_a, high_b)
    if low > high:
        return None
    if low == high and not (low_a <= low <= high_a and low_b <= low <= high_b):
        return None
    return low, high


def subtract_ranges(low, high, removed):
    """从 [low, high] 中去掉若干闭区间。"""
    segments = [(low, high)]
    for cut_low, cut_high in removed:
        remaining = []
        for seg_low, seg_high in segments:
            if cut_high < seg_low or cut_low > seg_high:
                remaining.append((seg_low, seg_high))
                continue
            if seg_low < cut_low:
                remaining.append((seg_low, cut_low))
            if cut_high < seg_high:
                remaining.append((cut_high, seg_high))
        segments = remaining
    return segments
