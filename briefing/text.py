"""原文切分和词面识别。

识别结果只是线索：这里不替材料下结论，也不改写任何原句。
"""
from __future__ import annotations

import re

SENTENCE_END = re.compile(r"[^。；！？!?]*[。；！？!?]?")
CHAINAGE = re.compile(r"K\d+\+\d+(?:\.\d+)?", re.IGNORECASE)
PLAIN_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:至|到|－|-|—)\s*(\d+(?:\.\d+)?)")

LINE_NAMES = ("正线", "连接线", "借用线")
LANE_UNITS = ("主线北半幅", "主线南半幅", "北肩", "南肩")

# 否定和缺口优先于“已试挖/已查”，因为它们决定这句话能证明到什么程度。
NEGATIVE_COVERAGE = (
    "尚未调查", "未调查", "未覆盖", "未测", "缺试坑", "仍缺", "未分",
    "不能代表", "未分幅",
)
NEGATIVE_FINDING = ("未见", "未发现", "没有见到")
LOCATING_BLOCK = ("断链", "暂不能按旧线桩号", "同一断链点", "无法区分")
SUSPECT = ("疑似", "疑有", "怀疑", "推测")
SCOPE_ONLY = ("只核", "只按", "仅按", "仅核", "只检查", "只核两端")
LIMIT = (
    "不能代表", "不表示", "不得代表", "不能据此", "不能一律",
    "未签", "不等同", "仅作提醒",
)
FOUND = ("初查有", "已发现", "发现", "见有", "见到", "见")
CHECKED = ("已查", "已检查", "勘测记录", "现场勘测")

SUBJECTS = (
    ("湿陷性黄土", ("湿陷性黄土", "湿陷层", "湿陷")),
    ("盐渍", ("盐渍", "盐碱")),
    ("风积沙", ("风积沙", "风沙")),
)

DIRECTION_WORDS = (
    "以西", "以东", "以南", "以北", "向西", "向东", "向南", "向北", "往回",
)

BRIDGE_ANAPHORA = (
    "桥外", "桥东", "桥西", "桥南", "桥北", "桥两侧",
    "桥界面东", "桥界面西",
)


def split_sentences(text: str):
    """按句末标点切句，保留标点，去掉空白句。"""
    sentences = []
    for match in SENTENCE_END.finditer(text or ""):
        sentence = match.group(0)
        if sentence.strip():
            sentences.append(sentence)
    return sentences


def split_clauses(sentence: str):
    """按逗号、分号、冒号切分句内小句，保留小句原文。"""
    return [part for part in re.split(r"[，,；;：:]", sentence) if part.strip()]


def chainages(text: str):
    return CHAINAGE.findall(text or "")


def plain_ranges(text: str):
    """不带 K 的“100到300”这类范围，只在设计留档里使用。"""
    return [(float(a), float(b)) for a, b in PLAIN_RANGE.findall(text or "")]


def detect_line(text: str):
    found = []
    for name in LINE_NAMES:
        if name in (text or "") and name not in found:
            found.append(name)
    return found


def detect_version(text: str):
    """从“乙线 / 甲版 / 旧线 / 新线”等词面判断方案版本。

    “按旧线桩号解释”说的是换算方式，不是本记录的版本，不能据此改版。
    """
    text = text or ""
    if any(word in text for word in ("按旧线桩号", "旧线桩号", "旧桩号", "旧线里程")):
        return None
    if any(word in text for word in ("乙线", "乙版", "新线", "新方案", "新建")):
        return "乙"
    if any(word in text for word in ("甲线", "甲版", "旧线", "旧路线", "已建")):
        return "甲"
    return None


def detect_subject(text: str):
    for subject, words in SUBJECTS:
        if any(word in (text or "") for word in words):
            return subject
    return None


def has_any(text: str, words) -> bool:
    return any(word in (text or "") for word in words)


def detect_nature(text: str):
    """判断这句话在讲发现、推测、未覆盖还是范围限定。

    返回 (性质, 覆盖, 限定词列表)。性质只描述原句的证明程度：
    “未见”是有限范围内的反面发现，不是全段排除；“尚未调查”不是排除。
    """
    text = text or ""
    limits = [word for word in LIMIT if word in text]

    if has_any(text, LOCATING_BLOCK):
        return "定位障碍", "未定位", limits

    suspected = has_any(text, SUSPECT)
    uncovered = has_any(text, NEGATIVE_COVERAGE)
    if suspected and uncovered:
        return "推测（未测）", "未覆盖", limits
    if suspected:
        return "推测", "已观察（未确认）", limits
    if uncovered:
        return "未覆盖调查", "未覆盖", limits
    if has_any(text, NEGATIVE_FINDING):
        return "未见（有限范围）", "已覆盖（限本句范围）", limits
    if has_any(text, SCOPE_ONLY):
        return "范围限定", "仅本句写明范围", limits
    if has_any(text, CHECKED):
        return "已查（设计回复）", "已覆盖（待原始记录核对）", limits
    if has_any(text, FOUND):
        return "发现", "已覆盖（限本句范围）", limits
    return None, None, limits


def detect_method(text: str):
    """这句话依据的是试坑、初查、断面还是签核。

    条文里“试坑”和“横断面复核”是两项不同证据，不能互相顶替。
    “已试挖”只说明方法，结果要看同句后文，不能单独当作发现。
    """
    text = text or ""
    methods = []
    if any(word in text for word in ("试坑", "试挖", "探坑")):
        methods.append("试坑")
    if "初查" in text:
        methods.append("初查")
    if "断面" in text:
        methods.append("断面")
    if "勘测" in text:
        methods.append("勘测")
    if "签核" in text:
        methods.append("签核")
    if not methods and detect_nature(text)[0] in ("发现", "未见（有限范围）"):
        methods.append("文字结论")
    return methods


def detect_direction_word(text: str):
    for word in DIRECTION_WORDS:
        if word in (text or ""):
            return word
    return None


def parse_lateral(text: str):
    """识别横向范围，并标出原文没有说清的地方。

    返回 (laterals, notes)。laterals 用固定词；notes 说明未分侧、未说明等。
    """
    text = text or ""
    laterals = []
    notes = []

    north_half = "北半幅" in text
    south_half = "南半幅" in text
    if north_half and not south_half:
        laterals.append("主线北半幅")
    elif south_half and not north_half:
        laterals.append("主线南半幅")
    elif "主线" in text:
        laterals.extend(["主线北半幅", "主线南半幅"])

    if "北侧路肩" in text or "北肩" in text:
        laterals.append("北肩")
    if "南侧路肩" in text or "南肩" in text:
        laterals.append("南肩")
    if "全幅" in text:
        laterals.extend(LANE_UNITS)
    if "两端" in text and ("接合" in text or "衔接" in text or "接" in text):
        laterals.append("两端衔接")
    if "桥界面" in text:
        notes.append("只写“桥界面”，未点名桥梁")
    if "路肩" in text and "北肩" not in laterals and "南肩" not in laterals:
        if "东侧路肩" in text or "西侧路肩" in text:
            laterals.extend(["北肩", "南肩"])
            notes.append("只写了桥界面东/西路肩，未说明是北肩还是南肩")
        else:
            laterals.append("路肩（未分侧）")
            notes.append("只写“路肩”未分南北，两侧都要核对")
    if "未分主线和路肩" in text or "未分主线与路肩" in text:
        notes.append("原文明确未分主线和路肩，不能把结论单归到某一侧")
    if "局部" in text or "小段" in text:
        notes.append("原文只说局部/小段，不能按全图范围视为已覆盖")
    if "附近" in text:
        notes.append("原文只说“附近”，没有给出起止桩号")
    if not laterals and "横向" in text:
        notes.append("横向范围未说明")

    seen = set()
    laterals = [item for item in laterals if not (item in seen or seen.add(item))]
    return laterals, notes


def parse_excluded(text: str):
    """识别原文明确排除的区段。

    常见写法是“不能代表西侧”“本单未签北肩”。排除与“未调查”不同：
    未调查只是未知，不能当成排除。
    返回 (excluded_sides, excluded_units, notes)。
    """
    text = text or ""
    sides = []
    units = []
    notes = []
    for side in ("东", "西", "南", "北"):
        if f"不能代表{side}侧" in text or f"不得代表{side}侧" in text:
            sides.append(f"{side}侧")
    if "未签北肩" in text:
        units.append("北肩")
    if "未签南肩" in text:
        units.append("南肩")
    if "未签" in text and "北肩" not in text and "南肩" not in text:
        notes.append("有“未签”字样但未写清未签范围")
    if "未分主线和路肩" in text:
        notes.append("原文明确未分主线和路肩")
    return sides, units, notes


def direction_between(first: float, second: float):
    if second > first:
        return "由西向东（坐标增大）"
    if second < first:
        return "由东向西（坐标减小）"
    return "同一位置（坐标相同）"
