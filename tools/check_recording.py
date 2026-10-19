#!/usr/bin/env python3
"""拿一段录像检验识别器：每一项读出来的比例，以及读数之间能不能互相印证。

    python tools/check_recording.py data/recordings/replay.h264

没有人工标注，靠画面自己的冗余来对账：
  · 同一方五个人的经济之和，四舍五入到 0.1K 应该等于顶部条的团队经济
  · 同一方五个人的击杀之和，应该等于顶部条的人头数
  · 时钟应该一秒一秒往前走

改了 vision/ 里的算法、模板或 config/vision.yaml 之后跑一遍，数字不应该变差。
"""
import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state import SIDES  # noqa: E402
from vision.reader import Reader  # noqa: E402
from vision.sources import H264File  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--step", type=int, default=2, help="每隔几帧读一帧（录像是 10 帧/秒）")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--stop", type=float)
    parser.add_argument("--show", type=int, default=8, help="每类问题最多列出几个例子")
    args = parser.parse_args()

    reader = Reader()
    frames = with_panel = 0
    read = Counter()            # 每一项读出来的帧数
    expected = Counter()        # 每一项应该能读出来的帧数
    checks = Counter()
    examples = {"gold": [], "kills": [], "clock": []}
    last_clock = None
    spent = 0.0

    for frame, stamp in H264File(args.recording, step=args.step, start=args.start, stop=args.stop):
        began = time.perf_counter()
        result = reader.read(frame)
        spent += time.perf_counter() - began
        frames += 1
        values = result["values"]

        expected["clock"] += 1
        if result["clock"] is not None:
            read["clock"] += 1
            if last_clock is not None:
                step = result["clock"] - last_clock[0]
                allowed = stamp - last_clock[1] + 1.5           # 画面过了多久，时钟最多走多少
                checks["clock_total"] += 1
                if 0 <= step <= allowed:
                    checks["clock_ok"] += 1
                elif len(examples["clock"]) < args.show:
                    examples["clock"].append(f"{stamp:6.1f}s  {last_clock[0]} → {result['clock']}")
            last_clock = (result["clock"], stamp)

        for side in SIDES:
            for field in ("towers", "gold", "kills"):
                expected[f"top.{field}"] += 1
                read[f"top.{field}"] += f"{side}.{field}" in values
        if not result["panel"]:
            continue
        with_panel += 1
        for side in SIDES:
            golds = [values.get(f"{side}.{index}.gold") for index in range(5)]
            kdas = [values.get(f"{side}.{index}.kda") for index in range(5)]
            expected["panel.gold"] += 5
            expected["panel.kda"] += 5
            read["panel.gold"] += sum(gold is not None for gold in golds)
            read["panel.kda"] += sum(kda is not None for kda in kdas)
            team_gold, team_kills = values.get(f"{side}.gold"), values.get(f"{side}.kills")
            if team_gold is not None and all(gold is not None for gold in golds):
                checks["gold_total"] += 1
                # 顶部条四舍五入到 0.1K，而且和面板不一定在同一帧刷新，放宽到差 100 以内
                if abs(sum(golds) - team_gold) <= 100:
                    checks["gold_ok"] += 1
                elif len(examples["gold"]) < args.show:
                    examples["gold"].append(f"{stamp:6.1f}s {side} 面板 {golds} 合计 {sum(golds)}，顶部 {team_gold}")
            if team_kills is not None and all(kda is not None for kda in kdas):
                checks["kills_total"] += 1
                if sum(kda[0] for kda in kdas) == team_kills:
                    checks["kills_ok"] += 1
                elif len(examples["kills"]) < args.show:
                    examples["kills"].append(f"{stamp:6.1f}s {side} 面板 {[kda[0] for kda in kdas]}，顶部 {team_kills}")

    def rate(done, total):
        return f"{done}/{total} = {100 * done / total:.2f}%" if total else "没有样本"

    print(f"{frames} 帧，其中底部面板展开 {with_panel} 帧；平均每帧识别 {1000 * spent / max(frames, 1):.1f} ms")
    print("读出率")
    for key in ("clock", "top.towers", "top.gold", "top.kills", "panel.gold", "panel.kda"):
        print(f"  {key:12s} {rate(read[key], expected[key])}")
    print("互相印证")
    print(f"  时钟逐秒递增          {rate(checks['clock_ok'], checks['clock_total'])}")
    print(f"  五人经济之和 = 团队经济  {rate(checks['gold_ok'], checks['gold_total'])}")
    print(f"  五人击杀之和 = 人头数    {rate(checks['kills_ok'], checks['kills_total'])}")
    for key, title in (("clock", "时钟跳变"), ("gold", "经济对不上"), ("kills", "击杀对不上")):
        if examples[key]:
            print(f"{title}的例子：")
            for line in examples[key]:
                print("  " + line)


if __name__ == "__main__":
    main()
