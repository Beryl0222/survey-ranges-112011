"""读取并浏览交接资料原件。"""
import argparse
import json
from pathlib import Path


def read_collection(path):
    """加载一份 UTF-8 资料，保持录入顺序和文字原样。"""
    with path.open(encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError(f"{path.name} 应为记录对象的数组")
    return records


def main():
    parser = argparse.ArgumentParser(description="浏览原始资料")
    parser.add_argument("collection", nargs="?", help="资料集名称")
    parser.add_argument("--id", help="仅显示指定编号的所有原始记录")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent / "data"
    files = {path.stem: path for path in sorted(directory.glob("*.json"))}
    if not args.collection:
        if args.id:
            parser.error("按编号查看时需要指定资料集")
        for name, path in files.items():
            print(f"{name}，{len(read_collection(path))} 条")
        return
    if args.collection not in files:
        parser.error(f"资料集不存在：{args.collection}")
    records = read_collection(files[args.collection])
    if args.id is not None:
        records = [row for row in records if row.get("id") == args.id]
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
