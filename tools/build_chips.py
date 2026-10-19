#!/usr/bin/env python3
"""做野怪刷新牌子上“存活”两个字的模板（vision/templates/chips.npz）。

    python tools/build_chips.py data/recordings/replay-a.h264 --start 230 --stop 520

给一段录像和一个时间范围，要求这段时间里男爵的牌子一直写着“存活”（男爵刷新之后、被打掉之前）。
把这段时间里牌子文字区域的墨迹取平均，就是模板。换手机、游戏改了字体时重做。
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision import layout as layout_mod  # noqa: E402
from vision.objectives import icon_kind  # noqa: E402
from vision.sources import H264File  # noqa: E402
from vision.text import TEMPLATES, ink_grey  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recording")
    parser.add_argument("--start", type=float, required=True, help="录像里的秒数：从这里开始男爵的牌子写着“存活”")
    parser.add_argument("--stop", type=float, required=True)
    args = parser.parse_args()

    layout = layout_mod.load_layout()
    spec = layout["chips"]
    top = spec["top"]["upper"]
    (iy0, iy1), (ix0, ix1) = spec["icon"]["rows"], spec["icon"]["x"]
    (ty0, ty1), (tx0, tx1) = spec["text"]["rows"], spec["text"]["x"]
    inks = []
    for frame, _ in H264File(args.recording, fps=layout["stream"]["fps"], start=args.start, stop=args.stop, step=5):
        frame = layout_mod.to_reference(frame, layout)
        if icon_kind(frame[top + iy0:top + iy1, ix0:ix1]) == "baron":
            inks.append(ink_grey(frame[top + ty0:top + ty1, tx0:tx1]))
    if len(inks) < 20:
        sys.exit(f"这段时间里只有 {len(inks)} 帧看得到男爵的牌子，太少了；检查时间范围")
    alive = np.mean(inks, axis=0).astype(np.float32)
    spread = float(np.mean([np.abs(ink - alive).mean() for ink in inks]))
    if spread > 0.05:
        sys.exit("这段时间里牌子上的字在变（可能混进了倒计时）；把时间范围缩到只有“存活”的那一段")
    np.savez_compressed(TEMPLATES / "chips.npz", alive=alive)
    print(f"用了 {len(inks)} 帧，模板 {alive.shape[1]}×{alive.shape[0]}，写进 {TEMPLATES / 'chips.npz'}")


if __name__ == "__main__":
    main()
