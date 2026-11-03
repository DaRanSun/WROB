#!/usr/bin/env python3
"""做多杀播报的模板（vision/templates/announce.npz）：“连破”两个字，前面的数字“二 / 三 / 四 / 五”，
和开头的“蓝 / 红”（哪一方）。

模板来自录像里真实的播报。挑一个播报完整显示、字最清楚的时刻：

    python tools/build_announce.py add data/recordings/x.h264 --at 553.6 --count 2
        录像第 553.6 秒画面上是一条“X方二连破”。它的“连破”、“二”和开头的“X”存进 data/announce-samples/，
        然后重做模板。字按颜色找（粉白 / 浅青哪种多用哪种）；写的是哪一方默认也按颜色猜，
        皮肤自带的播报颜色和阵营对不上时用 --side 指明字上写的是哪一方（比如粉色字的“蓝方三连破”：--side blue）。

    python tools/build_announce.py build
        按 data/announce-samples/ 里已有的样本重做模板。

游戏里还没见过的数字（现在是“五”）用系统字体渲染一个补上（要 base 环境的 Pillow，见 render_missing）；
录到真的五连破之后用 add 补进去，渲染的就不再用了。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision import announce  # noqa: E402
from vision import layout as layout_mod  # noqa: E402
from vision.sources import H264File  # noqa: E402

SAMPLES = ROOT / "data" / "announce-samples"
TARGET = ROOT / "vision" / "templates" / "announce.npz"
LABELS = {2: "二", 3: "三", 4: "四", 5: "五"}
LIANPO_WIDTH = 76           # “连破”两个字在缩放后的宽度
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def frame_at(recording: Path, at: float) -> np.ndarray:
    layout = layout_mod.load_layout()
    for frame, _ in H264File(recording, fps=layout["stream"]["fps"], start=at, stop=at + 1):
        return layout_mod.to_reference(frame, layout)
    sys.exit("录像里读不到这一秒的画面")


def locate(line: np.ndarray) -> int:
    """一行字里“连破”从哪一列开始：最后两个汉字（去掉末尾的感叹号）。"""
    columns = np.flatnonzero(line.sum(axis=0) > 0.5)
    right = columns[-1] + 1
    if line[:, right - 8:right].sum() < line[:, right - 40:right - 8].sum() * 0.15:      # 末尾是窄窄的“!”
        right = np.flatnonzero(line[:, :right - 12].sum(axis=0) > 0.5)[-1] + 1
    return right - LIANPO_WIDTH


def add(args) -> None:
    layout = layout_mod.load_layout()
    spec = layout["announce"]
    frame = frame_at(args.recording, args.at)
    strip = frame[spec["rows"][0]:spec["rows"][1], spec["x"][0]:spec["x"][1]]
    ink = max(announce.INKS, key=lambda name: announce.INKS[name](strip).sum())
    side = args.side or ink
    lines = announce.text_lines(announce.INKS[ink](strip))
    if not lines:
        sys.exit("这一帧上找不到播报的那一行字")
    # 上下几条候选里，字的墨迹最集中在中间的那一条（上下边缘留白最多）
    line = max(lines, key=lambda candidate: candidate[3:-3].sum() - candidate[:3].sum() - candidate[-3:].sum())
    at = locate(line)
    lianpo = line[:, at:at + LIANPO_WIDTH]
    left = at + announce.DIGIT_SPAN[0]
    digit = line[:, left:left + announce.DIGIT_SPAN[1] - announce.DIGIT_SPAN[0]]
    team = line[:, max(0, at + announce.TEAM_SPAN[0]):at + announce.TEAM_SPAN[1]]
    SAMPLES.mkdir(parents=True, exist_ok=True)
    name = f"{args.count}-{side}-{args.recording.stem}-{args.at:.1f}.npz"
    np.savez_compressed(SAMPLES / name, lianpo=lianpo, digit=digit, count=args.count, team=team, side=side, ink=ink)
    print(f"存了 {name}（{side} 方的播报，字是{'粉白' if ink == 'red' else '浅青'}色的）")
    build(args)


def first_glyph(region: np.ndarray) -> np.ndarray | None:
    """开头那个字（“蓝”或“红”）：从左往右第一处连着三列都有墨迹的地方起，取 TEAM_WIDTH 列。
    左边偶尔有几点零星的墨迹（底板上的闪光），一列只有一两个点，不算。"""
    solid = region.sum(axis=0) >= 3
    for x in range(len(solid) - 2):
        if solid[x:x + 3].all():
            glyph = region[:, x:x + announce.TEAM_WIDTH]
            return glyph if glyph.shape[1] == announce.TEAM_WIDTH else None
    return None


def render_missing(missing: list[int], size: tuple[int, int]) -> dict[int, np.ndarray]:
    """用系统字体渲染还没有真实样本的数字。Pillow 不在 wrob 环境里，借 base 环境的 Python 跑一下。"""
    if not missing:
        return {}
    script = (
        "import sys, json, numpy as np\nfrom PIL import Image, ImageDraw, ImageFont\n"
        f"font = ImageFont.truetype({FONT!r}, 120, index=1)\nout = {{}}\n"
        "for ch in sys.argv[2]:\n"
        "    img = Image.new('L', (200, 200), 0); ImageDraw.Draw(img).text((20, 10), ch, fill=255, font=font)\n"
        "    a = np.array(img) > 127; ys, xs = np.nonzero(a)\n"
        "    out['c%d' % ord(ch)] = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]\n"
        "np.savez(sys.argv[1], **out)\n")
    temp = SAMPLES / "_rendered.npz"
    chars = "".join(LABELS[count] for count in sorted(LABELS))
    subprocess.run(["/opt/anaconda3/bin/python", "-c", script, str(temp), chars], check=True)
    with np.load(temp) as data:
        glyphs = {count: data[f"c{ord(LABELS[count])}"].astype(np.float32) for count in LABELS}
    temp.unlink()
    height, width = size
    out = {}
    for count, glyph in glyphs.items():
        # 游戏里的数字大约占格子的 30×32；渲染字缩到这么大、居中放进格子
        box = np.zeros(size, np.float32)
        gh, gw = 30, min(width - 2, round(glyph.shape[1] * 30 / glyph.shape[0]))
        small = announce.resize(glyph, gw, gh)
        top, left = (height - gh) // 2, (width - gw) // 2
        box[top:top + gh, left:left + gw] = small
        out[count] = box
    return out


def build(_args=None) -> None:
    files = sorted(SAMPLES.glob("*.npz")) if SAMPLES.is_dir() else []
    files = [path for path in files if not path.name.startswith("_")]
    if not files:
        sys.exit(f"{SAMPLES} 里还没有样本：先用 add 从录像里加")
    lianpo, digits, labels, rendered_flags, teams, team_labels = [], [], [], [], [], []
    for path in files:
        with np.load(path) as data:
            lianpo.append(announce.resize(data["lianpo"], LIANPO_WIDTH, announce.HEIGHT))
            width = announce.DIGIT_SPAN[1] - announce.DIGIT_SPAN[0]
            digits.append(announce.resize(data["digit"], width, announce.HEIGHT))
            labels.append(str(int(data["count"])))
            rendered_flags.append(False)
            glyph = first_glyph(data["team"]) if "team" in data else None
            if glyph is not None:
                teams.append(glyph.astype(np.float32))
                team_labels.append(str(data["side"]))
    if len(set(team_labels)) < 2:
        sys.exit("样本里要有蓝方、红方的播报各至少一条（旧样本没存开头那个字的，用 add 在原来的时刻重新加一次）")
    seen = {int(label) for label in labels}
    rendered = render_missing(sorted(set(LABELS) - seen), digits[0].shape)
    if rendered:                    # 渲染字要和渲染的二三四一起比，才知道“最像五”是不是真的
        for count, glyph in render_missing(sorted(LABELS), digits[0].shape).items():
            digits.append(glyph)
            labels.append(str(count))
            rendered_flags.append(True)
    np.savez_compressed(TARGET, lianpo=np.stack(lianpo), digits=np.stack(digits),
                        digit_labels=np.array(json.dumps(labels)), digit_rendered=np.array(rendered_flags),
                        teams=np.stack(teams), team_labels=np.array(json.dumps(team_labels)))
    real = sorted({label for label, flag in zip(labels, rendered_flags) if not flag})
    print(f"{len(files)} 个样本 → {TARGET.relative_to(ROOT)}；有真实样本的：{'、'.join(LABELS[int(x)] for x in real)}"
          + (f"；渲染补上的：{'、'.join(LABELS[c] for c in sorted(set(LABELS) - seen))}" if rendered else "")
          + f"；开头的字：蓝 {team_labels.count('blue')} 个、红 {team_labels.count('red')} 个")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    adder = commands.add_parser("add")
    adder.add_argument("recording", type=Path)
    adder.add_argument("--at", type=float, required=True, help="录像里的秒数")
    adder.add_argument("--count", type=int, required=True, choices=sorted(LABELS), help="几连破")
    adder.add_argument("--side", choices=sorted(announce.INKS),
                       help="字上写的是哪一方（不给就按字的颜色猜；皮肤自带的播报颜色可能和阵营对不上）")
    adder.set_defaults(run=add)
    commands.add_parser("build").set_defaults(run=build)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
