"""从交底函和补录里拆出可以定位的事实。

一句话里可能同时有范围、横向、土质、发现还是未调查，也可能几个小句
共用前面给出的起止。这里按句内小句顺序归并：
- 范围只在句内承前，不跨句传递；
- “桥外/桥东”这类词把同句的桥当作背景端点，并注明该端点是回指来的；
- “不能代表西侧”“本单未签北肩”作为排除单独记录，与“未调查”区分开；
- 方法（试坑/初查/断面/签核）随句内小句继承。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from . import scope
from . import text as T


@dataclass
class Fact:
    letter_id: str
    日期: str
    角色: str
    函版本: str
    抬头: str
    句子: str
    小句: str
    线路: str | None
    版本: str
    起坐标: float | None
    止坐标: float | None
    单点: bool
    书写顺序: list
    横向: list
    单元: list
    土质: str | None
    性质: str | None
    覆盖: str | None
    方法: list
    限定词: list
    排除: list
    标段: list
    跨标段: list
    候选: list
    定位问题: list
    备注: list = field(default_factory=list)
    引用: list = field(default_factory=list)
    断链: bool = False
    旧桩号: bool = False
    附近: bool = False
    边界点: bool = False

    @property
    def id(self):
        return f"{self.letter_id}:{self.句子[:12]}"

    @property
    def low(self):
        if self.起坐标 is None or self.止坐标 is None:
            return None
        return min(self.起坐标, self.止坐标)

    @property
    def high(self):
        if self.起坐标 is None or self.止坐标 is None:
            return None
        return max(self.起坐标, self.止坐标)

    @property
    def located(self):
        return self.low is not None

    def as_dict(self):
        return {
            "编号": self.letter_id,
            "日期": self.日期,
            "角色": self.角色,
            "函版本": self.函版本,
            "抬头": self.抬头,
            "原句": self.句子,
            "小句": self.小句,
            "线路": self.线路,
            "版本": self.版本,
            "起坐标": self.起坐标,
            "止坐标": self.止坐标,
            "单点": self.单点,
            "书写顺序": self.书写顺序,
            "横向": self.横向,
            "单元": self.单元,
            "土质": self.土质,
            "性质": self.性质,
            "覆盖": self.覆盖,
            "方法": self.方法,
            "限定词": self.限定词,
            "排除": self.排除,
            "标段": self.标段,
            "跨标段": self.跨标段,
            "候选": self.候选,
            "定位问题": self.定位问题,
            "备注": self.备注,
            "引用": self.引用,
            "断链": self.断链,
            "旧桩号": self.旧桩号,
            "附近": self.附近,
            "边界点": self.边界点,
        }


def _merge_compatible(facts):
    """合并同句内同范围的互补判断。

    典型是“疑有盐渍，未测”：前一小句是推测，后一小句说明没测。
    拆成两条会让同一个未知看起来像两个证据；合并后仍保留两句原文。
    """
    merged = []
    for fact in facts:
        if merged:
            previous = merged[-1]
            same = (
                previous.句子 == fact.句子
                and previous.线路 == fact.线路
                and previous.版本 == fact.版本
                and previous.起坐标 == fact.起坐标
                and previous.止坐标 == fact.止坐标
                and previous.横向 == fact.横向
            )
            complementary = {previous.性质, fact.性质} <= {
                "推测", "推测（未测）", "未覆盖调查", "定位障碍", None,
            }
            compatible_subject = (
                previous.土质 is None or fact.土质 is None
                or previous.土质 == fact.土质
            )
            if same and complementary and compatible_subject:
                previous.小句 = previous.小句 + "；" + fact.小句
                if previous.土质 is None:
                    previous.土质 = fact.土质
                if previous.性质 == "推测" and fact.性质 == "未覆盖调查":
                    previous.性质 = "推测（未测）"
                    previous.覆盖 = "未覆盖"
                elif fact.性质 in ("未覆盖调查", "推测（未测）", "定位障碍"):
                    previous.性质 = fact.性质
                    previous.覆盖 = fact.覆盖
                elif previous.性质 is None:
                    previous.性质 = fact.性质
                    previous.覆盖 = fact.覆盖
                previous.方法 = list(dict.fromkeys(previous.方法 + fact.方法))
                previous.限定词 = sorted(set(previous.限定词) | set(fact.限定词))
                previous.排除 = list(dict.fromkeys(previous.排除 + fact.排除))
                previous.备注 = list(dict.fromkeys(previous.备注 + fact.备注))
                previous.定位问题 = list(dict.fromkeys(previous.定位问题 + fact.定位问题))
                previous.附近 = previous.附近 or fact.附近
                previous.断链 = previous.断链 or fact.断链
                previous.旧桩号 = previous.旧桩号 or fact.旧桩号
                continue
        merged.append(fact)
    return merged


class FactBuilder:
    def __init__(self, corpus, locator):
        self.corpus = corpus
        self.locator = locator
        self._letters_by_date = sorted(
            corpus.rows("letters"), key=lambda row: (row["日期"], row["id"])
        )

    # ---- 入口 -----------------------------------------------------------
    def for_letter(self, letter):
        facts = []
        header_line = self._header_line(letter)
        for sentence in T.split_sentences(letter["内容"]):
            facts.extend(self._sentence(letter, sentence, header_line, facts))
        return _merge_compatible(facts)

    def all_facts(self):
        facts = []
        for letter in self._letters_by_date:
            facts.extend(self.for_letter(letter))
        return facts

    # ---- 句内归并 -------------------------------------------------------
    def _sentence(self, letter, sentence, header_line, earlier_facts):
        sentence_lines = T.detect_line(sentence)
        current_range = None
        current_units = []
        current_subject = None
        bridge_context = None
        pending_methods = []
        pending_questions = []
        pending_candidates = []
        pending_refs = self._references(letter, sentence)
        facts = []
        last_fact = None

        for clause in T.split_clauses(sentence):
            line = self._pick_line(clause, sentence_lines, header_line)
            version = T.detect_version(clause) or letter["版本"]
            nature, coverage, limits = T.detect_nature(clause)
            laterals, lateral_notes = T.parse_lateral(clause)
            units, unit_notes = scope.fact_units(laterals)
            units_stated = bool(laterals)
            excluded_sides, excluded_units, excluded_notes = T.parse_excluded(clause)
            methods = T.detect_method(clause)
            subject = T.detect_subject(clause) or current_subject
            if T.detect_subject(clause):
                current_subject = subject

            resolution = self.locator.resolve_points(clause, line, version)
            points = list(resolution.written)
            anaphora = T.has_any(clause, T.BRIDGE_ANAPHORA)
            if anaphora and not points and not bridge_context:
                prior = last_fact or (earlier_facts[-1] if earlier_facts else None)
                if prior is not None and prior.low is not None:
                    bridge_context = {
                        "坐标": prior.止坐标 if prior.止坐标 is not None else prior.高,
                        "名称": "前文桥端",
                        "来源": prior.letter_id,
                    }

            # 桥端回指：只把同句里的桥当作本小句区间的一端。
            if bridge_context and anaphora and resolution.low is not None:
                bridge_coord = bridge_context["坐标"]
                point = resolution.candidates[0].坐标 if resolution.candidates else None
                if point is not None and point != bridge_coord:
                    stated = T.detect_direction_word(clause)
                    moved = 1 if point > bridge_coord else -1
                    if stated:
                        expected = 1 if stated.startswith("东") else (
                            -1 if stated.startswith("西") else moved
                        )
                        if expected != moved:
                            resolution.questions.append(
                                f"原文写“{clause[:2]}”，但{points[0]}位于"
                                f"{bridge_context['名称']}的"
                                f"{'东' if moved > 0 else '西'}侧，方向不符"
                            )
                    low, high = sorted((bridge_coord, point))
                    resolution.low, resolution.high = low, high
                    resolution.point = False
                    resolution.notes.append(
                        f"“{clause[:2]}”以同句{bridge_context['名称']}端点"
                        f" {bridge_coord} 作为区间一端，与{points[0]}的 {point} 组成范围"
                    )
            elif bridge_context and anaphora and resolution.low is None:
                bridge_coord = bridge_context["坐标"]
                resolution.low = resolution.high = bridge_coord
                resolution.point = True
                resolution.notes.append(
                    f"“{clause[:2]}”回指同句{bridge_context['名称']}端点"
                )
                resolution.questions.append(
                    f"“{clause[:2]}”只给出方向，未写另一端点；当前只能从"
                    f"{bridge_context['名称']}端点起算"
                )

            # 记录本句最近提到的桥，供后续小句回指。
            for name in self.locator.landmark_names_in(clause):
                if "桥" in name:
                    coord, _, _, _ = self.locator.resolve_landmark(
                        name, line, version
                    )
                    if coord is not None:
                        bridge_context = {"坐标": coord, "名称": name}

            has_points = resolution.low is not None
            if has_points:
                current_range = resolution

            # 纯排除小句（“不能代表西侧”“本单未签北肩”）挂到同句或上一条事实。
            # “不能代表”在词表里也标成“未覆盖”，这里按排除处理：
            # 它是给上一条结论划边界，而不是新的独立调查结论。
            limit_only = (
                not T.detect_subject(clause)
                and not T.has_any(clause, T.FOUND + T.SUSPECT + T.NEGATIVE_FINDING)
            )
            if (excluded_sides or excluded_units) and not has_points and (
                nature is None or limit_only
            ):
                target = last_fact or (earlier_facts[-1] if earlier_facts else None)
                if target is not None:
                    for side in excluded_sides:
                        target.排除.append(f"{side}（{clause}）")
                    for unit in excluded_units:
                        target.排除.append(f"{unit}（{clause}）")
                    target.备注.extend(excluded_notes)
                    target.限定词 = sorted(set(target.限定词) | set(limits))
                    continue
                resolution.questions.append("排除句没有可挂靠的上一处结论")

            # 只给方法/引用、没有判断的小句，作为后续小句的上下文。
            if (methods or pending_refs) and nature is None and not has_points \
                    and not laterals and not subject:
                pending_methods.extend(methods)
                pending_refs.extend(self._references(letter, clause))
                continue

            # 只给范围的小句：留给后续小句承前。
            if nature is None and not laterals and not excluded_sides \
                    and not excluded_units and not subject:
                if limits and last_fact is not None:
                    last_fact.限定词 = sorted(set(last_fact.限定词) | set(limits))
                    last_fact.备注.append(f"同句限定：{clause}")
                continue

            # 有横向但没有判断的小句：横向留作后续小句承前。
            if nature is None and laterals and not excluded_sides and not excluded_units:
                current_units = units
                pending_methods.extend(methods)
                pending_questions.extend(resolution.questions)
                pending_candidates.extend(resolution.candidates)
                continue

            if not has_points and current_range is not None:
                resolution = current_range
                inherits_range = True
                # 承前的范围备注不重复挂到本小句。
                resolution = replace(
                    resolution,
                    notes=[n for n in resolution.notes if "同名地物" not in n],
                    questions=[q for q in resolution.questions
                               if "同名地物" not in q],
                )
            else:
                inherits_range = False

            if not units_stated and current_units:
                units = current_units
                lateral_notes.append("横向承前一小句，本小句未另写通道")

            if not has_points and current_range is None:
                fact = self._fact(
                    letter, sentence, clause, line, version, resolution,
                    laterals, lateral_notes + unit_notes, units,
                    subject, nature, coverage,
                    list(dict.fromkeys(pending_methods + methods)),
                    limits, excluded_sides, excluded_units, excluded_notes,
                    pending_refs, False,
                )
                fact.备注.append("本句没有可定位的桩号或地物，范围待补")
                if pending_questions:
                    fact.定位问题 = list(dict.fromkeys(
                        fact.定位问题 + pending_questions
                    ))
                if pending_candidates:
                    known = {(c["线路"], c["版本"], c["坐标"]) for c in fact.候选}
                    for candidate in pending_candidates:
                        key = (candidate.线路, candidate.版本, candidate.坐标)
                        if key not in known:
                            fact.候选.append(candidate.as_dict())
                            known.add(key)
                facts.append(fact)
                last_fact = fact
                pending_methods = []
                pending_questions = []
                pending_candidates = []
                pending_refs = []
                continue

            fact = self._fact(
                letter, sentence, clause, line, version, resolution,
                laterals, lateral_notes + unit_notes, units,
                subject, nature, coverage,
                list(dict.fromkeys(pending_methods + methods)),
                limits, excluded_sides, excluded_units, excluded_notes,
                pending_refs, inherits_range,
            )
            # 横向/桥梁指代小句只作背景时，它带来的定位问题要挂到
            # 真正成条的那句上，不能随小句一起丢掉。
            if pending_questions:
                fact.定位问题 = list(dict.fromkeys(
                    fact.定位问题 + pending_questions
                ))
            if pending_candidates:
                known = {(c["线路"], c["版本"], c["坐标"]) for c in fact.候选}
                for candidate in pending_candidates:
                    key = (candidate.线路, candidate.版本, candidate.坐标)
                    if key not in known:
                        fact.候选.append(candidate.as_dict())
                        known.add(key)
            facts.append(fact)
            last_fact = fact
            pending_methods = []
            pending_questions = []
            pending_candidates = []
            pending_refs = []

        # 同句末尾的限定小句再归到上一条事实。
        for clause in T.split_clauses(sentence):
            if T.has_any(clause, T.LIMIT) and not T.detect_nature(clause)[0] and facts:
                facts[-1].限定词 = sorted(
                    set(facts[-1].限定词) | {word for word in T.LIMIT if word in clause}
                )
                facts[-1].备注.append(f"同句限定：{clause}")
        return facts

    # ---- 单条事实 -------------------------------------------------------
    def _fact(self, letter, sentence, clause, line, version, resolution,
              laterals, lateral_notes, units, subject, nature, coverage,
              methods, limits, excluded_sides, excluded_units, excluded_notes,
              refs, inherits_range):
        questions = list(dict.fromkeys(resolution.questions))
        notes = list(dict.fromkeys(resolution.notes + lateral_notes + excluded_notes))
        if not laterals:
            notes.append("横向范围原文未说明（未写主线、路肩或半幅）")
        if resolution.break_point:
            questions.append(
                "断链：同一坐标对应两个桩号，不能按书写顺序判断起止；"
                "需确认引用的是断链前还是断链后桩号，以及该段是否跨断链"
            )
        if T.detect_direction_word(clause):
            notes.append(f"原文方位词：{T.detect_direction_word(clause)}")
        if "附近" in clause:
            notes.append("原文只说“附近”，未给出范围")
        if "旧线" in clause or "旧桩号" in clause:
            resolution.old_chainage = True
        if line is None:
            version_lines = sorted({
                row["线路"] for row in self.corpus.rows("alignments")
                if row["版本"] == version
            })
            if len(version_lines) == 1:
                line = version_lines[0]
                notes.append(f"原文未写线路，按{version}版仅见于{line}暂定")
            else:
                questions.append(
                    f"原文未写线路，{version}版涉及 " + "、".join(version_lines)
                    + "，不能确定指哪一条"
                )
        header_line = self._header_line(letter)
        if line and header_line and line != header_line:
            notes.append(f"本句线路（{line}）与抬头（{header_line}）不同")
        if "标段" in (letter.get("抬头") or ""):
            notes.append(
                f"抬头写“{letter['抬头']}”而未写线路名；资料中的标段编号是 S1/S2 等，"
                "不能直接把“二标段”之类换算成线路"
            )
            questions.append(
                f"抬头“{letter['抬头']}”与标段编号如何对应？请补线路名或标段编号"
            )

        boundary = "两端衔接" in laterals or "两端衔接" in units
        if boundary and resolution.low is not None:
            notes.append("“两端衔接”只覆盖标段界线，线段内部不在本句覆盖内")

        excluded = [f"{side}" for side in excluded_sides]
        excluded.extend(excluded_units)

        segments = []
        if line and resolution.low is not None and resolution.high is not None:
            low, high = sorted((resolution.low, resolution.high))
            segments = self.locator.segments_in_range(line, version, low, high)
        # 只有原句点到另一条线路，或跨表明确把两地物连起来时，
        # 才把那条线路的标段记为“跨标段”。同名地物只作候选警示，
        # 不能因为桥名相同就把邻近标段算进本条事实。
        named_lines = T.detect_line(clause)
        cross = []
        if line and (len(named_lines) > 1 or any(
            "跨表" in note for note in resolution.notes
        )):
            for candidate in resolution.candidates:
                if candidate.线路 == line:
                    continue
                if candidate.线路 not in named_lines and "跨表" not in " ".join(
                    resolution.notes
                ):
                    continue
                for segment in candidate.标段:
                    if segment not in cross:
                        cross.append(segment)

        return Fact(
            letter_id=letter["id"], 日期=letter["日期"], 角色=letter["角色"],
            函版本=letter["版本"], 抬头=letter["抬头"],
            句子=sentence, 小句=clause,
            线路=line, 版本=version,
            起坐标=resolution.low, 止坐标=resolution.high, 单点=resolution.point,
            书写顺序=resolution.written,
            横向=laterals, 单元=list(units),
            土质=subject, 性质=nature, 覆盖=coverage,
            方法=list(dict.fromkeys(methods)),
            限定词=sorted(set(limits)), 排除=excluded,
            标段=segments, 跨标段=cross,
            候选=[item.as_dict() for item in resolution.candidates],
            定位问题=list(dict.fromkeys(questions)),
            备注=list(dict.fromkeys(notes)),
            引用=sorted(set(refs)),
            断链=resolution.break_point,
            旧桩号=resolution.old_chainage, 附近=("附近" in clause),
            边界点=boundary,
        )

    # ---- 上下文 ---------------------------------------------------------
    def _header_line(self, letter):
        for name in T.LINE_NAMES:
            if name in (letter.get("抬头") or ""):
                return name
        return None

    def _pick_line(self, clause, sentence_lines, header_line):
        explicit = T.detect_line(clause)
        if explicit:
            return explicit[0]
        if sentence_lines:
            return sentence_lines[0]
        return header_line

    def _references(self, letter, text):
        """标出“接前函”这类引用，便于区分补录与独立记录。"""
        refs = []
        for word in ("前函", "前一份", "前一函", "接前"):
            if word in (text or ""):
                earlier = [
                    row for row in self._letters_by_date
                    if row["日期"] < letter["日期"] and row["角色"] == letter["角色"]
                ]
                if earlier:
                    refs.append(earlier[-1]["id"])
        return sorted(set(refs))
