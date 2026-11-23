"""批准采用的设计指导条款，以及本工具对条款的可执行解释。

条文是散文；工具不自行改写条文，只把“什么算满足”写成结构化要求，
并在报告中同时引用条文原文和条号。若条文改动到与解释不符，载入时报警，
避免旧解释悄悄套到新条文上。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    类别: str          # 调查 / 复核
    标的: str          # 试坑记录、横断面复核、定位补查、共同会签
    说明: str


@dataclass(frozen=True)
class Clause:
    id: str
    版本: str
    条号原文: str
    适用: str
    要求: tuple
    判别关键词: tuple
    说明: str


# 解释与 data/directives.json 的条号一一对应。
# 判别关键词用于核对“这条确实是本工具解释的那条”，不做语义外推。
CLAUSES = (
    Clause(
        id="P1", 版本="乙",
        条号原文="已发现湿陷层的路肩设计须有试坑及相符版次横断面复核两份答复。",
        适用="同版次已发现湿陷层、且设计对象含路肩",
        要求=(
            Requirement("调查", "试坑记录", "在受影响路肩范围内有试坑或试挖记录"),
            Requirement("复核", "相符版次横断面复核", "横断面复核须与被复核设计同版次，且不处于待回复状态"),
        ),
        判别关键词=("湿陷", "路肩", "试坑", "横断面"),
        说明="调查与复核分开留痕；只有试坑、没有同版次断面答复时，不得视为满足。",
    ),
    Clause(
        id="P2", 版本="乙",
        条号原文="疑似盐渍先要求定位补查，未知不能签为通过。",
        适用="同版次存在盐渍推测",
        要求=(
            Requirement("调查", "定位补查", "先确定位置与范围，再谈是否排除"),
        ),
        判别关键词=("盐渍", "定位", "未知"),
        说明="“疑有”不是“未见”，位置未知时整图不能作为通过件。",
    ),
    Clause(
        id="P3", 版本="乙",
        条号原文="标段共用接合处在相邻两边证据冲突时须共同会签。",
        适用="相邻标段共用接合处，两边证据不一致或一边引用另一边而未会签",
        要求=(
            Requirement("复核", "共同会签", "相邻标段对接合处的结论共同签署"),
        ),
        判别关键词=("接合", "冲突", "共同会签"),
        说明="仅边界接合处挂起，两边各自内段仍可继续审查。",
    ),
    Clause(
        id="P4", 版本="甲",
        条号原文="运营线原签核区间继续使用甲版。疑似盐渍只允许在定位已确认的相同构筑物单列附注，定位未知须补勘。",
        适用="甲版运营线；存在盐渍推测",
        要求=(
            Requirement("复核", "原签核区间保留", "已签核区间继续按甲版有效"),
            Requirement("调查", "定位补勘", "盐渍位置未知时先补勘；确认同一构筑物才可单列附注"),
        ),
        判别关键词=("甲版", "盐渍", "构筑物"),
        说明="后续补勘不得自动改写甲版已签区间；只改变其明确涉及的位置。",
    ),
    Clause(
        id="P5", 版本="乙",
        条号原文="项目交接单能确认共享构筑物几何和编号时可引用这两项。同址试坑结论的横向覆盖仍从勘察案卷核对，不从构筑物共享推断。",
        适用="跨方案或跨线路存在经确认的共享构筑物",
        要求=(
            Requirement("复核", "共享构筑物引用", "只能引用已核实的几何与编号"),
            Requirement("调查", "横向覆盖核对", "试坑结论的横向覆盖须回勘察案卷核对"),
        ),
        判别关键词=("共享构筑物", "横向覆盖", "试坑"),
        说明="共享的是构筑物，不是土质结论的适用范围；新边界仍按对应方案判断。",
    ),
)


def check_integrity(corpus):
    """核对资料里的条文与工具解释是否还对得上。"""
    warnings = []
    by_id = {row["id"]: row for row in corpus.rows("directives")}
    for clause in CLAUSES:
        row = by_id.get(clause.id)
        if row is None:
            warnings.append(f"资料缺少条款 {clause.id}")
            continue
        if row["版本"] != clause.版本:
            warnings.append(
                f"条款 {clause.id} 版本为 {row['版本']}，解释按 {clause.版本} 编写"
            )
        missing = [word for word in clause.判别关键词 if word not in row["条文"]]
        if missing:
            warnings.append(
                f"条款 {clause.id} 原文缺少判别词 {missing}，解释可能过期"
            )
        if row["条文"] != clause.条号原文:
            warnings.append(f"条款 {clause.id} 原文与解释所记不一致，请核对")
    return warnings
