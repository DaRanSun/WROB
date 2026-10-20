#!/usr/bin/env python3
"""BP 识别用的英雄头像模板（vision/templates/champions.npz）。

    python tools/build_bp_icons.py build
        把 assets/bp-icons/<英雄 slug>.png 打包成模板。

    python tools/build_bp_icons.py titles
        从 bprecs/ 的两段录屏里切出各个阶段的标题（禁用阶段、蓝方选择英雄……）和“⊘”空禁用图标，
        写成 vision/templates/bp-titles.npz。切哪一帧写在下面的 TITLES 里。

    python tools/build_bp_icons.py countdown
        标题下面的读秒数字（0–9）的模板，写成 vision/templates/bp-countdown.npz。切哪些时刻、那时屏幕上是几，
        写在下面的 COUNTDOWN 里（每秒 4 帧逐帧看过）。

    python tools/build_bp_icons.py extract bprecs/allchamp.mp4
        从“选英雄格子从头滚到尾”的录屏里，把模板里还没有的头像裁出来，存到 data/bp-icons-new/，
        文件名先写成 WRPedia 头像里最像的那个英雄（猜的，十几个手游头像和 WRPedia 不同，会猜错），
        旁边配一张格子下面写的名字（*-name.png）。人看一遍，改对文件名，挪进 assets/bp-icons/，再 build。

出了新英雄、换了头像时走 extract。现有的 142 张是 10-20 从 allchamp.mp4（1280×576）裁的，每张 66×66。
格子的位置按 1280×576 写（列 x 420/512/604/696/787，方格 72，行距 96，上下随滚动变）。
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RECORDINGS = ROOT.parent / "bprecs"           # 用户 10-20 录的：picking.mp4（3 Ban 全流程）、allchamp.mp4（5 Ban，滚了一遍全英雄）
# (录屏, 第几秒, 标题)。会闪一下变亮的标题两种样子都切。
TITLES = [
    ("picking.mp4", 0.0, "预选阶段"), ("picking.mp4", 0.75, "预选阶段"), ("picking.mp4", 5.0, "禁用阶段"),
    ("picking.mp4", 14.0, "禁用显示"), ("picking.mp4", 14.75, "禁用显示"), ("picking.mp4", 19.25, "选择你的英雄!"),
    ("picking.mp4", 25.0, "蓝方选择英雄"), ("picking.mp4", 33.0, "红方选择英雄"), ("picking.mp4", 50.0, "准备阶段"),
    ("allchamp.mp4", 10.0, "蓝方禁用英雄"), ("allchamp.mp4", 22.25, "蓝方禁用英雄"), ("allchamp.mp4", 30.0, "红方禁用英雄"),
    # 数据机的实际画面（2800×1264）比 1280 的录屏清晰得多，只拿录屏做的模板相关只有 0.82（10-20 晚直播时卡在这里）
    ("实机-蓝方选择英雄.png", 0, "蓝方选择英雄"),
]
SCREENSHOTS = ROOT / "data" / "screenshots" / "BP"   # 数据机上 adb screencap 的整屏截图
EMPTY_AT = ("picking.mp4", 2.5)                # 这一帧左上角第一个禁用圆圈是空的（⊘）
# (录屏, 第几秒, 那时的读秒)。都取在两次跳秒的正中间。
COUNTDOWN = [("picking.mp4", t + 0.5, str(v)) for t, v in zip(range(2, 14), range(28, 16, -1))] + [
    ("picking.mp4", 14.0, "4"), ("picking.mp4", 14.75, "3"), ("picking.mp4", 15.75, "2"), ("picking.mp4", 16.75, "1"),
    ("picking.mp4", 18.0, "0"), ("picking.mp4", 26.75, "16"), ("picking.mp4", 27.75, "15"), ("picking.mp4", 28.75, "14"),
    ("picking.mp4", 29.75, "13"), ("picking.mp4", 31.25, "24"), ("picking.mp4", 34.25, "21"),
] + [("allchamp.mp4", 9.75 - k, str(18 + k)) for k in range(9)] + [
    ("allchamp.mp4", t + 0.5, str(v)) for t, v in zip([x + 0.25 for x in range(10, 27)], range(17, 0, -1))] + [
    ("allchamp.mp4", 27.75, "0"), ("allchamp.mp4", 29.0, "29"), ("allchamp.mp4", 54.25, "30"),
]
FOLDER = ROOT / "assets" / "bp-icons"
NEW = ROOT / "data" / "bp-icons-new"
TARGET = ROOT / "vision" / "templates" / "champions.npz"
COLUMNS = (420, 512, 604, 696, 787)
SQUARE, PITCH, GRID = 72, 96, (98, 470)


def build() -> None:
    files = sorted(FOLDER.glob("*.png"))
    names = [path.stem for path in files]
    icons = [cv2.imread(str(path)) for path in files]
    size = icons[0].shape[0]
    icons = [icon if icon.shape[0] == size else cv2.resize(icon, (size, size), interpolation=cv2.INTER_AREA) for icon in icons]
    np.savez_compressed(TARGET, names=json.dumps(names), icons=np.stack(icons))
    print(f"{len(names)} 个英雄 → {TARGET.relative_to(ROOT)}")


def grid_squares(frame: np.ndarray):
    """一帧格子里所有完整的方格（去掉 3 像素的描边）和它下面的名字。行的上下位置按方格的上下边沿找。"""
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(int)
    profile = np.zeros(frame.shape[0])
    for x in COLUMNS:
        profile[1:] += np.abs(np.diff(grey[:, x + 6:x + SQUARE - 6], axis=0)).sum(axis=1)
    offset = max(range(PITCH), key=lambda o: sum(profile[y] + profile[y + SQUARE] for y in range(o, frame.shape[0] - SQUARE - 1, PITCH)))
    for top in range(offset, GRID[1] - SQUARE, PITCH):
        if top < GRID[0]:
            continue
        for x in COLUMNS:
            square = frame[top + 3:top + SQUARE - 3, x + 3:x + SQUARE - 3]
            if square.std() >= 20:
                yield square, frame[top + SQUARE:top + SQUARE + 20, x - 10:x + SQUARE + 10]


def extract(video: Path) -> None:
    from vision.champions import ChampionMatcher, resize, vector, SIZE
    matcher = ChampionMatcher() if TARGET.is_file() else None
    from state.catalog import Catalog, load_config
    catalog = Catalog(load_config("app"))
    wrpedia = {}
    for champion in catalog.champions.values():
        image = cv2.imread(str(ROOT.parent / "WRPedia" / champion["icon"].replace("/wrpedia/", "")))
        if image is not None:
            wrpedia[champion["slug"]] = vector(resize(image, SIZE))
    NEW.mkdir(parents=True, exist_ok=True)
    seen, capture = [], cv2.VideoCapture(str(video))
    count = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        for square, name in grid_squares(cv2.resize(frame, (1280, 576), interpolation=cv2.INTER_AREA)):
            if matcher is not None and matcher.match(square):
                continue
            v = vector(resize(square, SIZE))
            if any(float(other @ v) > 0.9 for other in seen):
                continue
            seen.append(v)
            guess = max(wrpedia, key=lambda slug: float(wrpedia[slug] @ v))
            cv2.imwrite(str(NEW / f"{guess}.png"), square)
            cv2.imwrite(str(NEW / f"{guess}-name.png"), name)
            count += 1
    print(f"新头像 {count} 个 → {NEW.relative_to(ROOT)}（文件名是猜的，请核对）")


def frame_at(video: str, seconds: float) -> np.ndarray:
    if video.endswith(".png"):
        return cv2.imread(str(SCREENSHOTS / video))
    capture = cv2.VideoCapture(str(RECORDINGS / video))
    capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = capture.read()
    if not ok:
        sys.exit(f"{video} 第 {seconds} 秒读不出来")
    return frame


def titles() -> None:
    """标题和空禁用图标都在数据机画面（2800×1264）的坐标上切，和识别时一样处理（vision/bp.py 的 title_ink、empty_ink）。"""
    from vision import bp
    from vision.layout import load_layout, to_reference
    layout = load_layout()
    labels, images = [], []
    for video, seconds, label in TITLES:
        frame = to_reference(frame_at(video, seconds), layout)         # 和识别时一样：原样交给 to_reference 放大
        labels.append(label)
        images.append(bp.title_ink(frame, layout))
    video, seconds = EMPTY_AT
    frame = to_reference(frame_at(video, seconds), layout)
    empty = bp.empty_ink(bp.ban_circles(frame, layout)["blue"][0])
    np.savez_compressed(TARGET.parent / "bp-titles.npz", labels=json.dumps(labels, ensure_ascii=False),
                        titles=np.stack(images), empty=empty)
    print(f"{len(labels)} 个标题模板（{len(set(labels))} 种）+ 空禁用图标 → vision/templates/bp-titles.npz")


def countdown() -> None:
    """切读秒的数字。宽度差两列以上的同一个数字（“1”有好几种写法）分成不同的模板（"1"、"1#1"…）。"""
    from vision import bp
    from vision.layout import load_layout, to_reference
    from vision.stats import characters
    layout = load_layout()
    samples = {}
    for video, seconds, text in COUNTDOWN:
        ink = bp.countdown_ink(to_reference(frame_at(video, seconds), layout), layout)
        pieces = [piece for piece in characters(ink) if piece.shape[1] >= 4]
        if len(pieces) != len(text):
            sys.exit(f"{video} 第 {seconds} 秒：切出 {len(pieces)} 个字，和“{text}”对不上")
        for piece, char in zip(pieces, text):
            groups = samples.setdefault(char, [])
            group = next((g for g in groups if abs(g[0].shape[1] - piece.shape[1]) <= 1), None)
            if group is None:
                groups.append([piece])
            else:
                group.append(piece)
    glyphs = {}
    for char, groups in sorted(samples.items()):
        for number, group in enumerate(sorted(groups, key=len, reverse=True)):
            width = max(set(p.shape[1] for p in group), key=[p.shape[1] for p in group].count)
            glyphs[char if number == 0 else f"{char}#{number}"] = np.mean([p for p in group if p.shape[1] == width], axis=0)
    missing = set("0123456789") - set(samples)
    if missing:
        sys.exit(f"缺这些数字：{' '.join(sorted(missing))}")
    np.savez_compressed(TARGET.parent / "bp-countdown.npz", **glyphs)
    print(f"读秒数字 {len(glyphs)} 个模板（{', '.join(sorted(glyphs))}）→ vision/templates/bp-countdown.npz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["build", "extract", "titles", "countdown"])
    parser.add_argument("video", nargs="?", type=Path)
    args = parser.parse_args()
    if args.action == "build":
        build()
    elif args.action == "titles":
        titles()
    elif args.action == "countdown":
        countdown()
    else:
        extract(args.video)


if __name__ == "__main__":
    main()
