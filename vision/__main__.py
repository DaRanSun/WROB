"""识别服务：画面 → 读数 → 去抖校验 → 发给状态中心。

    python -m vision                                    实时：从 USB 连着的手机取画面
    python -m vision --file data/recordings/x.h264      用录像代替手机（联调、演示、排查）
    python -m vision --dry-run                          只在终端打印，不发给状态中心

要先启动 python -m server。导播台“局内”页里被锁住的字段，这里改不动。

画面上没有局内的时钟时（选人界面、结算界面），顺便看是不是选人界面：是就读 BP（vision/bp.py），自动填导播台的 BP
（导播台“BP”页可以关掉）。
导播点了“结束本局”之后，局内的数据一概不再读；改为等数据机打开结算页“统计 · 数据对比”，读到双方十个人的数据
就报给状态中心（赛后面板据此弹出）。导播开始下一局（进入局内）后自动回到读局内数据。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from state.catalog import load_config

from .layout import LayoutError, load_layout
from .reader import Reader
from .bp import BPReader, BPTracker
from .sources import H264File, ScrcpyStream, SourceError
from .stats import StatsReader, StatsWatcher
from .tracker import HEARTBEAT_SECONDS, Tracker

PHASE_SECONDS = 1.0         # 每隔这么久问一次状态中心：这一局是不是已经被导播结束了


class Sender:
    """把命令 POST 给状态中心。服务没开或暂时连不上时不报错退出，只提示一次，之后继续试。"""

    def __init__(self, base: str | None):
        self.base = base
        self.down = False

    def snapshot(self) -> dict | None:
        """状态中心当前的快照；连不上返回 None。"""
        if self.base is None:
            return None
        try:
            with urllib.request.urlopen(self.base + "/api/state", timeout=1) as response:
                return json.load(response)
        except (OSError, ValueError):
            return None

    def send(self, command: dict) -> None:
        if self.base is None:
            return
        request = urllib.request.Request(
            self.base + "/api/command", data=json.dumps(command).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(request, timeout=1).close()
            if self.down:
                print("[识别] 状态中心连上了")
            self.down = False
        except urllib.error.HTTPError as error:         # 命令被拒绝：打印原因，继续
            print(f"[识别] 命令被拒绝：{json.load(error).get('error')}  {command}")
        except OSError:
            if not self.down:
                print(f"[识别] 连不上状态中心 {self.base}（先运行 python -m server）；会一直重试")
            self.down = True


def describe(command: dict) -> str:
    if command["type"] == "game.clock":
        minutes, seconds = divmod(int(command["seconds"]), 60)
        return f"时钟对表 {minutes:02d}:{seconds:02d}" + ("" if command["running"] else "（暂停）")
    if command["type"] == "game.set":
        def show(path, value):
            if path.endswith(".items"):
                return f"{path}=[{' '.join(item or '·' for item in value)}]"
            return f"{path}={value}"
        # 血条一秒报好几次，不打印
        return "  ".join(show(path, value) for path, value in command["values"].items() if not path.endswith(".bars"))
    if command["type"] == "game.objective":
        minutes, seconds = divmod(int(command["t"]), 60)
        names = {"dragon": "元素亚龙", "herald": "峡谷先锋", "baron": "纳什男爵", "elder": "远古巨龙"}
        who = {"blue": "蓝方", "red": "红方"}.get(command.get("side"), "哪一方待定")
        element = f"（{command['element']}）" if command.get("element") else ""
        return f"{minutes:02d}:{seconds:02d} {names[command['what']]}{element}被击杀，{who}"
    if command["type"] == "vision.status":
        return "底部面板" + ("展开" if command["panel"] else "收起") + ("" if command["clock"] else "，时钟读不出")
    if command["type"].startswith("draft."):
        if command["type"] == "draft.start":
            return "BP：数据机进了选人界面，开始"
        if command["type"] == "draft.timer":
            return f"BP：读秒对到 {command['seconds']:.1f} 秒"
        ref = command["slot"]
        what = {"draft.hover": "亮出", "draft.lock": "锁定", "draft.skip": "空 Ban"}[command["type"]]
        return (f"BP：{'蓝方' if ref['side'] == 'blue' else '红方'}{'禁用' if ref['type'] == 'ban' else '选用'} {ref['index'] + 1} "
                f"{what} {command.get('champion') or ''}").rstrip()
    if command["type"] == "postgame.stats":
        def half(rows):
            return " / ".join(f"{row['damage']}（{row['damageShare']}%）" for row in rows)
        return f"结算数据：左边输出 {half(command['left'])}；右边输出 {half(command['right'])}"
    return str(command)


class Phase:
    """状态中心那边这一局走到哪了。导播结束本局之后：不读局内，等结算页的数据（还没收到的话）。"""

    def __init__(self, sender: Sender):
        self.sender = sender
        self.checked_at = float("-inf")
        self.ended = False
        self.wants_stats = False
        self.draft = None                   # 状态中心的 BP（识别填 BP 时要知道流程和哪些槽位已经锁了）

    def check(self, now: float) -> str | None:
        """到时间了就问一次。返回 "ended" / "started"（刚结束 / 新开了一局），没变化返回 None。"""
        if self.sender.base is None or now - self.checked_at < PHASE_SECONDS:
            return None
        self.checked_at = now
        snapshot = self.sender.snapshot()
        if not snapshot:
            return None
        was_ended = self.ended
        self.draft = snapshot.get("draft")
        postgame = snapshot.get("postgame")
        self.ended = snapshot["game"]["status"] == "ended"
        self.wants_stats = self.ended and bool(postgame) and postgame.get("stats") is None
        if self.ended != was_ended:
            return "ended" if self.ended else "started"
        return None


def main() -> None:
    app = load_config("app")
    layout = load_layout()
    parser = argparse.ArgumentParser(prog="python -m vision", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", type=Path, help="用录像（tools/record_stream.py 录的）代替手机")
    parser.add_argument("--serial", default=layout.get("stream", {}).get("serial"),
                        help="数据机的序列号（adb devices 里的第一列）；电脑上只连着一台手机时不用给")
    parser.add_argument("--start", type=float, default=0.0, help="录像从第几秒开始")
    parser.add_argument("--speed", type=float, default=1.0, help="录像的播放速度；0 = 不等待，尽快跑完")
    parser.add_argument("--server", default=f"http://127.0.0.1:{app.get('port', 8780)}")
    parser.add_argument("--hz", type=float, default=layout.get("sample_hz", 5), help="每秒读几次")
    parser.add_argument("--dry-run", action="store_true", help="不发给状态中心")
    parser.add_argument("--record", type=Path, metavar="文件",
                        help="实时模式下把画面同时录下来（约每分钟 35 MB），识别出了问题可以拿录像复现")
    parser.add_argument("--quiet", action="store_true", help="不逐条打印提交的数值")
    args = parser.parse_args()

    stream = layout.get("stream", {})
    if args.file:
        source = H264File(args.file, fps=stream.get("fps", 10), start=args.start)
    else:
        if args.record:
            args.record.parent.mkdir(parents=True, exist_ok=True)
        width, height = layout["reference"]
        source = ScrcpyStream(serial=args.serial or None, max_fps=stream.get("fps", 10),
                              bit_rate=stream.get("bit_rate", 16_000_000), record_to=args.record, aspect=width / height)
    reader, tracker, sender = Reader(), Tracker(), Sender(None if args.dry_run else args.server)
    stats_reader, stats_watcher, phase = StatsReader(), StatsWatcher(), Phase(sender)
    bp_reader, bp_tracker = BPReader(), BPTracker()
    heartbeat_at = float("-inf")

    interval = 1.0 / args.hz
    began = time.monotonic()
    next_at = float("-inf")
    samples = 0
    waiting = False
    print(f"[识别] 开始，每秒读 {args.hz:g} 次" + ("（不发送）" if args.dry_run else f"，发往 {args.server}"))
    try:
        for frame, stamp in source:
            if args.file and args.speed > 0:            # 录像按原速放，面板上看起来就和实时一样
                wait = began + (stamp - args.start) / args.speed - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            now = time.monotonic() if not args.file else began + (stamp - args.start) / (args.speed or 1.0)
            if frame is None:
                print("[识别] 画面没有变化（比赛暂停，或手机息屏）")
                continue
            if stamp < next_at:
                continue
            next_at = stamp + interval - 0.02
            samples += 1
            change = phase.check(now)
            if change == "ended":
                print("[识别] 导播结束了本局：停止读局内数据" + ("；等数据机打开结算页“统计 · 数据对比”" if phase.wants_stats else ""))
            elif change == "started":
                tracker, stats_watcher = Tracker(), StatsWatcher()      # 新的一局：上一局的去抖状态全部丢掉
                print("[识别] 新的一局开始了，重新读局内数据")
            draft = phase.draft
            if bp_tracker.sent and draft and draft["status"] == "idle" and not any(
                    slot["champion"] for side in draft["slots"].values() for slots in side.values() for slot in slots):
                bp_tracker = BPTracker()        # 导播台换了一局新的 BP（或者重置了）：从头认
            bp = None
            if phase.ended:
                commands, screen = [], None
                try:
                    bp = bp_reader.read(frame)
                except LayoutError:
                    bp = None
                if bp:
                    screen = "bp"
                elif phase.wants_stats:
                    try:
                        stats = stats_reader.read(frame)
                    except LayoutError:
                        stats = None
                    screen = "stats" if stats else None
                    commands = stats_watcher.update(stats, now)
                commands += bp_tracker.update(bp, now, draft)
                if now - heartbeat_at >= HEARTBEAT_SECONDS:
                    heartbeat_at = now
                    commands.append({"type": "vision.status", "panel": False, "clock": False, "screen": screen})
            else:
                try:
                    reading = reader.read(frame)
                    waiting = False
                except LayoutError:
                    # 手机不在游戏里（比如竖屏的桌面）：当作什么都没读到，继续等，心跳照发
                    if not waiting:
                        print(f"[识别] 现在的画面是 {frame.shape[1]}×{frame.shape[0]}，不是横屏的游戏画面，等待中")
                    waiting = True
                    reading = {"clock": None, "panel": False, "values": {}}
                commands = tracker.update(reading, now)
                if any(command["type"] == "vision.status" for command in commands):     # 每次报到时顺便对账
                    snapshot = sender.snapshot()
                    if snapshot:
                        commands += tracker.reconcile(snapshot, now)
                if reading["clock"] is None and not waiting:
                    bp = bp_reader.read(frame)          # 画面上没有局内时钟：是不是选人界面
                    commands += bp_tracker.update(bp, now, draft)
                    if bp:
                        for command in commands:
                            if command["type"] == "vision.status":
                                command["screen"] = "bp"
                    elif sender.base is None:
                        # 不连状态中心（--dry-run 看录像）时不知道导播有没有结束本局：顺便找找结算页
                        commands += stats_watcher.update(stats_reader.read(frame), now)
            for command in commands:
                sender.send(command)
                text = "" if args.quiet or command["type"] == "vision.status" else describe(command)
                if text:
                    print(f"[识别] {text}")
    except SourceError as error:
        sys.exit(f"[识别] {error}")
    except KeyboardInterrupt:
        pass
    finally:
        if isinstance(source, ScrcpyStream):
            source.close()
        print(f"[识别] 结束：读了 {samples} 次，校验挡掉 {tracker.rejected} 个读数")


if __name__ == "__main__":
    main()
