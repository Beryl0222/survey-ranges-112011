"""按原样读取案卷，并核对字段形状。

这里不做任何“清洗”：空字符串仍为空字符串，词形和录入顺序保持不动。
解析结果另行生成，避免把推断写回案卷。
"""
from __future__ import annotations

import json
from pathlib import Path

from .chainage import parse_chainage


class MaterialError(ValueError):
    """案卷缺项或形状不对，无法据此定位。"""


# 每份资料的字段名，与 data/ 中的 JSON 完全一致。
FIELDS = {
    "alignments": ("id", "线路", "版本", "桩号", "道路坐标"),
    "landmarks": ("id", "名称", "线路", "版本", "道路坐标"),
    "segments": ("id", "线路", "版本", "起米", "止米"),
    "letters": ("id", "日期", "角色", "版本", "抬头", "内容"),
    "directives": ("id", "版本", "条文"),
    "drawings": ("id", "标段", "线路", "版本", "范围", "依赖"),
    "design_log": ("id", "日期", "图号", "事件", "内容"),
    "crosswalk": ("id", "甲线路", "甲坐标", "乙线路", "乙坐标", "交接"),
}


def read_collection(path):
    """加载一份 UTF-8 资料，保持录入顺序和文字原样。"""
    with Path(path).open(encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError(f"{Path(path).name} 应为记录对象的数组")
    return records


class Corpus:
    """一次装好的案卷集合。

    所有原始记录都能按编号取回；解析视图（桩号米数等）单独缓存，
    不改动原始记录。
    """

    def __init__(self, root):
        self.root = Path(root)
        self.data_dir = self.root / "data"
        self.raw = {}
        for name in FIELDS:
            path = self.data_dir / f"{name}.json"
            if not path.exists():
                raise MaterialError(f"缺少资料集：{name}.json")
            rows = read_collection(path)
            self.raw[name] = rows
        self._validate()
        self._chainage = {}

    # ---- 原始取用 -------------------------------------------------------
    def rows(self, name):
        return self.raw[name]

    def by_id(self, name, record_id):
        """同一编号在资料集内可能有多条（不同版本），全部按录入顺序返回。"""
        return [row for row in self.raw[name] if row["id"] == record_id]

    # ---- 校核 -----------------------------------------------------------
    def _validate(self):
        for name, fields in FIELDS.items():
            for row in self.raw[name]:
                missing = [field for field in fields if field not in row]
                extra = [field for field in row if field not in fields]
                if missing or extra:
                    raise MaterialError(
                        f"{name} 记录 {row.get('id')!r} 字段不符：缺 {missing}，多 {extra}"
                    )
                if not isinstance(row["id"], str) or not row["id"]:
                    raise MaterialError(f"{name} 有记录缺少编号")
                self._check_scalars(name, row)

    @staticmethod
    def _check_scalars(name, row):
        for field in ("线路", "版本", "桩号", "名称", "角色", "抬头", "内容",
                      "条文", "标段", "范围", "图号", "事件", "日期", "交接",
                      "甲线路", "乙线路"):
            if field in row and not isinstance(row[field], str):
                raise MaterialError(f"{name} 记录 {row['id']!r} 的 {field} 应为文字")
        for field in ("道路坐标", "起米", "止米", "甲坐标", "乙坐标"):
            if field in row and not isinstance(row[field], (int, float)):
                raise MaterialError(f"{name} 记录 {row['id']!r} 的 {field} 应为数值")
        if "依赖" in row and not isinstance(row["依赖"], list):
            raise MaterialError(f"{name} 记录 {row['id']!r} 的依赖应为数组")

    # ---- 解析出的视图 ---------------------------------------------------
    def chainage_metres(self, record):
        """alignments 记录里桩号对应的米数；同一记录只解析一次。"""
        key = record["id"]
        if key not in self._chainage:
            self._chainage[key] = parse_chainage(record["桩号"])
        return self._chainage[key]

    def segment_of(self, line, version, metres):
        """给定线路、版本与道路坐标，返回包含该点的标段记录列表。

        标段区间含两端，交接点为相邻两段共有，因此可能返回两条。
        """
        return [
            row for row in self.raw["segments"]
            if row["线路"] == line
            and row["版本"] == version
            and row["起米"] <= metres <= row["止米"]
        ]
