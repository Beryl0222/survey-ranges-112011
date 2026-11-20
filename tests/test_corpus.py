"""交接资料格式和原文完整性测试。"""
from pathlib import Path
import json
import subprocess
import sys
import unittest

from corpus import read_collection

ROOT = Path(__file__).resolve().parents[1]


def records(name):
    return read_collection(ROOT / "data" / (name + ".json"))


class MaterialTests(unittest.TestCase):
    def test_collections_are_utf8_record_arrays(self):
        for name, fields in FIELDS.items():
            with self.subTest(collection=name):
                rows = records(name)
                self.assertTrue(rows)
                for row in rows:
                    self.assertEqual(set(row), set(fields))
                    self.assertIsInstance(row["id"], str)
                    self.assertTrue(row["id"])

    def test_reader_retains_exact_text_and_order(self):
        result = subprocess.check_output(
            [sys.executable, "-m", "corpus", PRIMARY, "--id", LINE_ID],
            cwd=ROOT, text=True, encoding="utf-8",
        )
        original = [row for row in records(PRIMARY) if row["id"] == LINE_ID]
        self.assertTrue(original)
        self.assertEqual(json.loads(result), original)

    def test_discontinuity_and_shared_border_are_representable(self):
        alignments = records("alignments")
        positions = {(row["线路"], row["版本"], row["桩号"]): row["道路坐标"]
                     for row in alignments}
        self.assertEqual(positions["正线", "乙", "K8+200"], positions["正线", "乙", "K8+100"])
        segments = {row["id"]: row for row in records("segments")}
        self.assertEqual(segments["S1"]["止米"], segments["S2"]["起米"])
        plots = {row["id"]: row for row in records("drawings")}
        for row in plots.values():
            self.assertTrue(set(row["依赖"]).issubset(plots))


PRIMARY = 'letters'
LINE_ID = 'B2'
FIELDS = {
    "alignments": [
        "id",
        "线路",
        "版本",
        "桩号",
        "道路坐标"
    ],
    "landmarks": [
        "id",
        "名称",
        "线路",
        "版本",
        "道路坐标"
    ],
    "segments": [
        "id",
        "线路",
        "版本",
        "起米",
        "止米"
    ],
    "letters": [
        "id",
        "日期",
        "角色",
        "版本",
        "抬头",
        "内容"
    ],
    "directives": [
        "id",
        "版本",
        "条文"
    ],
    "drawings": [
        "id",
        "标段",
        "线路",
        "版本",
        "范围",
        "依赖"
    ],
    "design_log": [
        "id",
        "日期",
        "图号",
        "事件",
        "内容"
    ],
    "crosswalk": [
        "id",
        "甲线路",
        "甲坐标",
        "乙线路",
        "乙坐标",
        "交接"
    ]
}
