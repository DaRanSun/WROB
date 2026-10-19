#!/usr/bin/env python3
"""从结算页截图生成“统计 · 数据对比”页的字形模板和表头模板（vision/templates/stats.npz、stats-header.npz）。

    python tools/build_stats.py
        读 data/screenshots/结算/ 里的截图和人工抄的数（数据对比.yaml），切出每个字，同一个字取平均当模板；
        再拿生成的模板把每张截图从头读一遍，和抄的数逐项对。

换手机、游戏改了这一页的字体时：在新手机上截几张这一页（2800×1264 横屏），把数抄进 数据对比.yaml，重跑。
截图里要凑齐 0–9 和 %。
"""
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision import layout as layout_mod  # noqa: E402
from vision.stats import (COLUMNS, HALVES, KEYS, SHARED, StatsReader, characters, field_ink,  # noqa: E402
                          header_strip, split_words)
from vision.text import TEMPLATES, GlyphSet  # noqa: E402

SHOTS = ROOT / "data" / "screenshots" / "结算"


def expected_words(row: dict) -> dict[str, list[str]]:
    words = {}
    for column in COLUMNS:
        if column in SHARED:
            words[column] = [str(row[column]), f"{row[SHARED[column]]}%"]
        elif column == "share":
            words[column] = [f"{row['share']}%"]
        else:
            words[column] = [str(row[column])]
    return words


def load_truth() -> dict:
    with open(SHOTS / "数据对比.yaml", encoding="utf-8") as fh:
        truth = yaml.safe_load(fh)
    return {name: {half: [dict(zip(KEYS, row)) for row in halves[half]] for half in HALVES}
            for name, halves in truth.items()}


def main() -> None:
    layout = layout_mod.load_layout()
    truth = load_truth()
    frames = {name: cv2.imread(str(SHOTS / name)) for name in truth}
    samples = defaultdict(list)
    for name, halves in truth.items():
        frame = layout_mod.to_reference(frames[name], layout)
        for half in HALVES:
            for index, row in enumerate(halves[half]):
                for column, texts in expected_words(row).items():
                    ink = field_ink(frame, layout, half, index, column)
                    words = split_words(ink, layout["stats"]["word_gap"]) if ink is not None else []
                    pieces = [characters(word) for word in words]
                    if len(words) != len(texts) or any(len(p) != len(t) for p, t in zip(pieces, texts)):
                        sys.exit(f"{name} {half} 第 {index + 1} 行 {column}：切出来的字数和 {texts} 对不上")
                    for chars, text in zip(pieces, texts):
                        for glyph, char in zip(chars, text):
                            samples[char].append(glyph)
    missing = set("0123456789%") - set(samples)
    if missing:
        sys.exit(f"截图里缺这些字：{' '.join(sorted(missing))}")
    glyphs = {}
    for char, items in sorted(samples.items()):
        widths = [item.shape[1] for item in items]
        width = max(set(widths), key=widths.count)              # 最常见的宽度
        glyphs[char] = np.mean([item for item in items if item.shape[1] == width], axis=0).astype(np.float32)
        print(f"  {char}  {len(items):3d} 个样本，宽 {width}")
    path = GlyphSet(glyphs).save(layout["stats"]["glyphs"])
    header = np.mean([header_strip(layout_mod.to_reference(frame, layout), layout["stats"]["header"])
                      for frame in frames.values()], axis=0).astype(np.float32)
    np.savez_compressed(TEMPLATES / "stats-header.npz", header=header)
    print(f"写出 {path.relative_to(ROOT)} 和 stats-header.npz")

    reader, wrong = StatsReader(layout), 0
    for name, halves in truth.items():
        reading = reader.read(frames[name])
        if reading is None:
            sys.exit(f"{name}：表头对不上模板（{reader.header_score(frames[name]):.2f}）")
        for half in HALVES:
            for index, row in enumerate(halves[half]):
                for key in KEYS:
                    if reading[half][index][key] != row[key]:
                        wrong += 1
                        print(f"  读错 {name} {half} 第 {index + 1} 行 {key}：{reading[half][index][key]} ≠ {row[key]}")
        print(f"  {name}：表头相关 {reader.header_score(frames[name]):.2f}，完整且自洽 {reading['complete']}")
    print("全部读对" if not wrong else f"读错 {wrong} 项")


if __name__ == "__main__":
    main()
