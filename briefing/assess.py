"""把事实、条款、设计留档对上，判断每张图当前能出到什么程度。

输出保留每一步的原文与编号，便于审查员按图或按线段追到原句。
判定只用材料能支持的部分：
- 条款按版次适用，甲版事实不套乙版条款，反之亦然；
- 调查（试坑/补查）与复核（同版次断面/共同会签）分别留痕，互不顶替；
- 设计回复只覆盖它实际引用的范围、版次和结论；
- 送审不是签核，引用不是会签，未调查不是排除。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import directives
from . import scope
from . import text as T
from .scope import UNITS

SURVEY_WORDS = ("试坑", "试挖", "探坑")
SALTY = "盐渍"
COLLAPSE = "湿陷性黄土"
SHOULDER = ("北肩", "南肩")


def union(intervals):
    """合并闭区间，供覆盖率判断。"""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [list(ordered[0])]
    for low, high in ordered[1:]:
        if low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return [(low, high) for low, high in merged]


def covers(intervals, low, high, epsilon=1e-9):
    """这组闭区间是否完整覆盖 [low, high]。"""
    for seg_low, seg_high in union(intervals):
        if seg_low - epsilon <= low and seg_high + epsilon >= high:
            return True
    return False


def uncovered(low, high, intervals):
    """[low, high] 中未被这组区间覆盖的部分。"""
    if low is None or high is None:
        return []
    return scope.subtract_ranges(low, high, union(intervals))


@dataclass
class Need:
    条款: str | None
    类别: str            # 调查 / 复核
    标的: str
    状态: str            # 满足 / 局部 / 缺 / 不适用
    单元: list = field(default_factory=list)
    证据: list = field(default_factory=list)
    覆盖: list = field(default_factory=list)
    未覆盖: list = field(default_factory=list)
    说明: str = ""

    def as_dict(self):
        return {
            "条款": self.条款,
            "类别": self.类别,
            "标的": self.标的,
            "状态": self.状态,
            "单元": self.单元,
            "证据": self.证据,
            "覆盖": [list(item) for item in self.覆盖],
            "未覆盖": [list(item) for item in self.未覆盖],
            "说明": self.说明,
        }


@dataclass
class Hold:
    原因: str
    条款: str | None
    低: float | None
    高: float | None
    单元: list
    说明: str
    类别: str = "补证"        # 补证 / 提醒 / 待定位 / 待会签
    来源: list = field(default_factory=list)
    范围未知: bool = False

    def as_dict(self):
        return {
            "原因": self.原因,
            "条款": self.条款,
            "低坐标": self.低,
            "高坐标": self.高,
            "单元": self.单元,
            "说明": self.说明,
            "类别": self.类别,
            "来源": self.来源,
            "范围未知": self.范围未知,
        }


@dataclass
class Trace:
    fact_id: str
    原文: str
    日期: str
    事实性质: str
    适用: bool
    不适用原因: str | None
    条款: list
    范围: tuple | None
    单元: list
    需求: list
    设计答复: list = field(default_factory=list)
    备注: list = field(default_factory=list)

    def as_dict(self):
        return {
            "事实": self.fact_id,
            "原文": self.原文,
            "日期": self.日期,
            "事实性质": self.事实性质,
            "适用": self.适用,
            "不适用原因": self.不适用原因,
            "条款": self.条款,
            "范围": list(self.范围) if self.范围 else None,
            "单元": self.单元,
            "需求": [need.as_dict() for need in self.需求],
            "设计答复": self.设计答复,
            "备注": self.备注,
        }


UNIT_ORDER = {"主线北半幅": 0, "主线南半幅": 1, "北肩": 2, "南肩": 3}


def _unit_order(unit):
    return UNIT_ORDER.get(unit, 99)


def _metres(value):
    return f"{value:g}"


def _covered_segments(coverage):
    """把覆盖区间合并，保留接合点这类零宽区间。"""
    points = [(low, high) for low, high, _, _ in coverage]
    merged = union([(low, high) for low, high in points if low < high])
    singles = {(low, high) for low, high in points if low == high}
    return merged + sorted(singles)


def _covered_units_across(coverage, low, high):
    units = set()
    for seg_low, seg_high, cov_units, _ in coverage:
        if seg_low == seg_high:
            continue
        if scope.overlap(seg_low, seg_high, low, high) is None:
            continue
        units.update(cov_units)
    return sorted(units, key=_unit_order)


def _subtract_all(segments, hold):
    remaining = []
    for low, high in segments:
        remaining.extend(scope.subtract_ranges(low, high, [(hold.低, hold.高)]))
    return remaining


class Assessment:
    def __init__(self, corpus, locator, builder, design):
        self.corpus = corpus
        self.locator = locator
        self.builder = builder
        self.design = design
        self.facts = builder.all_facts()
        self.drawings = {row["id"]: row for row in corpus.rows("drawings")}
        self.segments = {row["id"]: row for row in corpus.rows("segments")}
        self._scopes = {}
        self._traces = {}
        self._results = {}
        self._visiting = set()

    # ---- 小工具 ---------------------------------------------------------
    def drawing_scope(self, drawing_id):
        if drawing_id not in self._scopes:
            self._scopes[drawing_id] = scope.parse_drawing_scope(
                self.drawings[drawing_id]["范围"]
            )
        return self._scopes[drawing_id]

    def logs(self, drawing_id=None):
        entries = self.design.entries
        if drawing_id is not None:
            entries = [entry for entry in entries if entry.图号 == drawing_id]
        return sorted(entries, key=lambda entry: (entry.日期, entry.id))

    @staticmethod
    def _unit_overlap(drawing_units, fact_units):
        return [unit for unit in drawing_units if unit in fact_units]

    def _clipped_units(self, fact, drawing_scope):
        """事实的横向范围落到图面上：没写横向的事实按图面横向核对。"""
        if fact.单元:
            return self._unit_overlap(drawing_scope.横向, fact.单元)
        return list(drawing_scope.横向)

    # ---- 事实 -> 条款 ---------------------------------------------------
    def clauses_for(self, fact):
        """按版次、土质、性质挑出条文；不跨版次套用。"""
        ids = []
        if fact.版本 == "乙":
            # P1 只针对“已发现湿陷层的路肩设计”；未调查本身不触发该条。
            # 原句没写横向时不据此认定路肩已有该层，改由横向确认问题处理。
            # 只有原句明确写到路肩时才判 P1；横向没写的事实留给
            # needs_for 生成“横向范围确认”，不预先套用条款。
            if fact.土质 == COLLAPSE and fact.性质 == "发现" \
                    and any(unit in SHOULDER for unit in fact.横向):
                ids.append("P1")
            if fact.土质 == SALTY and fact.性质 in ("推测", "推测（未测）", "未覆盖调查"):
                ids.append("P2")
            if fact.边界点:
                ids.append("P3")
        elif fact.版本 == "甲":
            if fact.土质 == SALTY and fact.性质 in ("推测", "推测（未测）", "未覆盖调查"):
                ids.append("P4")
            if fact.边界点:
                ids.append("P3")
        if fact.跨标段:
            ids.append("P5")
        return ids

    def needs_for(self, fact, clause_ids, low, high, units):
        needs = []
        if "P1" in clause_ids:
            # P1 只管路肩设计；同一句里主线的事实不因横向未说明就套用路肩条款。
            shoulder_units = [unit for unit in units if unit in SHOULDER]
            if shoulder_units:
                needs.append(self._need_test_pit(fact, low, high, shoulder_units))
                needs.append(self._need_section(fact, low, high, shoulder_units))
        elif fact.土质 == COLLAPSE and fact.性质 == "发现" and not fact.横向:
            # 发现写了、横向没写：不能把路肩算作已发现，也不能算作已排除。
            # 横向未确认前，调查记录和断面复核都无法按通道核对。
            needs.append(Need(
                "P1", "调查", "横向范围确认", "缺", list(units), [], [],
                [(unit, low, high) for unit in units],
                说明=(
                    "原句只说发现湿陷层，未写主线/路肩；无法判定是否涉及路肩设计。"
                    "需补分幅记录后再决定是否适用 P1"
                ),
            ))
            needs.append(Need(
                "P1", "复核", "相符版次横断面复核", "缺", list(units), [], [],
                [(unit, low, high) for unit in units],
                说明="横向未确认，断面复核无从按通道核对；待横向补正后再提供同版次断面",
            ))
        if "P2" in clause_ids:
            needs.append(self._need_location(fact, low, high, units, "P2", drill=False))
        if "P4" in clause_ids:
            needs.append(self._need_preserved(fact))
            needs.append(self._need_location(fact, low, high, units, "P4", drill=True))
        if "P3" in clause_ids:
            needs.append(self._need_joint(fact))
        if "P5" in clause_ids:
            needs.append(self._need_shared(fact))
        return needs

    # ---- 各项证据 -------------------------------------------------------
    def _per_unit_status(self, fact, low, high, units, collected):
        """collected: {unit: [(low, high, label)]}"""
        statuses = {}
        for unit in units:
            intervals = [(a, b) for a, b, _ in collected.get(unit, [])]
            if covers(intervals, low, high):
                statuses[unit] = "满足"
            elif intervals:
                statuses[unit] = "局部"
            else:
                statuses[unit] = "缺"
        return statuses

    @staticmethod
    def _aggregate(statuses):
        values = set(statuses.values())
        if values == {"满足"}:
            return "满足"
        if "满足" in values or "局部" in values:
            return "局部"
        return "缺"

    def _collect_survey(self, fact, low, high, units, need_method=None):
        collected = {unit: [] for unit in units}
        labels = []
        for other in self.facts:
            if other.版本 != fact.版本 or other.土质 != COLLAPSE:
                continue
            if other.low is None or other.high is None:
                continue
            if other.性质 not in ("发现", "未见（有限范围）"):
                continue
            if need_method and not any(
                method in SURVEY_WORDS for method in other.方法
            ):
                continue
            overlap = scope.overlap(other.low, other.high, low, high)
            if overlap is None or overlap[1] <= overlap[0]:
                # 只在接合点相接不算覆盖本图区间。
                continue
            shared = self._unit_overlap(units, other.单元) if other.单元 else list(units)
            for unit in shared:
                collected[unit].append((
                    overlap[0], overlap[1],
                    f"{other.letter_id}（{other.小句.strip('。；')}）",
                ))
            if shared:
                labels.append(
                    f"{other.letter_id} 覆盖 {overlap[0]}–{overlap[1]} {shared}"
                )
        return collected, labels

    def _need_test_pit(self, fact, low, high, units):
        collected, labels = self._collect_survey(fact, low, high, units, SURVEY_WORDS)
        # 设计回复只提到“有试坑记录”时只能算线索，不能当原始结论。
        hints = []
        for entry in self.design.entries:
            if "试坑" in entry.内容 and entry.低 is not None:
                overlap = scope.overlap(entry.低, entry.高, low, high)
                if overlap is not None:
                    hints.append(f"{entry.id}（{entry.图号}）提到试坑记录")
        statuses = self._per_unit_status(fact, low, high, units, collected)
        status = self._aggregate(statuses)
        misses = []
        for unit, values in collected.items():
            for miss_low, miss_high in uncovered(
                low, high, [(a, b) for a, b, _ in values]
            ):
                misses.append((unit, miss_low, miss_high))
        if status == "满足":
            note = "试坑结论覆盖本图受影响范围与横向"
        elif status == "局部":
            note = "试坑只覆盖部分范围或部分通道，其余仍缺"
        else:
            note = "本范围内没有可核对的试坑结论"
        if hints:
            note += "；" + "、".join(hints) + " 只能证明存在该记录，不能代替原始结论"
        return Need("P1", "调查", "试坑记录", status, list(collected),
                    证据=labels + hints, 覆盖=[
                        (a, b) for values in collected.values() for a, b, _ in values
                    ], 未覆盖=misses, 说明=note)

    def _need_section(self, fact, low, high, units):
        collected = {unit: [] for unit in units}
        same, other_version, pending = [], [], []
        for entry in self.design.entries:
            if not entry.是回复 or not entry.提到断面:
                continue
            if entry.低 is None:
                continue
            overlap = scope.overlap(entry.低, entry.高, low, high)
            if overlap is None or overlap[1] <= overlap[0]:
                continue
            shared = self._unit_overlap(units, entry.单元)
            if not shared:
                continue
            label = f"{entry.id}（{entry.图号} {entry.日期}）"
            if not entry.已闭合:
                pending.append(label)
                continue
            if entry.版本 != fact.版本:
                other_version.append(f"{label} 版次为{entry.版本}")
                continue
            for unit in shared:
                collected[unit].append((overlap[0], overlap[1], label))
            same.append(f"{label} 覆盖 {overlap[0]}–{overlap[1]} {shared}")
        statuses = self._per_unit_status(fact, low, high, units, collected)
        status = self._aggregate(statuses)
        misses = []
        for unit, values in collected.items():
            for miss_low, miss_high in uncovered(
                low, high, [(a, b) for a, b, _ in values]
            ):
                misses.append((unit, miss_low, miss_high))
        if status == "满足":
            note = "有同版次且已闭合的断面复核回复，仍以回复写明范围为准"
        elif pending:
            note = "断面回复仍待，未闭合，不能签为通过"
        elif other_version:
            note = "有断面回复但版次不同，不能顶替本图"
        elif status == "局部":
            note = "断面回复只覆盖部分范围或通道"
        else:
            note = "没有同版次横断面复核回复"
        return Need("P1", "复核", "相符版次横断面复核", status, list(collected),
                    证据=same + other_version + pending, 覆盖=[
                        (a, b) for values in collected.values() for a, b, _ in values
                    ], 未覆盖=misses, 说明=note)

    def _need_location(self, fact, low, high, units, clause, drill=False):
        labels = []
        for entry in self.design.entries:
            if not entry.已闭合 or entry.低 is None:
                continue
            if scope.overlap(entry.低, entry.高, low, high) is None:
                continue
            if T.has_any(entry.内容, ("补勘", "补查", "定位")):
                labels.append(f"{entry.id}（{entry.图号}）")
        if labels:
            return Need(clause, "调查", "盐渍定位补勘", "满足", list(units),
                        labels, [], [],
                        说明="定位补查已闭合，仍按回复写明的范围引用")
        reason = "；".join(fact.定位问题) or "原文未给出可核位置"
        return Need(clause, "调查", "盐渍定位补勘", "缺", list(units), [], [],
                     [(unit, low, high) for unit in units],
                    说明=f"定位未知，须先补查；{reason}")

    def _need_preserved(self, fact):
        hits = [
            f"{entry.id}（{entry.图号}）"
            for entry in self.design.entries
            if entry.是签核 and entry.版本 == fact.版本
            and (entry.结论.startswith("已签核") or "原签核" in entry.内容)
        ]
        if hits:
            return Need("P4", "复核", "甲版原签核区间保留", "满足", [], hits, [], [],
                        说明="甲版已签区间继续有效，后续补勘只改其明确涉及的位置")
        return Need("P4", "复核", "甲版原签核区间保留", "缺", [], [], [], [],
                    说明="未找到同版次原签核记录")

    def _need_joint(self, fact):
        for entry in self.design.entries:
            if "会签" in entry.内容 and "不表示会签" not in entry.内容:
                return Need("P3", "复核", "相邻标段共同会签", "满足", [], [entry.id], [], [],
                            说明="已找到共同会签记录")
        return Need("P3", "复核", "相邻标段共同会签", "缺", [], [], [], [],
                    说明="接合处暂无共同会签；两边内段可继续审查，边界待共同确认")

    def _need_shared(self, fact):
        confirmations = []
        for link in self.corpus.rows("crosswalk"):
            if fact.线路 in (link["甲线路"], link["乙线路"]):
                confirmations.append(f"跨表 {link['id']}：{link['交接']}")
        if confirmations:
            return Need("P5", "复核", "共享构筑物引用边界", "局部", [], confirmations,
                        [], [],
                        说明="交接表只连坐标，未写构筑物编号；几何与编号须先核实，"
                             "试坑的横向覆盖仍回勘察案卷核对")
        return Need("P5", "复核", "共享构筑物引用边界", "缺", [], [], [],
                     [(unit, None, None) for unit in UNITS],
                    说明="没有可用的构筑物交接记录")

    # ---- 图面判定 -------------------------------------------------------
    def trace_for(self, drawing_id):
        if drawing_id in self._traces:
            return self._traces[drawing_id]
        drawing = self.drawings[drawing_id]
        drawing_scope = self.drawing_scope(drawing_id)
        traces = []
        for fact in self.facts:
            trace = self._trace_fact(drawing, drawing_scope, fact)
            if trace is not None:
                traces.append(trace)
        self._traces[drawing_id] = traces
        return traces

    def _design_replies(self, drawing, drawing_scope, low, high, units):
        """与这段事实相交的设计留档，按日期列出，供一步步追到答复。

        只列同图或同线路同版次的留档；其他线路的接合处理与本段无关。
        """
        replies = []
        if low is None or high is None:
            return replies
        for entry in self.design.entries:
            if entry.低 is None or entry.高 is None:
                continue
            if scope.overlap(entry.低, entry.高, low, high) is None:
                continue
            entry_drawing = self.drawings.get(entry.图号)
            if entry.图号 != drawing["id"]:
                if entry_drawing is None:
                    continue
                if entry_drawing["线路"] != drawing["线路"]:
                    continue
                if (entry.版本 or entry_drawing["版本"]) != drawing["版本"]:
                    continue
                # 本图的答复要覆盖本图写明的通道；只在别的通道上的留档不算。
                if not set(drawing_scope.横向) & set(entry.单元):
                    continue
            replies.append({
                "编号": entry.id,
                "日期": entry.日期,
                "图号": entry.图号,
                "事件": entry.事件,
                "结论": entry.结论,
                "原文": entry.内容,
                "是否本图": entry.图号 == drawing["id"],
            })
        return sorted(replies, key=lambda row: (row["日期"], row["编号"]))

    def _trace_fact(self, drawing, drawing_scope, fact):
        if fact.low is None:
            # 落不到桩号的事实也留在视图里，但要写清它为什么不能参与比对。
            related = fact.线路 == drawing["线路"]
            if fact.线路 is None:
                related = any(
                    candidate["线路"] == drawing["线路"]
                    and drawing_scope.低 <= candidate["坐标"] <= drawing_scope.高
                    for candidate in fact.候选
                )
            if not related:
                return None
            if fact.性质 is None and not fact.单元:
                return None
            reason = "原句没有可定位的起止，无法与图面比对"
            if fact.线路 is None:
                reason = (
                    "原句没有写明线路与起止；候选落在本图范围，"
                    "但在确认归属前不能算作本图证据"
                )
            return Trace(
                fact.letter_id, fact.小句, fact.日期, fact.性质 or "",
                False, reason, [], None, list(fact.单元), [],
                [], list(fact.定位问题),
            )
        if fact.线路 != drawing["线路"]:
            return None
        intersects = False
        if fact.low is not None:
            clipped = scope.overlap(fact.low, fact.high, drawing_scope.低, drawing_scope.高)
            # 只有端点相接的区间不算“覆盖到本图”，除非事实本身是单点。
            if clipped is not None and (
                clipped[1] > clipped[0] or fact.low == fact.high
            ):
                intersects = True
        if not intersects:
            return None

        units = self._clipped_units(fact, drawing_scope)
        if fact.版本 != drawing["版本"]:
            return Trace(
                fact.letter_id, fact.小句, fact.日期, fact.性质 or "",
                False,
                (f"事实为{fact.版本}版，图为{drawing['版本']}版；"
                 "甲、乙版分别承担已建与拟建区段，补勘所得不能一律顺延"),
                [], None, units, [],
                self._design_replies(drawing, drawing_scope, fact.low, fact.high, units),
                ["不套用本图条款；如需引用，须先按对应方案确认适用边界"],
            )
        if not units:
            return Trace(
                fact.letter_id, fact.小句, fact.日期, fact.性质 or "",
                False, "事实写明的横向范围与本图不相交",
                [], None, [], [],
            )

        # 条款和证据只按本图实际覆盖的那一段判断：事实写到图外的一端
        # 不能把本图的缺口放大到图外，也不能把图外的证据算成本图已覆盖。
        clipped = scope.overlap(fact.low, fact.high, drawing_scope.低, drawing_scope.高)
        low, high = clipped if clipped is not None else (fact.low, fact.high)
        clause_ids = self.clauses_for(fact)
        needs = self.needs_for(fact, clause_ids, low, high, units)
        notes = list(fact.定位问题)
        if (low, high) != (fact.low, fact.high):
            notes.append(
                f"原句范围 {fact.low}–{fact.high}，本图只取相交段 {low}–{high}"
            )
        if fact.性质 in ("未覆盖调查", "推测（未测）", "定位障碍"):
            notes.append(f"本句只说明{fact.性质}，不能作为通过依据")
        if fact.边界点:
            notes.append("仅两端衔接，线段内部未覆盖")
        if fact.排除:
            notes.append("原文排除：" + "、".join(fact.排除))
        return Trace(
            fact.letter_id, fact.小句, fact.日期, fact.性质 or "",
            True, None, clause_ids, (low, high), units, needs,
            self._design_replies(drawing, drawing_scope, low, high, units), notes,
        )

    def drawing(self, drawing_id):
        if drawing_id in self._results:
            return self._results[drawing_id]
        if drawing_id in self._visiting:
            # 依赖成环时保留一个占位结果，避免无限递归；真实数据里没有环。
            return {
                "图号": drawing_id, "状态": "待确认",
                "摘要": "图面依赖成环，需人工拆分依赖关系",
                "挂起": [], "需求": [], "事实": [], "签核覆盖": [],
            }
        self._visiting.add(drawing_id)
        drawing = self.drawings[drawing_id]
        drawing_scope = self.drawing_scope(drawing_id)
        traces = self.trace_for(drawing_id)
        applies = [trace for trace in traces if trace.适用]
        logs = self.logs(drawing_id)

        needs = [need for trace in applies for need in trace.需求]
        holds = []
        for trace in applies:
            if not trace.需求 and trace.事实性质 in ("发现", "未见（有限范围）") \
                    and not trace.条款:
                # 条款未覆盖事实，但图面要的是“有没有证据”，这里不额外提高要求，
                # 只把事实留在追踪里，由审查员判断是否提醒。
                pass
            for need in trace.需求:
                if need.状态 == "满足":
                    continue
                if need.状态 == "局部":
                    misses = need.未覆盖 or [
                        (unit, *(trace.范围 or (None, None)))
                        for unit in (need.单元 or trace.单元)
                    ]
                    for unit, low, high in misses:
                        if low is None or high is None:
                            continue
                        holds.append(Hold(
                            原因=f"{need.类别}证据不足（局部）：{need.标的}",
                            条款=need.条款, 低=low, 高=high,
                            单元=[unit], 说明=need.说明,
                            类别="补证", 来源=[trace.fact_id],
                        ))
                    continue
                holds.append(Hold(
                    原因=f"{need.类别}证据不足：{need.标的}",
                    条款=need.条款,
                    低=trace.范围[0] if trace.范围 else None,
                    高=trace.范围[1] if trace.范围 else None,
                    单元=list(need.单元 or trace.单元), 说明=need.说明,
                    类别="待定位" if (
                        need.标的.startswith("盐渍") or "确认" in need.标的
                    ) else "补证",
                    来源=[trace.fact_id],
                    范围未知=any("附近" in note or "范围未给出" in note
                             for note in trace.备注),
                ))
            if trace.事实性质 in ("未覆盖调查", "推测（未测）", "定位障碍"):
                holds.append(Hold(
                    原因=f"原文{trace.事实性质}",
                    条款=trace.条款[0] if trace.条款 else None,
                    低=trace.范围[0] if trace.范围 else None,
                    高=trace.范围[1] if trace.范围 else None,
                    单元=list(trace.单元),
                    说明="未调查或未测不等于排除，需先补查再判断",
                    类别="待定位" if trace.事实性质 == "定位障碍" else "补证",
                    来源=[trace.fact_id],
                    范围未知=any("附近" in note or "范围未给出" in note
                             for note in trace.备注),
                ))

        signed = [entry for entry in logs if entry.是签核]
        sent = [entry for entry in logs if entry.是送审]

        coverage = self._signed_coverage(drawing_id, drawing_scope, signed)
        holds.extend(self._boundary_only_holds(drawing, drawing_scope))
        for change in self._post_survey_changes(drawing_id, drawing_scope, coverage):
            holds.append(change)
        holds.extend(self._dependency_holds(drawing_id, drawing_scope))
        for entry in logs:
            for question in entry.问题:
                holds.append(Hold(
                    原因=f"设计留档定位问题（{entry.id}）",
                    条款=None,
                    低=entry.低, 高=entry.高, 单元=list(entry.单元),
                    说明=question,
                    类别="待定位" if entry.是签核 else "提醒",
                    来源=[entry.id],
                ))
        holds = self._dedupe_holds(holds)

        status = self._status(drawing, drawing_scope, logs, holds, sent, signed, coverage)
        history = self._history(drawing_id, drawing_scope)
        submitted = self._submission_relations(drawing_id, drawing_scope, sent)
        edges = self._edge_states(drawing, drawing_scope)
        shared = self.shared_structures(drawing_id)

        result = {
            "图号": drawing_id,
            "标段": drawing["标段"],
            "线路": drawing["线路"],
            "版本": drawing["版本"],
            "范围原文": drawing["范围"],
            "图面": drawing_scope.as_dict(),
            "状态": status["状态"],
            "摘要": status["摘要"],
            "签核覆盖": [
                {"低坐标": low, "高坐标": high, "单元": units, "来源": source}
                for low, high, units, source in coverage
            ],
            "事实": [trace.as_dict() for trace in traces],
            "需求": [need.as_dict() for need in needs],
            "挂起": [hold.as_dict() for hold in holds],
            "签核": [entry.as_dict() for entry in signed],
            "送审": [entry.as_dict() for entry in sent],
            "送审关系": submitted,
            "历史": history,
            "边界": edges,
            "共享构筑物": shared,
            "记录": [entry.as_dict() for entry in logs],
            "依赖": list(drawing["依赖"]),
        }
        self._results[drawing_id] = result
        self._visiting.discard(drawing_id)
        return result

    def _boundary_only_holds(self, drawing, drawing_scope):
        """交底只核两端衔接时，线内调查不能算已覆盖。"""
        holds = []
        for fact in self.facts:
            if not fact.边界点 or fact.线路 != drawing["线路"]:
                continue
            if fact.版本 != drawing["版本"]:
                continue
            holds.append(Hold(
                原因=f"交底只核两端衔接（{fact.letter_id}）",
                条款="P1" if drawing["版本"] == "乙" else "P4",
                低=drawing_scope.低, 高=drawing_scope.高,
                单元=list(drawing_scope.横向),
                说明=(
                    f"原句只覆盖接合点，线内调查不在其内；"
                    f"不能把两端已核当成整段已核。原句：{fact.小句}"
                 ),
                类别="补证", 来源=[fact.letter_id],
            ))
        return holds

    # ---- 历史与接合 -----------------------------------------------------
    def _history(self, drawing_id, drawing_scope):
        """每次送审时可见的材料快照。

        案卷按收到顺序保存，没有版本树；这里只按日期列出当时已有的
        交底与留档，并注明“当时材料原样”，不回溯改写。
        """
        logs = self.logs(drawing_id)
        snapshot = []
        for entry in logs:
            if not entry.是送审:
                continue
            visible_letters = [
                {
                    "编号": fact.letter_id,
                    "日期": fact.日期,
                    "版本": fact.版本,
                    "原句": fact.小句,
                    "与本图同版": fact.版本 == self.drawings[drawing_id]["版本"],
                }
                for fact in self.facts
                if fact.日期 <= entry.日期
                and fact.线路 == self.drawings[drawing_id]["线路"]
            ]
            visible_logs = [
                {"编号": other.id, "事件": other.事件, "日期": other.日期,
                 "内容": other.内容}
                for other in logs if other.日期 <= entry.日期
            ]
            snapshot.append({
                "送审": {"编号": entry.id, "日期": entry.日期, "内容": entry.内容},
                "当时已见交底": visible_letters,
                "当时已见留档": visible_logs,
            })
        return snapshot

    def _submission_relations(self, drawing_id, drawing_scope, sent):
        """送审图与后续勘察的关系：哪些内容变了、哪些未变。"""
        relations = []
        for entry in sent:
            low = entry.低 if entry.低 is not None else drawing_scope.低
            high = entry.高 if entry.高 is not None else drawing_scope.高
            changes = []
            for fact in self.facts:
                if fact.日期 <= entry.日期:
                    continue
                if fact.线路 != self.drawings[drawing_id]["线路"]:
                    continue
                if fact.版本 != self.drawings[drawing_id]["版本"]:
                    continue
                if fact.low is None:
                    continue
                overlap = scope.overlap(fact.low, fact.high, low, high)
                if overlap is None:
                    continue
                if fact.性质 not in (
                    "发现", "未覆盖调查", "推测（未测）", "定位障碍", "推测"
                ):
                    continue
                changes.append({
                    "编号": fact.letter_id,
                    "日期": fact.日期,
                    "原句": fact.小句,
                    "性质": fact.性质,
                    "影响范围": list(overlap),
                    "影响单元": sorted(
                        set(fact.单元) & set(self._clipped_units(fact, drawing_scope)),
                        key=_unit_order,
                    ),
                })
            relations.append({
                "送审": entry.id,
                "日期": entry.日期,
                "送审范围": [low, high],
                "后续改变": changes,
                "关系": (
                    "送审后勘察有改变，受影响部分应转回待确认"
                    if changes else "送审后未见改变本单范围的勘察记录"
                ),
            })
        return relations

    def _edge_states(self, drawing, drawing_scope):
        states = []
        for row in self.boundaries():
            if row["线路"] != drawing["线路"]:
                continue
            if row["坐标"] not in (drawing_scope.低, drawing_scope.高):
                continue
            junction = self.junction(row["线路"], row["坐标"])
            states.append({
                "坐标": row["坐标"],
                "相邻标段": row["标段"],
                "状态": junction["状态"],
                "说明": junction["说明"],
            })
        return states

    # ---- 签核覆盖 -------------------------------------------------------
    def _signed_coverage(self, drawing_id, drawing_scope, signed):
        """签核实际覆盖的区间与单元。

        “两端接合已核”只覆盖交界点；“本单未签北肩”扣掉北肩；
        “按乙版北半幅”只覆盖主线北半幅。约束不到的单元不算已覆盖。
        """
        coverage = []
        for entry in signed:
            if entry.低 is None or entry.高 is None:
                continue
            clipped = scope.overlap(entry.低, entry.高, drawing_scope.低, drawing_scope.高)
            if clipped is None:
                continue
            if entry.边界点:
                # “两端接合已核”只覆盖交界点：用零宽区间的两端各记一处。
                coverage.append((clipped[0], clipped[0], list(entry.单元),
                                 f"{entry.id}（仅接合点 {clipped[0]}）"))
                coverage.append((clipped[1], clipped[1], list(entry.单元),
                                 f"{entry.id}（仅接合点 {clipped[1]}）"))
                continue
            if entry.结论.startswith("已签核"):
                coverage.append((clipped[0], clipped[1], list(entry.单元), entry.id))
            elif entry.结论.startswith("部分签核"):
                coverage.append((clipped[0], clipped[1], list(entry.单元),
                                 f"{entry.id}（部分）"))
            # 横向未分的签核只作提醒，不算覆盖任何单元。
        return coverage

    def _covered_units_at(self, coverage, low, high):
        units = []
        for seg_low, seg_high, cov_units, _ in coverage:
            if scope.overlap(seg_low, seg_high, low, high) is None:
                continue
            units.extend(cov_units)
        return sorted(set(units))

    def _post_survey_changes(self, drawing_id, drawing_scope, coverage):
        if not coverage:
            return []
        first_signed = min(
            entry.日期 for entry in self.logs(drawing_id) if entry.是签核
        )
        drawing = self.drawings[drawing_id]
        holds = []
        for fact in self.facts:
            if fact.日期 <= first_signed:
                continue
            if fact.线路 != drawing["线路"] or fact.版本 != drawing["版本"]:
                continue
            if fact.low is None:
                continue
            overlap = scope.overlap(fact.low, fact.high, drawing_scope.低, drawing_scope.高)
            if overlap is None:
                continue
            units = self._clipped_units(fact, drawing_scope)
            affected = [
                unit for unit in units
                if unit in self._covered_units_at(coverage, overlap[0], overlap[1])
            ]
            if not affected:
                continue
            if fact.性质 not in (
                "发现", "未覆盖调查", "推测（未测）", "定位障碍", "推测"
            ):
                continue
            holds.append(Hold(
                原因=f"签核后补充勘察（{fact.letter_id} {fact.日期}）",
                条款=(self.clauses_for(fact) or [None])[0],
                低=overlap[0], 高=overlap[1], 单元=affected,
                说明=(
                    f"该补充记录晚于原签核；只把受影响部分转回待确认，"
                    f"其余区段签核保持有效。原句：{fact.小句}"
                 ),
                类别="补证" if fact.性质 != "定位障碍" else "待定位",
                来源=[fact.letter_id],
                范围未知=fact.附近,
            ))
        return holds

    def _dependency_holds(self, drawing_id, drawing_scope):
        holds = []
        for upstream_id in self.drawings[drawing_id]["依赖"]:
            if upstream_id not in self.drawings:
                continue
            upstream = self.drawing(upstream_id)
            if upstream["状态"] in ("待确认", "挂起", "部分待确认"):
                reasons = [
                    f"{hold['原因']}（{hold['低坐标']}–{hold['高坐标']}）"
                    for hold in upstream["挂起"]
                    if hold["类别"] in ("补证", "待定位", "待会签")
                ]
                detail = "；上游未闭合事项：" + "、".join(reasons[:4]) if reasons else ""
                holds.append(Hold(
                    原因=f"依赖上游图 {upstream_id} 未闭合",
                    条款=None, 低=drawing_scope.低, 高=drawing_scope.高,
                    单元=list(drawing_scope.横向),
                    说明=(f"{upstream_id} 当前状态：{upstream['状态']}；"
                         "本图依赖列表列出该图，图面断面引用应以该图闭合为前提"
                         f"{detail}"),
                    类别="提醒", 来源=[upstream_id],
                ))
        return holds

    @staticmethod
    def _dedupe_holds(holds):
        seen = {}
        for hold in holds:
            key = (hold.原因, hold.条款, hold.低, hold.高,
                   tuple(hold.单元), hold.类别, hold.说明)
            seen.setdefault(key, hold)
        return list(seen.values())

    def _status(self, drawing, drawing_scope, logs, holds, sent, signed, coverage):
        blocking = [hold for hold in holds if hold.类别 in ("补证", "待定位", "待会签")]
        reminder = [hold for hold in holds if hold.类别 == "提醒"]

        if not signed:
            if blocking:
                return {"状态": "挂起", "摘要": "未签核；存在未闭合的补证或定位问题"}
            if sent:
                return {"状态": "待确认", "摘要": "仅送审，尚未签核"}
            return {"状态": "待确认", "摘要": "未送审、未签核"}

        # 签核覆盖到哪些区间与单元。
        full = False
        for low, high in _covered_segments(coverage):
            if low <= drawing_scope.低 and high >= drawing_scope.高 \
                    and set(drawing_scope.横向) <= set(_covered_units_across(
                        coverage, drawing_scope.低, drawing_scope.高)):
                full = True
        if blocking and all("两端衔接" in hold.原因 for hold in blocking) \
                and not any(hold.原因.startswith(("调查", "复核")) for hold in blocking):
            return {
                "状态": "部分待确认",
                "摘要": "仅两端接合已核，线内调查仍待确认",
            }
        if blocking:
            unknown = [hold for hold in blocking if hold.范围未知]
            preserved = [] if unknown else self._preserved(
                drawing_scope, coverage, blocking
            )
            summary = "部分待确认"
            if unknown:
                summary += "；有事项只说“附近”，影响范围未定，暂不能划出仍有效的签核区间"
            if preserved:
                summary += "；其余签核仍有效：" + "、".join(
                    f"{_metres(low)}–{_metres(high)}（{'、'.join(units)}）"
                    for low, high, units in preserved
                )
            elif not unknown:
                summary += "；受影响范围已覆盖整幅图面，没有可保留的签核区间"
                if len(blocking) <= 2:
                    summary += "（" + "、".join(hold.原因 for hold in blocking) + "）"
            return {"状态": "部分待确认", "摘要": summary}
        if reminder:
            return {"状态": "已签核（附提醒）",
                    "摘要": "；".join(hold.原因 for hold in reminder)}
        if not full:
            covered = []
            for low, high in _covered_segments(coverage):
                units = _covered_units_across(coverage, low, high) if low < high else []
                label = f"{_metres(low)}–{_metres(high)}"
                if units:
                    label += f"（{'、'.join(units)}）"
                covered.append(label)
            detail = "；已签范围：" + "、".join(covered) if covered else "；没有实际签核范围"
            return {
                "状态": "部分待确认",
                "摘要": "签核未覆盖整幅图面，未覆盖部分待确认" + detail,
            }
        return {"状态": "已签核有效", "摘要": "签核覆盖图面范围，未发现未闭合的补证事项"}

    @staticmethod
    def _preserved(drawing_scope, coverage, blocking):
        """扣除待确认区间后仍有效的签核区间，按单元分别计算。

        只从实际签核覆盖里扣，不从整幅图面倒推：没有签核过的区段
        不能因为“没被挂起”就算有效。
        """
        result = []
        for seg_low, seg_high, units, _ in coverage:
            for unit in units:
                remaining = [(seg_low, seg_high)]
                for hold in blocking:
                    if hold.低 is None or hold.高 is None:
                        continue
                    if hold.单元 and unit not in hold.单元:
                        continue
                    remaining = scope.subtract_ranges(
                        remaining[0][0], remaining[0][1], [(hold.低, hold.高)]
                    ) if len(remaining) == 1 else _subtract_all(remaining, hold)
                for low, high in remaining:
                    result.append((low, high, [unit]))
        merged = {}
        for low, high, units in result:
            key = (low, high)
            merged.setdefault(key, set()).update(units)
        return [
            (low, high, sorted(units, key=_unit_order))
            for (low, high), units in sorted(merged.items())
        ]

    # ---- 汇总视图 -------------------------------------------------------
    def overview(self):
        rows = []
        for drawing_id, drawing in self.drawings.items():
            result = self.drawing(drawing_id)
            counts = {"补证": 0, "待定位": 0, "待会签": 0, "提醒": 0}
            for hold in result["挂起"]:
                counts[hold["类别"]] = counts.get(hold["类别"], 0) + 1
            rows.append({
                "图号": drawing_id,
                "标段": drawing["标段"],
                "线路": drawing["线路"],
                "版本": drawing["版本"],
                "范围": drawing["范围"],
                "状态": result["状态"],
                "摘要": result["摘要"],
                **counts,
            })
        return rows

    def questions(self):
        items = []
        for fact in self.facts:
            for question in fact.定位问题:
                items.append({
                    "来源": fact.letter_id,
                    "原句": fact.小句,
                    "线路": fact.线路,
                    "版本": fact.版本,
                    "问题": question,
                    "候选": list(fact.候选),
                })
        for entry in self.design.entries:
            for question in entry.问题:
                items.append({
                    "来源": entry.id,
                    "原句": entry.内容,
                    "线路": self.drawings.get(entry.图号, {}).get("线路"),
                    "版本": entry.版本,
                    "问题": question,
                    "候选": [],
                })
        return items

    def junction(self, line, metres):
        near = [
            fact for fact in self.facts
            if fact.线路 == line and fact.low is not None
            and fact.low <= metres <= fact.high
        ]
        # 同名桥使“桥界面东侧”这类句子落不到线路和桩号上，但它显然
        # 与本接合点有关；单列出来请人确认，不并进任何一边的证据。
        bridge_names = {
            name for name, rows in {
                name: [row for row in self.corpus.rows("landmarks")
                       if row["道路坐标"] == metres]
                for name in {row["名称"] for row in self.corpus.rows("landmarks")}
            }.items() if rows
        }
        unresolved = [
            fact for fact in self.facts
            if fact.low is None
            and (fact.线路 is None or fact.线路 == line)
            and (
                any(name in fact.句子 for name in bridge_names)
                or "桥" in fact.句子
                or any(candidate["坐标"] == metres for candidate in fact.候选)
            )
        ]
        segments = [
            row["id"] for row in self.corpus.rows("segments")
            if row["线路"] == line and row["起米"] <= metres <= row["止米"]
        ]
        segment_detail = [
            {"标段": row["id"], "版本": row["版本"], "起米": row["起米"],
             "止米": row["止米"]}
            for row in self.corpus.rows("segments")
            if row["线路"] == line and row["起米"] <= metres <= row["止米"]
        ]
        conflict = self._junction_conflict(near)
        joint = any(
            "会签" in entry.内容 and "不表示会签" not in entry.内容
            for entry in self.design.entries
        )
        blocking_unresolved = bool(unresolved) and not joint
        if conflict and not joint:
            state = "待共同确认"
            explanation = "两边可继续审查各自内段；仅边界接合处等共同确认"
        elif blocking_unresolved:
            state = "边界证据归属未定"
            explanation = (
                "有交底原句提到桥界面但落不到线路与桩号，不能算作任一标段"
                "已完成的边界证据；待确认归属后再判断是否需要共同会签"
            )
        else:
            state = "无需共同确认"
            explanation = "边界接合处无冲突证据"
        return {
            "线路": line,
            "坐标": metres,
            "标段": segments,
            "标段详情": segment_detail,
            "相关事实": [
                {"编号": fact.letter_id, "日期": fact.日期, "原句": fact.小句,
                 "性质": fact.性质, "单元": fact.单元}
                for fact in near
            ],
            "待确认归属": [
                {"编号": fact.letter_id, "日期": fact.日期, "原句": fact.小句,
                 "性质": fact.性质, "问题": fact.定位问题}
                for fact in unresolved
            ],
            "冲突": conflict,
            "共同会签": joint,
            "状态": state,
            "说明": explanation,
        }

    @staticmethod
    def _junction_conflict(facts):
        findings = {}
        for fact in facts:
            for unit in fact.单元 or ["未分"]:
                findings.setdefault(unit, set()).add(fact.性质)
        conflicts = []
        for unit, natures in findings.items():
            found = any(nature in ("发现", "未覆盖调查") for nature in natures)
            not_found = "未见（有限范围）" in natures
            if found and not_found:
                conflicts.append(f"{unit}：一边发现或未调查，一边未见")
        return conflicts

    def shared_structures(self, drawing_id=None):
        """跨方案共用的构筑物：只允许引用已核实几何与编号。

        交接表若只连坐标，就只是“可能同一座”；不据此推断土质结论的
        横向覆盖，也不把旧版签核顺延到新版。
        """
        rows = []
        for link in self.corpus.rows("crosswalk"):
            if drawing_id is not None and self.drawings[drawing_id]["线路"] not in (
                link["甲线路"], link["乙线路"]
            ):
                continue
            confirmed = self._structure_confirmed(link)
            kind = self._shared_kind(link)
            if kind != "构筑物":
                rows.append({
                    "交接": link["id"],
                    "对象类型": kind,
                    "甲": {"线路": link["甲线路"], "坐标": link["甲坐标"]},
                    "乙": {"线路": link["乙线路"], "坐标": link["乙坐标"]},
                    "原文": link["交接"],
                    "几何与编号已核实": None,
                    "可引用": False,
                    "说明": (
                        "交接表写的是共用位置标志，不是可承载土质结论的构筑物；"
                        "P5 的几何/编号引用不适用，位置相近也不等于横向覆盖相同"
                    ),
                })
                continue
            rows.append({
                "交接": link["id"],
                "对象类型": kind,
                "甲": {"线路": link["甲线路"], "坐标": link["甲坐标"]},
                "乙": {"线路": link["乙线路"], "坐标": link["乙坐标"]},
                "原文": link["交接"],
                "几何与编号已核实": confirmed,
                "可引用": confirmed,
                "说明": (
                    "交接表写明了几何与编号，可引用这两项；"
                    "新的适用边界仍按对应方案判断"
                    if confirmed else
                    "交接表只连坐标，未写构筑物编号或几何；只能作为候选，"
                    "需先核实后才能引用，试坑结论的横向覆盖仍回勘察案卷核对"
                ),
            })
        return rows

    @staticmethod
    def _shared_kind(link):
        """交接对象是构筑物还是位置标志。"""
        text = link["交接"]
        if "桥" in text:
            return "构筑物"
        if any(word in text for word in ("界石", "桩", "标志")):
            return "位置标志"
        return "未说明"

    def _structure_confirmed(self, link):
        """交接表本身是否写明了几何与编号。

        资料里没有单独的构筑物表；这里只认交接原文明确给出的编号或几何，
        不从“同一坐标”反推。
        """
        text = link["交接"]
        has_id = any(word in text for word in ("编号", "桥号", "构筑物号"))
        has_geometry = any(word in text for word in ("跨径", "孔", "几何", "尺寸"))
        return has_id and has_geometry

    def boundaries(self):
        rows = []
        for segment in self.corpus.rows("segments"):
            for other in self.corpus.rows("segments"):
                if other["id"] <= segment["id"]:
                    continue
                if other["线路"] != segment["线路"]:
                    continue
                if segment["止米"] != other["起米"]:
                    continue
                rows.append({
                    "线路": segment["线路"],
                    "版本": segment["版本"],
                    "坐标": segment["止米"],
                    "标段": [segment["id"], other["id"]],
                    "会签": self.junction(segment["线路"], segment["止米"])["状态"],
                })
        return rows

    def integrity(self):
        warnings = directives.check_integrity(self.corpus)
        for fact in self.facts:
            if fact.附近 and not fact.定位问题:
                warnings.append(f"{fact.letter_id} “附近”未给出范围")
        return warnings
