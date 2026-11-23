"""命令行入口：浏览、核对、按图或按线路追踪。

用法示例：
    python3 -m briefing
    python3 -m briefing overview
    python3 -m briefing drawing G1
    python3 -m briefing line 正线 --from 300 --to 500
    python3 -m briefing facts --id B1
    python3 -m briefing questions
    python3 -m briefing boundary 正线 500
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assess import Assessment
from .corpus import Corpus
from .designlog import DesignLog
from .facts import FactBuilder
from .locate import Locator


def build(root):
    corpus = Corpus(root)
    locator = Locator(corpus)
    builder = FactBuilder(corpus, locator)
    design = DesignLog(corpus, locator)
    return Assessment(corpus, locator, builder, design)


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="briefing", description="交底定位与出图状态核对"
    )
    parser.add_argument(
        "--root", default=str(Path(__file__).resolve().parent.parent),
        help="案卷根目录（含 data/）",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("overview", help="列出全部图纸当前状态")

    drawing = sub.add_parser("drawing", help="完整追踪一张图")
    drawing.add_argument("id")

    line = sub.add_parser("line", help="按线路与里程段查看")
    line.add_argument("name")
    line.add_argument("--from", dest="low", type=float, default=None)
    line.add_argument("--to", dest="high", type=float, default=None)

    facts = sub.add_parser("facts", help="列出交底事实")
    facts.add_argument("--id", dest="letter_id", default=None)
    facts.add_argument("--unlocated", action="store_true", help="只看定位不唯一的事实")

    sub.add_parser("questions", help="列出全部定位问题")
    sub.add_parser("boundaries", help="列出标段交接点及会签状态")
    sub.add_parser("integrity", help="核对条款解释与资料是否一致")

    boundary = sub.add_parser("boundary", help="查看一个接合点")
    boundary.add_argument("name")
    boundary.add_argument("metres", type=float)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    assessment = build(args.root)

    if args.command == "overview":
        dump(assessment.overview())
    elif args.command == "drawing":
        dump(assessment.drawing(args.id))
    elif args.command == "line":
        dump(cut_by_line(assessment, args.name, args.low, args.high))
    elif args.command == "facts":
        rows = assessment.builder.all_facts()
        if args.letter_id:
            rows = [row for row in rows if row.letter_id == args.letter_id]
        if args.unlocated:
            rows = [row for row in rows if row.定位问题]
        dump([row.as_dict() for row in rows])
    elif args.command == "questions":
        dump(assessment.questions())
    elif args.command == "boundaries":
        dump(assessment.boundaries())
    elif args.command == "boundary":
        dump(assessment.junction(args.name, args.metres))
    elif args.command == "integrity":
        dump(assessment.integrity())
    return 0


def cut_by_line(assessment, name, low=None, high=None):
    """按线路（可再按里程段）汇总相关事实、图与接合点。"""
    facts = []
    for fact in assessment.builder.all_facts():
        if fact.线路 != name:
            continue
        if low is not None and fact.low is not None and fact.high < low:
            continue
        if high is not None and fact.low is not None and fact.low > high:
            continue
        facts.append(fact.as_dict())

    drawings = []
    for drawing_id, drawing in assessment.drawings.items():
        if drawing["线路"] != name:
            continue
        result = assessment.drawing(drawing_id)
        drawings.append({
            "图号": drawing_id,
            "标段": drawing["标段"],
            "版本": drawing["版本"],
            "范围": drawing["范围"],
            "状态": result["状态"],
            "摘要": result["摘要"],
        })

    return {
        "线路": name,
        "里程": {"低": low, "高": high},
        "事实": facts,
        "图纸": drawings,
        "接合点": [
            assessment.junction(name, row["坐标"])
            for row in assessment.boundaries() if row["线路"] == name
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
