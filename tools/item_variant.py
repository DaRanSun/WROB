#!/usr/bin/env python3
"""给装备库加“游戏内的样子”：有的装备游戏里的图标和掌盟的不一样，一直认不出来（实战里卡莉斯塔的破败王者之刃）。
从录像里把那一格裁下来，存成这件装备的一个变体模板。

    python tools/item_variant.py add data/recordings/x.h264 --at 920 --cell red.3.5 --name 破败王者之刃
        录像第 920 秒的画面里，红方第 4 行（下路）第 6 格（由外到内数，0 是鞋子）就是破败王者之刃。
        挑一个这件装备没在冷却、完整亮着的时刻。裁下来的图存进 assets/items/variants/，然后自动重做模板。

    python tools/item_variant.py build
        按 assets/items/variants/ 里现有的图重做 vision/templates/items-variants.npz（删了图之后用）。

变体图的文件名是“装备名@序号.png”。改完要重启识别服务。
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision import layout as layout_mod  # noqa: E402
from vision.sources import H264File  # noqa: E402

FOLDER = ROOT / "assets" / "items" / "variants"
TARGET = ROOT / "vision" / "templates" / "items-variants.npz"


def known_items() -> list[str]:
    config = yaml.safe_load((ROOT / "config" / "items.yaml").read_text(encoding="utf-8"))
    excluded = set(config.get("exclude") or [])
    return [name for name in config["items"] if name not in excluded]


def build(_args=None) -> None:
    files = sorted(FOLDER.glob("*.png")) if FOLDER.is_dir() else []
    names, icons = [], []
    items = set(known_items())
    for path in files:
        name = path.stem.split("@")[0]
        if name not in items:
            sys.exit(f"{path.name}：装备库（config/items.yaml）里没有“{name}”")
        icons.append(cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR))
        names.append(name)
    size = layout_mod.load_layout()["items"]["size"]
    stack = np.stack(icons) if icons else np.zeros((0, size, size, 3), np.uint8)
    np.savez_compressed(TARGET, names=np.array(json.dumps(names, ensure_ascii=False)), icons=stack)
    print(f"{len(names)} 个变体 → {TARGET.relative_to(ROOT)}" + (f"：{'、'.join(sorted(set(names)))}" if names else ""))


def add(args) -> None:
    if args.name not in known_items():
        sys.exit(f"装备库（config/items.yaml）里没有“{args.name}”")
    side, row, slot = args.cell.split(".")
    layout = layout_mod.load_layout()
    for frame, _ in H264File(args.recording, fps=layout["stream"]["fps"], start=args.at, stop=args.at + 2):
        frame = layout_mod.to_reference(frame, layout)
        cell = layout_mod.item_cells(frame, layout)[f"{side}.{row}"][int(slot)]
        break
    else:
        sys.exit("录像里读不到这一秒的画面")
    FOLDER.mkdir(parents=True, exist_ok=True)
    number = len(list(FOLDER.glob(f"{args.name}@*.png"))) + 1
    path = FOLDER / f"{args.name}@{number}.png"
    cv2.imencode(".png", cell)[1].tofile(str(path))
    print(f"存了 {path.relative_to(ROOT)}")
    build()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    adder = commands.add_parser("add")
    adder.add_argument("recording", type=Path)
    adder.add_argument("--at", type=float, required=True, help="录像里的秒数")
    adder.add_argument("--cell", required=True, help="哪一格：blue.0.3 = 蓝方第 1 行（上路）由外到内第 4 格")
    adder.add_argument("--name", required=True, help="这件装备在装备库里的名字")
    adder.set_defaults(run=add)
    commands.add_parser("build").set_defaults(run=build)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
