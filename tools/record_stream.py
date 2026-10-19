#!/usr/bin/env python3
"""把手机画面录成原始 H.264，给识别的开发和回归测试用。不影响正在投屏的 scrcpy 窗口。

    python tools/record_stream.py data/recordings/replay.h264 --seconds 600

文件大约每分钟 30–40 MB。之后可以用 vision.sources.H264File 逐帧读回来。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vision.sources import ScrcpyStream  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=600, help="录多久")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--serial", help="连着不止一台手机时，要录的那一台的序列号")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with ScrcpyStream(serial=args.serial, max_fps=args.fps, record_to=args.output) as stream:
        for frame, _ in stream:
            elapsed = time.monotonic() - started
            if elapsed >= args.seconds:
                break
            if frame is None:
                print(f"[{elapsed:5.0f}s] 画面没有变化", flush=True)
            elif stream.frames % (args.fps * 30) == 0:
                print(f"[{elapsed:5.0f}s] 已录 {stream.frames} 帧，{args.output.stat().st_size / 1e6:.0f} MB", flush=True)
    print(f"录完：{stream.frames} 帧，{args.output.stat().st_size / 1e6:.0f} MB → {args.output}")


if __name__ == "__main__":
    main()
