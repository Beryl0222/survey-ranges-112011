"""把文字里的关键点落到案卷坐标上。

原则：
- 只有材料明确支持时才给出唯一定位；
- 同一线路同名地物、断链同名桩号、旧线桩号在新线无对应等，
  一律作为定位问题提出，并保留候选范围；
- 旧线桩号按跨表、借用段或内插分别给候选，不把一种读法当成唯一答案；
- 引用相邻标段时明确写出“跨标段”，不把它偷偷并进本段。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import text as T
from .chainage import format_chainage, parse_chainage


@dataclass
class Candidate:
    """一处可能的位置，连同它为什么是候选。"""
    线路: str
    版本: str
    坐标: float
    依据: str
    标段: list = field(default_factory=list)

    def as_dict(self):
        return {
            "线路": self.线路,
            "版本": self.版本,
            "坐标": self.坐标,
            "标段": self.标段,
            "依据": self.依据,
        }


@dataclass
class PointRef:
    token: str
    kind: str
    coord: float | None
    candidates: list = field(default_factory=list)


@dataclass
class RangeResolution:
    low: float | None = None
    high: float | None = None
    point: bool = False
    written: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    break_point: bool = False
    old_chainage: bool = False

    def as_dict(self):
        return {
            "起坐标": self.low,
            "止坐标": self.high,
            "单点": self.point,
            "书写顺序": self.written,
            "候选": [item.as_dict() for item in self.candidates],
            "备注": self.notes,
            "定位问题": self.questions,
            "断链": self.break_point,
            "旧桩号": self.old_chainage,
        }


class Locator:
    """在已装入的案卷上做定位，不改动任何原始记录。"""

    def __init__(self, corpus):
        self.corpus = corpus
        self._landmarks = {}
        for row in corpus.rows("landmarks"):
            self._landmarks.setdefault(row["名称"], []).append(row)

    # ---- 地物 -----------------------------------------------------------
    def landmark_names_in(self, text):
        """句中出现的已知地物名，包括“东沟旧桥”这类带旧/新的写法。"""
        text = text or ""
        found = []
        for name in self._landmarks:
            if not name:
                continue
            if name in text:
                found.append(name)
                continue
            stem = name.rstrip("桥")
            if stem and f"{stem}旧桥" in text or (stem and f"{stem}新桥" in text):
                found.append(name)
        return found

    def landmark_candidates(self, name):
        return [
            Candidate(
                线路=row["线路"], 版本=row["版本"], 坐标=row["道路坐标"],
                依据=f"地物 {row['id']} {row['名称']}",
                标段=self.segments_at(row["线路"], row["版本"], row["道路坐标"]),
            )
            for row in self._landmarks.get(name, [])
        ]

    def resolve_landmark(self, name, line=None, version=None):
        """按线路、版本收窄同名地物；收不窄就只给候选。"""
        all_rows = self._landmarks.get(name, [])
        notes = []
        questions = []
        if not all_rows:
            # 借用线没有独立地物记录时，跨表可能指认同一座旧桥。
            for link in self.corpus.rows("crosswalk"):
                if line and link["乙线路"] == line:
                    notes.append(
                        f"地物“{name}”在{line}无独立记录；跨表 {link['id']} 只把"
                        f"{link['甲线路']}甲坐标 {link['甲坐标']} 与"
                        f"{link['乙线路']}乙坐标 {link['乙坐标']} 相连"
                    )
                    questions.append(
                        f"“{name}”在{line}缺少自身地物记录，跨表 {link['id']} 是否即为"
                        f"该桥，需先确认构筑物编号和位置"
                    )
                    return link["乙坐标"], self.landmark_candidates(name), notes, questions
            questions.append(f"地物“{name}”在案卷中找不到记录")
            return None, [], notes, questions

        rows = all_rows
        if line:
            same_line = [row for row in rows if row["线路"] == line]
            if same_line:
                rows = same_line
            else:
                # 借用线没有自己的地物记录。只有在跨表明确把该线路与
                # 同名地物所在线路连起来时，才允许引用；否则不猜。
                links = [
                    link for link in self.corpus.rows("crosswalk")
                    if link["乙线路"] == line
                ]
                connected = any(
                    link["甲线路"] == row["线路"] for link in links for row in rows
                )
                if connected:
                    notes.append(
                        f"地物“{name}”在{line}无独立记录；跨表把{line}与"
                        f"{'、'.join(sorted({link['甲线路'] for link in links}))}"
                        "相应坐标相连，暂按该地物位置引用"
                    )
                    questions.append(
                        f"“{name}”在{line}的构筑物编号与位置仍需按跨表确认，"
                        "共享构筑物只能引用已核实的几何和编号"
                    )
                else:
                    questions.append(
                        f"地物“{name}”在{line}没有记录，也没有跨表能把它连到其他线路"
                    )
                    return None, self.landmark_candidates(name), notes, questions
        if version:
            same_version = [row for row in rows if row["版本"] == version]
            if same_version:
                rows = same_version
            else:
                notes.append(
                    f"地物“{name}”在{line or '已知线路'}没有{version}版记录，"
                    "按现有记录列出候选"
                )

        other_names = [row for row in all_rows if row not in rows]
        if other_names:
            notes.append(
                f"同名地物“{name}”另有 {len(other_names)} 条记录："
                + "、".join(f"{row['线路']}{row['版本']} {row['id']}" for row in other_names)
            )
            if len({row["道路坐标"] for row in all_rows}) > 1:
                questions.append(
                    f"同名地物“{name}”在案卷里对应 {len({row['道路坐标'] for row in all_rows})} 个坐标，"
                    "当前句没有足够信息排除其他同名物"
                )

        coords = sorted({row["道路坐标"] for row in rows})
        if len(coords) > 1:
            questions.append(
                f"地物“{name}”在{line or '已知线路'}有 {len(coords)} 个位置，"
                "需确认指哪一处"
            )
            return None, self.landmark_candidates(name), notes, questions
        if not coords:
            return None, [], notes, questions
        return coords[0], self.landmark_candidates(name), notes, questions

    # ---- 桩号 -----------------------------------------------------------
    def chainage_candidates(self, token, line=None, version=None):
        matches = [
            row for row in self.corpus.rows("alignments")
            if row["桩号"] == token
            and (line is None or row["线路"] == line)
            and (version is None or row["版本"] == version)
        ]
        return matches

    def resolve_chainage(self, token, line=None, version=None, old=False,
                         interpolate=True):
        notes = []
        questions = []
        try:
            parse_chainage(token)
        except ValueError as exc:
            return None, [], notes, [str(exc)]

        matches = self.chainage_candidates(token, line, version)
        if not matches and interpolate and line and version:
            coord, interp_notes, interp_questions = self.interpolate_chainage(
                token, line, version
            )
            if coord is not None:
                notes.extend(interp_notes)
                questions.extend(interp_questions)
                matches = [{
                    "id": f"内插（{line}{version}）",
                    "线路": line,
                    "版本": version,
                    "桩号": token,
                    "道路坐标": coord,
                }]
        if not matches and old:
            # 旧线桩号先在甲版里找，再按跨表转到乙版。
            old_matches = self.chainage_candidates(token, line, "甲")
            if old_matches:
                notes.append(f"{token} 按旧线（甲版）桩号读取")
                questions.append(
                    f"旧线桩号 {token} 需按跨表落到新线位置；旧线和新线各写各的桩号，"
                    "不能直接沿用"
                )
                matches = old_matches
        if not matches and line:
            # 线路写错或借用线没有对照时，保留其他线路候选而不是硬套。
            everywhere = self.chainage_candidates(token)
            if everywhere:
                questions.append(
                    f"桩号 {token} 在{line}没有对照，只见于"
                    + "、".join(f"{row['线路']}{row['版本']}" for row in everywhere)
                )
                matches = everywhere
        if not matches:
            questions.append(f"桩号 {token} 在{line or '已知线路'}找不到对照")
            return None, [], notes, questions

        coords = sorted({row["道路坐标"] for row in matches})
        candidates = [
            Candidate(
                线路=row["线路"], 版本=row["版本"], 坐标=row["道路坐标"],
                依据=f"桩号 {token}（alignments {row['id']}）",
                标段=self.segments_at(row["线路"], row["版本"], row["道路坐标"]),
            )
            for row in matches
        ]
        if len(matches) > 1:
            same_coord = len(coords) == 1
            if same_coord:
                notes.append(
                    f"断链：{token} 在{line or ''}有 {len(matches)} 条对照，"
                    f"均落在坐标 {coords[0]}；只按桩号书写无法区分"
                )
                questions.append(
                    f"桩号 {token} 位于断链点，同一坐标对应 "
                    + "、".join(row["桩号"] for row in matches)
                    + "；需确认按断链前还是断链后桩号引用"
                )
            else:
                questions.append(
                    f"桩号 {token} 对应 {len(coords)} 个坐标，需人工确认"
                )
        if len(coords) == 1:
            return coords[0], candidates, notes, questions
        return None, candidates, notes, questions

    def interpolate_chainage(self, token, line, version):
        """桩号不在对照表里时，按相邻两点线性内插。

        只有夹在一对相邻桩号之间、且该段没有重复坐标（断链）时才内插；
        否则返回 None 并把原因作为定位问题。
        """
        try:
            metres = parse_chainage(token)
        except ValueError as exc:
            return None, [], [str(exc)]
        rows = sorted(
            (row for row in self.corpus.rows("alignments")
             if row["线路"] == line and row["版本"] == version),
            key=lambda row: parse_chainage(row["桩号"]),
        )
        if len(rows) < 2:
            return None, [], [f"{line}{version}版对照点不足，无法内插 {token}"]
        for left, right in zip(rows, rows[1:]):
            left_m = parse_chainage(left["桩号"])
            right_m = parse_chainage(right["桩号"])
            if not (left_m < metres < right_m):
                continue
            if right_m == left_m:
                continue
            if left["道路坐标"] == right["道路坐标"]:
                return None, [], [
                    f"{token} 落在断链段内插范围 {left['桩号']}–{right['桩号']}，"
                    "两端坐标相同，不能内插"
                ]
            ratio = (metres - left_m) / (right_m - left_m)
            coord = left["道路坐标"] + ratio * (right["道路坐标"] - left["道路坐标"])
            notes = [
                f"{token} 不在对照表，按 {left['桩号']}（{left['道路坐标']}）与 "
                f"{right['桩号']}（{right['道路坐标']}）线性内插得 {round(coord, 3)}"
            ]
            return round(coord, 3), notes, [
                f"{token} 为内插位置，需确认断链与纵坡是否影响换算"
            ]
        return None, [], [f"{token} 不在{line}{version}版对照点范围内，不能内插"]

    def resolve_old_chainage(self, token, old_line, old_version="甲", new_version="乙"):
        """旧线桩号落到新方案的可能位置。

        旧线和新线各写各的桩号；这里同时给出：
        - 旧线表上的精确或内插坐标；
        - 跨表把旧坐标连到的乙线位置；
        - 乙线保留同一旧桩号的借用段（如果有）。
        候选多于一个就提问，不替材料选。
        """
        notes = []
        questions = []
        candidates = []

        # 旧线（甲版）的桩号可能只存在于正线；借用段沿用旧桩号时，
        # 先按同一线路找，找不到再去正线甲版里内插，并注明这是借读。
        old_coord = None
        old_source = old_line
        exact = self.chainage_candidates(token, old_line, old_version)
        if not exact and old_line != "正线":
            exact = self.chainage_candidates(token, "正线", old_version)
            if exact:
                old_source = "正线"
                notes.append(f"旧线桩号按正线{old_version}版读（{old_line}无该版对照）")
        if exact:
            coords = {row["道路坐标"] for row in exact}
            if len(coords) == 1:
                old_coord = coords.pop()
                notes.append(f"旧线 {old_source}{old_version}版有 {token} 对照")
            else:
                questions.append(f"旧线 {old_source} 的 {token} 有 {len(coords)} 个坐标")
        else:
            old_coord, interp_notes, interp_questions = self.interpolate_chainage(
                token, old_source, old_version
            )
            if old_coord is None and old_source != "正线":
                alt_coord, alt_notes, alt_questions = self.interpolate_chainage(
                    token, "正线", old_version
                )
                if alt_coord is not None:
                    old_coord = alt_coord
                    old_source = "正线"
                    interp_notes = alt_notes + [
                        f"{old_line}无{old_version}版对照，按正线{old_version}版内插"
                    ]
                    interp_questions = alt_questions
            notes.extend(interp_notes)
            questions.extend(interp_questions)

        if old_coord is not None:
            for link in self.corpus.rows("crosswalk"):
                if link["甲线路"] == old_source and link["甲坐标"] == old_coord:
                    candidates.append(Candidate(
                        线路=link["乙线路"], 版本=new_version, 坐标=link["乙坐标"],
                        依据=f"跨表 {link['id']}：{old_source}甲 {old_coord} → "
                             f"{link['乙线路']}乙 {link['乙坐标']}",
                        标段=self.segments_at(link["乙线路"], new_version, link["乙坐标"]),
                    ))

        # 借用段可能直接保留旧桩号；这与“按新线内插”是两个不同位置。
        # 同一坐标上的借用段记录本身就是候选，不能因为坐标相同就丢掉。
        for row in self.corpus.rows("alignments"):
            if row["版本"] != new_version or row["桩号"] != token:
                continue
            candidates.append(Candidate(
                线路=row["线路"], 版本=row["版本"], 坐标=row["道路坐标"],
                依据=f"{row['线路']}{row['版本']}版保留同一桩号 {token}（alignments {row['id']}）",
                标段=self.segments_at(row["线路"], row["版本"], row["道路坐标"]),
            ))

        # 新线在同一坐标上的位置同样可能被误当旧桩号对应。
        if old_coord is not None:
            for row in self.corpus.rows("alignments"):
                if row["版本"] != new_version or row["线路"] == old_line:
                    continue
                if row["道路坐标"] == old_coord:
                    candidates.append(Candidate(
                        线路=row["线路"], 版本=row["版本"], 坐标=row["道路坐标"],
                        依据=f"{row['线路']}{row['版本']}版在坐标 {old_coord} 有对照点 {row['桩号']}",
                        标段=self.segments_at(row["线路"], row["版本"], row["道路坐标"]),
                    ))

        unique = {}
        for candidate in candidates:
            unique[(candidate.线路, candidate.版本, candidate.坐标)] = candidate
        candidates = list(unique.values())
        if not candidates:
            questions.append(f"旧线桩号 {token} 在跨表和新线对照里都找不到位置")
        elif len(candidates) > 1:
            questions.append(
                f"旧线桩号 {token} 对应 {len(candidates)} 个候选位置："
                + "、".join(f"{c.线路}乙 {c.坐标}" for c in candidates)
                + "；需确认是按跨表、借用段还是新线内插"
            )
        return candidates, notes, questions

    @staticmethod
    def _settle_candidates(candidates, questions):
        """候选坐标只有一个时才落位；多义时留给定位问题。"""
        coords = {candidate.坐标 for candidate in candidates}
        if len(coords) == 1:
            return coords.pop()
        if not coords and not questions:
            questions.append("没有可用候选位置")
        return None

    def segments_at(self, line, version, metres):
        return [row["id"] for row in self.corpus.segment_of(line, version, metres)]

    def segments_in_range(self, line, version, low, high):
        return [
            row["id"] for row in self.corpus.rows("segments")
            if row["线路"] == line and row["版本"] == version
            and row["止米"] >= low and row["起米"] <= high
        ]

    def line_bounds(self, line, version=None):
        rows = [
            row for row in self.corpus.rows("segments")
            if row["线路"] == line and (version is None or row["版本"] == version)
        ]
        if not rows:
            return None, None
        return min(row["起米"] for row in rows), max(row["止米"] for row in rows)

    # ---- 范围 -----------------------------------------------------------
    def resolve_points(self, clause, line=None, version=None):
        """按句中书写顺序取出地物/桩号点，给出区间或候选。

        “以西/以东/往回”等只作校验和说明；区间一律取两点坐标的较小、较大值。
        """
        result = RangeResolution()
        points = []

        # 按原文出现位置排序，保留“东沟桥以西到K8+000”的书写顺序。
        found = []
        for token in T.chainages(clause):
            found.append((clause.find(token), token, "chainage"))
        names = self.landmark_names_in(clause)
        if not names and "桥界面" in (clause or ""):
            # “桥界面”没点名哪座桥：保留案卷里所有桥梁地物作候选。
            for name, rows in self._landmarks.items():
                if "桥" not in name:
                    continue
                for row in rows:
                    result.candidates.append(Candidate(
                        线路=row["线路"], 版本=row["版本"], 坐标=row["道路坐标"],
                        依据=f"“桥界面”未点名，候选桥梁地物 {row['id']} {name}",
                        标段=self.segments_at(row["线路"], row["版本"], row["道路坐标"]),
                    ))
            result.questions.append(
                "“桥界面”没有点名桥梁，候选为案卷内全部桥梁地物；"
                "需确认是哪一座桥、哪条线路、哪一侧"
            )
        for name in names:
            found.append((clause.find(name), name, "landmark"))
        for _, token, kind in sorted(found, key=lambda item: (item[0], item[1])):
            if kind == "chainage":
                text = clause or ""
                if "旧线" in text or "旧桩号" in text:
                    result.old_chainage = True
                    candidates, notes, questions = self.resolve_old_chainage(
                        token, line, "甲", version or "乙"
                    )
                    coord = self._settle_candidates(candidates, questions)
                else:
                    coord, candidates, notes, questions = self.resolve_chainage(
                        token, line, version
                    )
            else:
                coord, candidates, notes, questions = self.resolve_landmark(
                    token, line, version
                )
            result.notes.extend(notes)
            result.questions.extend(questions)
            result.candidates.extend(candidates)
            if any("断链" in note for note in notes):
                result.break_point = True
            points.append(PointRef(token, kind, coord, candidates))

        result.written = [point.token for point in points]
        if "附近" in (clause or "") and points:
            result.notes.append("原文只说“附近”，没有给出起止桩号")
            result.questions.append(
                f"“{points[0].token}附近”的范围未给出；请补起止桩号或里程，"
                f"当前只能保留候选位置 {points[0].coord}"
            )

        coords = [point.coord for point in points if point.coord is not None]
        if len(points) >= 2 and len(coords) >= 2:
            first, last = coords[0], coords[-1]
            result.low, result.high = min(first, last), max(first, last)
            result.point = result.low == result.high
            stated = T.detect_direction_word(clause)
            actual = T.direction_between(first, last)
            if stated in ("以西", "以东", "以南", "以北", "往回"):
                if (stated == "以西" and last > first) or (stated == "以东" and last < first):
                    result.questions.append(
                        f"原文写“{stated}”，但两个定位点的坐标顺序是{actual}，需确认起止"
                    )
            result.notes.append(
                f"书写顺序 {first}→{last}，{actual}"
            )
        elif len(points) == 1:
            result.low = result.high = points[0].coord
            result.point = True
            if T.detect_direction_word(clause):
                result.questions.append(
                    f"原文有“{T.detect_direction_word(clause)}”但只给出了一个定位点，"
                    "另一端点未写"
                )
        elif len(points) >= 2:
            result.questions.append("两个定位点中至少一个无法落到坐标，不能组成区间")
        else:
            result.questions.append("原句没有可定位的桩号或地物，无法确定起止")

        if len(points) >= 2:
            result.point = (result.low == result.high)
        if result.point and result.low is not None:
            chainages = self.same_coordinate_chainages(
                line, version, result.low
            ) if line and version else []
            if len(chainages) > 1:
                result.break_point = True
                result.notes.append(
                    f"坐标 {result.low} 在{line}{version}版对应桩号：" + "、".join(chainages)
                )
        return result

    def same_coordinate_chainages(self, line, version, metres):
        return [
            row["桩号"] for row in self.corpus.rows("alignments")
            if row["线路"] == line and row["版本"] == version
            and row["道路坐标"] == metres
        ]

    def reach(self, line, version, low, high):
        return self.segments_in_range(line, version, low, high)

    @staticmethod
    def describe(low, high):
        if low is None or high is None:
            return None
        if low == high:
            return format_chainage(low)
        return f"{format_chainage(low)}→{format_chainage(high)}"
