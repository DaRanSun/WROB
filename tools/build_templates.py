#!/usr/bin/env python3
"""从录像里生成数字的字形模板（vision/templates/*.npz）。换手机、游戏改了字体时重做一遍。

分三步，中间需要人看一眼图、给每一类字形标上它是哪个字符：

    python tools/build_templates.py collect data/recordings/replay.h264
        从录像里把疑似单个字符的小块切出来，按形状聚类，存到 data/templates-work/
        （可以给多个录像；录像要覆盖到 0–9 每个数字）

    python tools/build_templates.py montage
        把每一类的平均字形画成图 data/templates-work/<组>.png，图上标着类的序号和出现次数

    python tools/build_templates.py label panel "3 7 / 0 - 1 …"
        按序号顺序给每一类标字符（- 表示丢弃），写出 vision/templates/panel.npz；
        同一个字符标了多次时，保留出现次数最多的几类作为变体

字形分七组：panel（底部面板）、top-towers / top-gold / top-kills（顶部条的塔数、经济、人头）、clock（时钟）、
respawn（两侧卡片上的复活倒计时）、level（头像右下角的等级）。
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision import layout as layout_mod  # noqa: E402
from vision.text import INKS, GlyphSet  # noqa: E402

WORK = ROOT / "data" / "templates-work"
MIN_WIDTH, MAX_WIDTH = 2, 30


def column_runs(on: np.ndarray) -> list[tuple[int, int]]:
    """布尔数组里连续为真的段 [起, 止)。"""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8), [0]))))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


def candidates(frame: np.ndarray, layout: dict) -> dict[str, list[np.ndarray]]:
    """一帧里所有疑似单个字符的墨迹小块，按字形组分开。切得不准没关系，切坏的会聚成很小的类。"""
    found = defaultdict(list)
    ink = INKS[layout["clock"]["ink"]](layout_mod.clock_field(frame, layout))
    for a, b in column_runs((ink > 0.5).sum(axis=0) >= 2):
        found[layout["clock"]["glyphs"]].append(ink[:, a:b])
    for name, roi in layout_mod.top_fields(frame, layout).items():
        spec = layout["top"]["fields"][name]
        ink = INKS[spec["ink"]](roi)
        for a, b in column_runs((ink > 0.5).sum(axis=0) >= 2):      # 顶部条字距大，按空列就能切开
            found[spec["glyphs"]].append(ink[:, a:b])
    cards = layout["cards"]
    for roi in layout_mod.card_fields(frame, layout).values():      # 复活倒计时：灰头像上的红色数字
        if not layout_mod.portrait_is_grey(roi):
            continue
        ink = INKS[cards["ink"]](roi)
        for a, b in column_runs((ink > 0.5).sum(axis=0) >= 2):
            found[cards["glyphs"]].append(ink[:, a:b])
    levels = layout["levels"]
    for roi in layout_mod.level_fields(frame, layout).values():     # 头像右下角的等级；压在头像上，会切出不少杂块
        ink = INKS[levels["ink"]](roi)
        for a, b in column_runs((ink > 0.5).sum(axis=0) >= 2):
            if 3 <= b - a <= 14:
                found[levels["glyphs"]].append(ink[:, a:b])
    if layout_mod.panel_visible(frame, layout):
        bright = INKS[layout["panel"]["ink"]]
        for roi in layout_mod.panel_fields(frame, layout).values():
            ink = bright(roi)
            for a, b in column_runs(ink.sum(axis=0) > 1.6):         # 面板的字会粘连，按“这一列墨迹很少”来切
                found[layout["panel"]["glyphs"]].append(ink[:, a:b])
    return found


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def cluster(patches: list[np.ndarray], threshold: float = 0.9) -> list[tuple[np.ndarray, int]]:
    """同宽度的小块按相关系数贪心聚类。返回 [(平均字形, 个数)]，个数多的在前。"""
    groups: list[dict] = []
    for patch in patches:
        if not MIN_WIDTH <= patch.shape[1] <= MAX_WIDTH:
            continue
        best, best_score = None, threshold
        for group in groups:
            if group["sum"].shape == patch.shape:
                score = correlation(group["sum"] / group["n"], patch)
                if score > best_score:
                    best, best_score = group, score
        if best is None:
            groups.append({"sum": patch.astype(np.float64).copy(), "n": 1})
        else:
            best["sum"] += patch
            best["n"] += 1
    groups.sort(key=lambda group: -group["n"])
    return [((group["sum"] / group["n"]).astype(np.float32), group["n"]) for group in groups]


def collect(args) -> None:
    from vision.sources import H264File

    layout = layout_mod.load_layout()
    patches = defaultdict(list)
    frames = 0
    for frame, _ in H264File(args.recording, step=args.step, start=args.start, stop=args.stop):
        frames += 1
        for group, items in candidates(layout_mod.to_reference(frame, layout), layout).items():
            if not args.only or group in args.only:
                patches[group].extend(items)
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"{frames} 帧")
    for group, items in sorted(patches.items()):
        clusters = [(mean, n) for mean, n in cluster(items) if n >= max(3, len(items) // 400)]
        np.savez_compressed(WORK / f"{group}.npz", counts=np.array([n for _, n in clusters]),
                            **{f"c{index:03d}": mean for index, (mean, _) in enumerate(clusters)})
        print(f"{group:10s} {len(items):6d} 个小块 → 保留 {len(clusters)} 类：{[n for _, n in clusters]}")


def load_clusters(group: str) -> list[tuple[np.ndarray, int]]:
    path = WORK / f"{group}.npz"
    if not path.is_file():
        sys.exit(f"没有 {path}：先运行 collect")
    with np.load(path) as data:
        return [(data[f"c{index:03d}"], int(n)) for index, n in enumerate(data["counts"])]


def montage(args) -> None:
    import cv2

    scale, per_row = 6, 12
    for path in sorted(WORK.glob("*.npz")):
        if args.only and path.stem not in args.only:
            continue
        clusters = load_clusters(path.stem)
        height = clusters[0][0].shape[0]
        cell_w, cell_h = MAX_WIDTH * scale + 16, height * scale + 34
        rows = (len(clusters) + per_row - 1) // per_row
        sheet = np.full((rows * cell_h, per_row * cell_w, 3), 40, np.uint8)
        for index, (mean, count) in enumerate(clusters):
            x, y = (index % per_row) * cell_w + 8, (index // per_row) * cell_h
            tile = cv2.resize((mean * 255).astype(np.uint8), None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            sheet[y + 30:y + 30 + tile.shape[0], x:x + tile.shape[1]] = tile[..., None]
            cv2.putText(sheet, f"#{index} n={count}", (x, y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 220, 255), 1)
        cv2.imwrite(str(path.with_suffix(".png")), sheet)
        print(f"{path.stem}: {len(clusters)} 类 → {path.with_suffix('.png')}")


def label(args) -> None:
    clusters = load_clusters(args.group)
    labels = args.labels.split()
    if len(labels) > len(clusters):
        sys.exit(f"标了 {len(labels)} 个，但只有 {len(clusters)} 类")
    glyphs, variants = {}, defaultdict(list)
    for (mean, count), name in zip(clusters, labels):          # 已按个数从多到少排好
        if name == "-" or len(variants[name]) >= args.variants:
            continue
        columns = np.flatnonzero(mean.max(axis=0) > 0.3)        # 贴着墨迹裁掉两侧的空列
        key = name if not variants[name] else f"{name}#{len(variants[name])}"
        glyphs[key] = mean[:, columns[0]:columns[-1] + 1]
        variants[name].append((count, glyphs[key].shape[1]))
    for pair in args.rotated or []:                            # 录像里没出现过的字，用形状相同的字转 180° 顶上
        new, old = pair.split("=")
        if new in glyphs or old not in glyphs:
            continue
        source = glyphs[old]
        rows = np.flatnonzero(source.max(axis=1) > 0.3)
        turned = np.zeros_like(source)
        turned[rows[0]:rows[-1] + 1] = source[rows[0]:rows[-1] + 1][::-1, ::-1]
        glyphs[new] = turned
        variants[new].append((0, turned.shape[1]))
    path = GlyphSet(glyphs).save(args.group)
    print(f"{args.group}: {len(variants)} 个字符、{len(glyphs)} 个字形 → {path}")
    print("   " + "  ".join(f"{name}:" + "/".join(f"宽{w}×{n}" for n, w in items) for name, items in sorted(variants.items())))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("collect")
    p.add_argument("recording", type=Path)
    p.add_argument("--step", type=int, default=5, help="每隔几帧取一帧（录像是 10 帧/秒）")
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--stop", type=float)
    p.add_argument("--only", action="append", metavar="组", help="只收集这一组（可以给多次），其他组留着不动")
    p.set_defaults(run=collect)
    p = commands.add_parser("montage")
    p.add_argument("--only", action="append", metavar="组")
    p.set_defaults(run=montage)
    p = commands.add_parser("label")
    p.add_argument("group")
    p.add_argument("labels")
    p.add_argument("--variants", type=int, default=3, help="同一个字符最多保留几个变体")
    p.add_argument("--rotated", action="append", metavar="新=旧",
                   help="录像里没采到“新”这个字时，用“旧”转 180° 代替，例如 9=6（次数显示为 0）")
    p.set_defaults(run=label)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
