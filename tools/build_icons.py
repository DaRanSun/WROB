#!/usr/bin/env python3
"""召唤师技能和基石符文的图标库：assets/spells/<名字>.png、assets/runes/<名字>.png（面板显示用的也是这些图）。

图标有两个来源：掌盟“资料 → 技能 / 符文”页的截图（圆形大图，名字按顺序写在 config/icons.yaml），
以及从对局画面的选手卡片上直接裁下来的 33×33 小图（截图里没有的，比如“解封的传送”）。

    python tools/build_icons.py import ../spellrune
        把截图里的图标切出来，按 config/icons.yaml 的名字存进 assets/spells/、assets/runes/，然后重做模板。

    python tools/build_icons.py cut data/recordings/live.h264 --at 636 --out /tmp/icons
        把录像第 636 秒画面里十名选手的技能和符文都裁出来（blue-0-spell-0.png、red-3-rune.png …）。
        挑出库里还没有的，改成它的名字，放进 assets/spells/ 或 assets/runes/。要挑没在冷却的（亮的）。

    python tools/build_icons.py build
        按这两个文件夹重做识别模板 vision/templates/spells.npz、runes.npz。加了、改了图标之后运行。
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

GROUPS = ("spells", "runes")
COLUMN = (30, 260)                      # 掌盟列表里图标所在的那一列
LIST_TOP, LIST_BOTTOM = 420, 2520       # 去掉顶部页签和底部导航
ICON = 168                              # 掌盟里图标的直径
SHOWN = 96                              # 存给面板用的图标边长
# 掌盟的圆形图标缩到多大，才和选手卡片上 33×33 的格子对得上（实测：技能 32，符文 33）
FIT = {"spells": 32, "runes": 33}


def read(path: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)   # 文件名是中文，不能直接 imread


def write(path: Path, image: np.ndarray) -> None:
    cv2.imencode(".png", image)[1].tofile(str(path))


def icons_in(image: np.ndarray) -> list[np.ndarray]:
    """一张截图里的图标，从上到下。被屏幕边缘截掉一部分的也算一个（靠 config/icons.yaml 里的 skip 跳过）。"""
    x0, x1 = COLUMN
    dark = image[LIST_TOP:LIST_BOTTOM, x0:x1].min(axis=2) < 225
    rows = np.concatenate(([0], (dark.sum(axis=1) > 20).astype(np.int8), [0]))
    edges = np.flatnonzero(np.diff(rows))
    found = []
    for top, bottom in zip(edges[::2], edges[1::2]):
        if bottom - top < 120:
            continue
        left = int(np.flatnonzero(dark[top:bottom].sum(axis=0) > 10)[0]) + x0
        found.append(image[LIST_TOP + top:LIST_TOP + top + ICON, left:left + ICON])
    return found


def round_icon(icon: np.ndarray) -> np.ndarray:
    """圆形、透明底的图标。"""
    small = cv2.resize(icon, (SHOWN, SHOWN), interpolation=cv2.INTER_AREA)
    yy, xx = np.mgrid[:SHOWN, :SHOWN]
    distance = np.hypot(yy - (SHOWN - 1) / 2, xx - (SHOWN - 1) / 2)
    alpha = np.clip((SHOWN / 2 - 1 - distance) * 255, 0, 255).astype(np.uint8)
    return np.dstack([small, alpha])


def import_screenshots(args) -> None:
    files = sorted(path for path in args.folder.iterdir() if path.suffix.lower() in (".jpg", ".jpeg", ".png"))
    icons = [icon for path in files for icon in icons_in(read(path))]
    with open(ROOT / "config" / "icons.yaml", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    names = [(group, name) for group in GROUPS for name in config[group]]
    if len(names) != len(icons):
        sys.exit(f"截图里找到 {len(icons)} 个图标，config/icons.yaml 里写了 {len(names)} 个名字，对不上")
    for (group, name), icon in zip(names, icons):
        if name == "skip":
            continue
        if icon.shape[:2] != (ICON, ICON):
            sys.exit(f"{name} 没截全，换一张截图，或者在 config/icons.yaml 里写 skip")
        (ROOT / "assets" / group).mkdir(parents=True, exist_ok=True)
        write(ROOT / "assets" / group / f"{name}.png", round_icon(icon))
    build(args)


def template(image: np.ndarray, group: str, size: int) -> np.ndarray:
    """一张图标 → 识别用的 size×size 模板。选手卡片上裁下来的小图原样用；掌盟的大图缩到 FIT 那么大再补齐。"""
    if image.shape[2] == 4:                                 # 透明底的，垫成黑底
        image = (image[..., :3] * (image[..., 3:] / 255.0)).astype(np.uint8)
    if image.shape[:2] == (size, size):
        return image
    fit = FIT[group]
    out = np.zeros((size, size, 3), np.uint8)
    out[:fit, :fit] = cv2.resize(image, (fit, fit), interpolation=cv2.INTER_AREA)[:size, :size]
    return out


def cut(args) -> None:
    capture = cv2.VideoCapture(str(args.recording))
    capture.set(cv2.CAP_PROP_POS_MSEC, args.at * 1000)
    frame = None
    for _ in range(int(args.at * 10) + 5 if not capture.get(cv2.CAP_PROP_POS_MSEC) else 1):     # 裸 H.264 不能跳转时逐帧读
        ok, frame = capture.read()
        if not ok:
            break
    if frame is None:
        sys.exit("读不出这一帧")
    layout = layout_mod.load_layout()
    frame = layout_mod.to_reference(frame, layout)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, roi in layout_mod.spell_cells(frame, layout).items():
        write(args.out / f"{name.replace('.', '-')}.png", roi)
    print(f"裁了 30 张到 {args.out}")


def build(args) -> None:
    size = layout_mod.load_layout()["spells"]["size"]
    for group in GROUPS:
        files = sorted((ROOT / "assets" / group).glob("*.png"))
        if not files:
            sys.exit(f"assets/{group}/ 里没有图标")
        icons = np.stack([template(read(path), group, size) for path in files])
        names = [path.stem for path in files]
        np.savez_compressed(ROOT / "vision" / "templates" / f"{group}.npz", icons=icons,
                            names=np.array(json.dumps(names, ensure_ascii=False)))
        print(f"{group}: {len(names)} 个 —— {'、'.join(names)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    cutter = commands.add_parser("cut")
    cutter.add_argument("recording", type=Path)
    cutter.add_argument("--at", type=float, required=True, help="录像里的秒数")
    cutter.add_argument("--out", type=Path, required=True)
    cutter.set_defaults(run=cut)
    commands.add_parser("build").set_defaults(run=build)
    importer = commands.add_parser("import")
    importer.add_argument("folder", type=Path, help="放掌盟截图的文件夹")
    importer.set_defaults(run=import_screenshots)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
