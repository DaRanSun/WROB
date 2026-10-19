#!/usr/bin/env python3
"""把掌盟“资料 → 物品”页的截图变成装备库：给面板显示用的图标，和给识别用的模板。

    python tools/build_items.py "../Equip(raw)"

做三件事：
  1. 在每张截图里找出两列圆形图标切下来：每张截图先左列从上到下，再右列从上到下；翻页时重复截到的自动去重。
  2. 和 config/items.yaml 里的名字按顺序一一对上（数量对不上就报错，不会悄悄错位）。
  3. 写出 assets/items/<名字>.png（圆形、透明底，面板显示用）和 vision/templates/items.npz（识别用）。

游戏里装备栏的格子是 44×44 的方图，掌盟的圆形图标正好是它的内切圆，所以缩到 44 像素就能直接比对。
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
COLUMNS = [(40, 240), (615, 815)]       # 两列图标的横向范围（1264 宽的截图）
LIST_TOP, LIST_BOTTOM = 540, 2490       # 列表区域：去掉顶部页签和底部导航
ICON = 168                              # 掌盟里图标的直径
CELL = 44                               # 游戏里装备格的边长
SHOWN = 96                              # 存给面板用的图标边长


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


def icons_in(image: np.ndarray) -> list[np.ndarray]:
    """一张截图里完整露出来的图标，先左列后右列、各自从上到下。"""
    found = []
    for x0, x1 in COLUMNS:
        strip = image[LIST_TOP:LIST_BOTTOM, x0:x1]
        dark = strip.min(axis=2) < 225                      # 白底上不是白的地方
        for a, b in runs(dark.sum(axis=1) > 6):
            if b - a < 150:                                 # 被顶部或底部截掉一半的
                continue
            columns = np.flatnonzero(dark[a:b].sum(axis=0) > 6)
            cy = LIST_TOP + (a + b) // 2
            cx = x0 + (int(columns[0]) + int(columns[-1]) + 1) // 2
            found.append((cy, cx, image[cy - ICON // 2:cy + ICON // 2, cx - ICON // 2:cx + ICON // 2]))
    return found


def collect(folder: Path) -> list[np.ndarray]:
    """所有截图里不重复的图标，顺序是：截图按文件名，每张截图里先左列再右列，列内从上到下。"""
    unique, small = [], []
    files = sorted(folder.glob("*.jpg")) + sorted(folder.glob("*.png"))
    if not files:
        sys.exit(f"{folder} 里没有截图")
    for path in files:
        image = cv2.imread(str(path))
        if image is None or image.shape[1] != 1264:
            sys.exit(f"{path.name}：读不出来，或者不是 1264 宽的截图")
        for _, _, icon in icons_in(image):
            thumb = cv2.resize(icon, (48, 48), interpolation=cv2.INTER_AREA).astype(np.int16)
            if any(np.abs(thumb - seen).mean() < 6 for seen in small):
                continue
            unique.append(icon)
            small.append(thumb)
    return unique


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("screenshots", type=Path, help="放截图的文件夹")
    parser.add_argument("--sheet", type=Path, help="另存一张“序号 + 图标 + 名字”的对照图，用来人工核对有没有对错位")
    args = parser.parse_args()

    config = yaml.safe_load((ROOT / "config" / "items.yaml").read_text(encoding="utf-8"))
    names, excluded = config["items"], set(config.get("exclude") or [])
    if len(set(names)) != len(names):
        sys.exit("config/items.yaml 里有重复的名字")
    icons = collect(args.screenshots)
    if len(icons) != len(names):
        sys.exit(f"截图里切出 {len(icons)} 个图标，config/items.yaml 里有 {len(names)} 个名字，对不上")

    yy, xx = np.mgrid[:SHOWN, :SHOWN]
    alpha = np.clip((SHOWN / 2 - 1 - np.hypot(yy - SHOWN / 2 + 0.5, xx - SHOWN / 2 + 0.5)) * 255, 0, 255).astype(np.uint8)
    folder = ROOT / "assets" / "items"
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("*.png"):
        stale.unlink()
    kept, templates = [], []
    for name, icon in zip(names, icons):
        if name in excluded:
            continue
        shown = cv2.resize(icon, (SHOWN, SHOWN), interpolation=cv2.INTER_AREA)
        ok, data = cv2.imencode(".png", np.dstack([shown, alpha]))
        (folder / f"{name}.png").write_bytes(data.tobytes())            # 文件名是中文，不走 cv2.imwrite
        kept.append(name)
        templates.append(cv2.resize(icon, (CELL, CELL), interpolation=cv2.INTER_AREA))
    target = ROOT / "vision" / "templates" / "items.npz"
    np.savez_compressed(target, names=np.array(json.dumps(kept, ensure_ascii=False)), icons=np.stack(templates))
    print(f"{len(kept)} 件装备 → {folder}（图标）、{target}（识别模板）；不参与识别：{sorted(excluded)}")

    if args.sheet:
        per_row, width, height = 6, 300, 70
        rows = (len(names) + per_row - 1) // per_row
        sheet = np.full((rows * height, per_row * width, 3), 255, np.uint8)
        for index, icon in enumerate(icons):
            x, y = (index % per_row) * width, (index // per_row) * height
            sheet[y + 3:y + 67, x + 50:x + 114] = cv2.resize(icon, (64, 64), interpolation=cv2.INTER_AREA)
            cv2.putText(sheet, str(index), (x + 4, y + 42), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 200), 2)
        cv2.imwrite(str(args.sheet), sheet)
        print(f"对照图 → {args.sheet}（图上只有序号；第 n 个对应 items.yaml 里的第 n 个名字，从 0 数）")


if __name__ == "__main__":
    main()
