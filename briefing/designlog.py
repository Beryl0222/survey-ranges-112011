"""设计留档：送审、签核、回复及其实际覆盖范围。

设计回复只覆盖它实际引用的范围、版次和结论：
- “本单未签北肩”不是全幅签核；
- “按乙版北半幅签核”只签北半幅；
- “待断面回复”不算复核完成；
- “引用西段交底”不等于会签，也不把西段证据变成东段自己的证据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import scope
from . import text as T

_EVENT_KIND = {"送审": "送审", "签核": "签核", "回复": "回复"}

_PENDING_MARKERS = ("待断面回复", "待回复", "仍待", "未回复", "待确认")
_REFERENCE_MARKERS = ("引用", "采用", "按西段", "接合采用", "沿用")
_NOT_SIGNED_MARKERS = ("不表示会签", "不会签", "未会签", "不是会签")


@dataclass
class LogEntry:
    id: str
    日期: str
    图号: str
    事件: str
    内容: str
    性质: str
    版本: str | None
    低: float | None
    高: float | None
    单点: bool
    单元: list
    标段: list
    结论: str
    引用: list
    未签: list
    待办: list
    边界点: bool = False
    自述已查: list = field(default_factory=list)
    问题: list = field(default_factory=list)
    备注: list = field(default_factory=list)

    @property
    def 是签核(self):
        return self.事件 == "签核"

    @property
    def 是送审(self):
        return self.事件 == "送审"

    @property
    def 是回复(self):
        return self.事件 == "回复"

    @property
    def 已闭合(self):
        return not self.待办

    @property
    def 提到断面(self):
        return "断面" in (self.内容 or "")

    def as_dict(self):
        return {
            "编号": self.id,
            "日期": self.日期,
            "图号": self.图号,
            "事件": self.事件,
            "内容": self.内容,
            "性质": self.性质,
            "版本": self.版本,
            "低坐标": self.低,
            "高坐标": self.高,
            "单点": self.单点,
            "单元": self.单元,
            "标段": self.标段,
            "结论": self.结论,
            "引用": self.引用,
            "未签": self.未签,
            "待办": self.待办,
            "边界点": self.边界点,
            "自述已查": self.自述已查,
            "问题": self.问题,
            "备注": self.备注,
        }


class DesignLog:
    def __init__(self, corpus, locator):
        self.corpus = corpus
        self.locator = locator
        self.drawings = {row["id"]: row for row in corpus.rows("drawings")}
        self.entries = [self._entry(row) for row in corpus.rows("design_log")]
        self.by_id = {entry.id: entry for entry in self.entries}

    def for_drawing(self, drawing_id):
        return [entry for entry in self.entries if entry.图号 == drawing_id]

    # ---- 单条 -----------------------------------------------------------
    def _entry(self, row):
        text = row["内容"]
        drawing = self.drawings.get(row["图号"])
        line = drawing["线路"] if drawing else self._line_from_text(text)
        version = T.detect_version(text) or (drawing["版本"] if drawing else None)

        low = high = None
        single = False
        notes = []
        questions = []
        explicit = T.plain_ranges(text)
        located = self.locator.resolve_points(text, line, version) if line else None

        if located is not None and located.low is not None and (
            T.chainages(text) or self.locator.landmark_names_in(text)
        ):
            low, high = located.low, located.high
            notes.extend(located.notes)
            questions.extend(located.questions)
            single = located.point
            if single and not re.search(r"K\d|\d+\s*(?:至|到)", text):
                # 只提到地物、没有里程时，不能拿地物点当本单范围；
                # 范围仍按图面读取，地物只用于比对。
                topic = located.low
                low = high = None
                notes.append(
                    f"本单只提到位置 {topic}，未写起止；范围改按所属图面读取"
                )
        elif len(explicit) == 1:
            low, high = sorted(explicit[0])
        elif len(explicit) > 1:
            low, high = sorted(explicit[0])
            notes.append(f"本单出现 {len(explicit)} 组数字范围，只按第一组落位")

        # “旧线上K5+240”这类写法要核对旧线自己的桩号体系。
        for token in re.findall(r"旧线上?(K\d+\+\d+)", text):
            if line:
                candidates, cand_notes, cand_questions = self.locator.resolve_old_chainage(
                    token, line, "甲", version or "乙"
                )
                notes.extend(cand_notes)
                coords = {candidate.坐标 for candidate in candidates}
                if len(coords) == 1:
                    notes.append(
                        f"“旧线上{token}”按旧线与新线两种读法均落在 {coords.pop()}，"
                        "不改变本单范围"
                    )
                else:
                    questions.extend(cand_questions)

        if low is None and drawing is not None:
            drawing_scope = scope.parse_drawing_scope(drawing["范围"])
            low, high = drawing_scope.低, drawing_scope.高
            notes.append("本单未另写范围，按所属图面范围读取")

        units, unit_notes = self._units(text, drawing)
        notes.extend(unit_notes)

        not_signed, pending, references, claimed = [], [], [], []
        for clause in T.split_clauses(text):
            sides, shoulder_units, _ = T.parse_excluded(clause)
            not_signed.extend(sides)
            not_signed.extend(shoulder_units)
            if any(marker in clause for marker in _PENDING_MARKERS):
                pending.append(clause.strip("。；"))
            if any(marker in clause for marker in _REFERENCE_MARKERS) and "引用" in clause:
                references.append(clause.strip("。；"))
            if any(marker in clause for marker in _NOT_SIGNED_MARKERS):
                notes.append(f"原文明确不是会签：{clause.strip('。；')}")
            if T.has_any(clause, ("已查", "已检查")):
                claimed.append(clause.strip("。；"))

        # “两端接合已核”只签接合点，不代表线段内部已核。
        # “以新方案范围检查两端接合”只是检查方式，不是范围结论。
        boundary_only = (
            T.has_any(text, ("两端接合已核", "两端衔接已核", "两端线内"))
            and not T.has_any(text, ("全段", "全线", "逐段"))
        )
        if boundary_only:
            notes.append("本单只写两端接合，覆盖按接合点计，线段内部不在签核范围内")

        conclusion = self._conclusion(row["事件"], text, pending, not_signed)
        segments = []
        if line and low is not None and high is not None:
            segments = self.locator.segments_in_range(line, version, low, high)
            if drawing is not None:
                drawn = scope.parse_drawing_scope(drawing["范围"])
                clipped = scope.overlap(low, high, drawn.低, drawn.高)
                if clipped is None:
                    notes.append("本单范围与所属图面范围不相交，需核对图号")
                elif clipped != (low, high):
                    notes.append(
                        f"范围 {low}–{high} 超出图面 {drawn.低}–{drawn.高}，"
                        f"实际覆盖按 {clipped[0]}–{clipped[1]}"
                    )
            if "未分主线和路肩" in text:
                segments = self.locator.segments_in_range(line, version, low, high)

        return LogEntry(
            id=row["id"], 日期=row["日期"], 图号=row["图号"], 事件=row["事件"],
            内容=row["内容"], 性质=_EVENT_KIND.get(row["事件"], row["事件"]),
            版本=version, 低=low, 高=high, 单点=single or (low is not None and low == high),
            单元=units, 标段=segments, 结论=conclusion,
            引用=references, 未签=list(dict.fromkeys(not_signed)),
            待办=list(dict.fromkeys(pending)),
            边界点=boundary_only,
            自述已查=list(dict.fromkeys(claimed)),
            问题=list(dict.fromkeys(questions)),
            备注=list(dict.fromkeys(notes)),
        )

    def _line_from_text(self, text):
        names = T.detect_line(text)
        return names[0] if names else None

    def _units(self, text, drawing, entry_id=None):
        """签核/回复实际涉及的横向单元。

        只认本单写明的通道；“已建段维持甲版”只说版次延续，
        没有里程和横向，不能拿图面横向整体代入。
        """
        notes = []
        units = []

        if "北半幅" in text:
            units.append("主线北半幅")
        if "南半幅" in text:
            units.append("主线南半幅")
        if "北肩" in text:
            units.append("北肩")
        if "南肩" in text:
            units.append("南肩")

        if "维持" in text and drawing is not None:
            notes.append(
                "原文“维持甲版”只说明版次延续，没有写里程和横向；"
                "本条不据此扩大签核覆盖，需补该段起止"
            )
            notes.append(
                "“已建段维持甲版”对应的起止与横向未写，请补里程和通道；"
                "当前只按本单另点名的单元计算"
            )

        not_signed = T.parse_excluded(text)[1]
        for unit in not_signed:
            if unit in units:
                units.remove(unit)
            notes.append(f"本单明确未签 {unit}，不能按图面横向范围推定")

        if "未分主线和路肩" in text:
            units = list(scope.UNITS)
            notes.append("本单未分主线和路肩，横向覆盖按全部单元待确认")

        if not units and drawing is not None:
            drawn = scope.parse_drawing_scope(drawing["范围"])
            units = list(drawn.横向)
            notes.append("本单未另写横向，按所属图面横向范围读取")
        return list(dict.fromkeys(units)), notes

    def _conclusion(self, event, text, pending, not_signed):
        if event == "送审":
            return "送审记录（非签核）"
        if event == "签核":
            if pending:
                return "签核附待办"
            if not_signed:
                return "部分签核（有未签范围）"
            if "未分主线和路肩" in text:
                return "签核（横向未分，效果待定）"
            return "已签核（限本单写明范围）"
        if event == "回复":
            if pending:
                return "回复未闭合（待后续）"
            if any(marker in text for marker in _NOT_SIGNED_MARKERS):
                return "回复（明确不表示会签）"
            if not_signed:
                return "回复（部分范围）"
            return "回复（限本单引用范围）"
        return event

    # ---- 汇总 -----------------------------------------------------------
    def state_for(self, drawing_id):
        entries = sorted(
            self.for_drawing(drawing_id), key=lambda item: (item.日期, item.id)
        )
        signed, reviewed, pending = [], [], []
        for entry in entries:
            if entry.是签核 and entry.结论.startswith("已签核"):
                signed.append(entry.id)
            if entry.是回复 and entry.已闭合:
                reviewed.append(entry.id)
            pending.extend(f"{entry.id}：{item}" for item in entry.待办)
        return {
            "图号": drawing_id,
            "记录": [entry.as_dict() for entry in entries],
            "签核": signed,
            "复核回复": reviewed,
            "待确认": pending,
        }
