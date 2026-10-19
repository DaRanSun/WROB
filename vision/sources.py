"""画面来源。每个来源都是一个迭代器，产出 (帧, 时间戳秒)；帧是 BGR 的 numpy 数组，高 × 宽 × 3。

    ScrcpyStream   实时：另开一路 scrcpy-server 只取视频，不影响正在投屏的 scrcpy 窗口
    H264File       离线：ScrcpyStream 录下来的原始 H.264 文件，开发和回归测试用
"""
from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REMOTE_JAR = "/data/local/tmp/wrob-scrcpy-server.jar"      # 用自己的文件名，别和投屏那个 scrcpy 推上去的互相删
SCID = "57524f42"                                           # 会话号，区别于投屏那一路


class SourceError(RuntimeError):
    pass


def _av():
    try:
        import av
    except ImportError as error:
        raise SourceError("没有装 PyAV：在 wrob 环境里运行 pip install -r requirements.txt") from error
    return av


def _scrcpy_server() -> tuple[Path, str]:
    """环境里那份 scrcpy 的服务端文件和版本号（两者必须一致，否则手机端拒绝启动）。"""
    jar = Path(sys.prefix) / "opt" / "scrcpy" / "scrcpy-server"
    if not jar.is_file():
        raise SourceError(f"找不到 {jar}：先运行 tools/install_scrcpy.sh")
    version = subprocess.run([str(jar.with_name("scrcpy")), "--version"], capture_output=True, text=True).stdout.split()
    if len(version) < 2:
        raise SourceError("读不到 scrcpy 的版本号")
    return jar, version[1]


def connected_devices(adb: str | None = None) -> dict[str, str]:
    """已经授权的手机：{序列号: 型号}。"""
    adb = adb or shutil.which("adb") or str(Path(sys.prefix) / "bin" / "adb")
    result = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True)
    devices = {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            model = next((part.split(":", 1)[1] for part in parts[2:] if part.startswith("model:")), "")
            devices[parts[0]] = model.replace("_", " ")
    return devices


def display_size(adb: str, serial: str) -> tuple[int, int] | None:
    """一台设备屏幕的（长边, 短边）。读不到返回 None。"""
    result = subprocess.run([adb, "-s", serial, "shell", "wm", "size"], capture_output=True, text=True)
    sizes = re.findall(r"(\d+)x(\d+)", result.stdout)          # 有 Override size 时它在后面，以它为准
    if not sizes:
        return None
    width, height = (int(value) for value in sizes[-1])
    return max(width, height), min(width, height)


def pick_device(sizes: dict[str, tuple[int, int] | None], aspect: float) -> str | None:
    """连着好几台设备时，挑出屏幕比例和识别标定（config/vision.yaml 的 reference）一致的那一台当数据机。
    比如手机 2800×1264 和 Mac 上的 MuMu 模拟器 2560×1440 同时连着：只有手机对得上。正好一台对得上才返回它。"""
    matches = [serial for serial, size in sizes.items() if size and abs(size[0] / size[1] - aspect) <= 0.01]
    return matches[0] if len(matches) == 1 else None


class ScrcpyStream:
    """实时画面。

    在手机上再启动一个 scrcpy-server（原始 H.264、不带控制、不带音频），经 adb 转发到本机端口，
    后台线程收数据并解码，只保留最新一帧。迭代时每次等到有新帧才返回，所以画面静止（比如回放暂停）
    时会停在那里；连续 stall_seconds 没有新帧就产出 None，让调用方知道“画面没动”而不是一直卡住。
    """

    def __init__(self, serial: str | None = None, max_fps: int = 10, bit_rate: int = 16_000_000,
                 port: int = 27199, record_to: Path | None = None, stall_seconds: float = 2.0,
                 aspect: float | None = None):
        self.serial = serial
        self.aspect = aspect            # 识别标定的屏幕比例：连着好几台设备、又没指定序列号时，按它挑数据机
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.port = port
        self.record_to = record_to
        self.stall_seconds = stall_seconds
        self.frames = 0
        # 没激活环境、直接用环境里的解释器运行时，PATH 里没有 adb，到解释器旁边去找
        self._adb = [shutil.which("adb") or str(Path(sys.prefix) / "bin" / "adb")] + (["-s", serial] if serial else [])
        self._server = None
        self._socket = None
        self._thread = None
        self._latest = None             # (av.VideoFrame, 时间戳)
        self._taken = 0                 # 调用方已经拿走到第几帧
        self._condition = threading.Condition()
        self._error: Exception | None = None
        self._closed = False

    def _run_adb(self, *args: str) -> str:
        result = subprocess.run(self._adb + list(args), capture_output=True, text=True)
        if result.returncode:
            raise SourceError(f"adb {' '.join(args)} 失败：{(result.stderr or result.stdout).strip()}")
        return result.stdout

    def start(self) -> "ScrcpyStream":
        jar, version = _scrcpy_server()
        devices = connected_devices(self._adb[0])
        if not devices:
            raise SourceError("adb 没有看到已授权的手机：检查 USB 线和“USB 调试”")
        listing = "\n".join(f"    {serial}  {model}" for serial, model in devices.items())
        if self.serial and self.serial not in devices:
            raise SourceError(f"没有找到序列号是 {self.serial} 的手机。现在连着的：\n{listing}")
        if not self.serial and len(devices) > 1:
            sizes = {serial: display_size(self._adb[0], serial) for serial in devices}
            chosen = pick_device(sizes, self.aspect) if self.aspect else None
            if chosen is None:
                listing = "\n".join(f"    {serial}  {model}  {'×'.join(map(str, sizes[serial] or ())) or '屏幕尺寸读不到'}"
                                     for serial, model in devices.items())
                raise SourceError("电脑上连着不止一台设备，按屏幕比例也分不出哪一台是数据机：加上 --serial 序列号，"
                                  f"或者写进 config/vision.yaml 的 stream.serial。现在连着的：\n{listing}")
            self.serial = chosen
            self._adb = self._adb[:1] + ["-s", chosen]
            print(f"[识别] 连着 {len(devices)} 台设备，按屏幕比例选了 {chosen}（{devices[chosen]}）当数据机")
        self._run_adb("push", str(jar), REMOTE_JAR)
        self._run_adb("forward", f"tcp:{self.port}", f"localabstract:scrcpy_{SCID}")
        self._server = subprocess.Popen(
            self._adb + ["shell", f"CLASSPATH={REMOTE_JAR}", "app_process", "/", "com.genymobile.scrcpy.Server", version,
                         f"scid={SCID}", "log_level=warn", "tunnel_forward=true", "audio=false", "control=false",
                         "cleanup=false", "raw_stream=true", f"max_fps={self.max_fps}", f"video_bit_rate={self.bit_rate}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 服务端起来之前，转发端口上的连接会被立刻关掉；重试到真的读到数据为止。
        deadline = time.monotonic() + 8
        first = b""
        while time.monotonic() < deadline and not first:
            try:
                self._socket = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                self._socket.settimeout(3)
                first = self._socket.recv(1 << 16)
            except OSError:
                first = b""
            if not first:
                if self._socket:
                    self._socket.close()
                time.sleep(0.1)
        if not first:
            self.close()
            raise SourceError("连不上手机上的 scrcpy-server（8 秒内没有收到视频数据）")
        self._thread = threading.Thread(target=self._pump, args=(first,), daemon=True)
        self._thread.start()
        return self

    def _pump(self, data: bytes) -> None:
        codec = _av().CodecContext.create("h264", "r")
        record = open(self.record_to, "wb") if self.record_to else None
        try:
            while not self._closed:
                if record:
                    record.write(data)
                for packet in codec.parse(data):
                    for frame in codec.decode(packet):
                        with self._condition:
                            self._latest = (frame, time.monotonic())
                            self.frames += 1
                            self._condition.notify_all()
                try:
                    data = self._socket.recv(1 << 16)
                except socket.timeout:
                    continue                        # 画面静止时编码器不出帧，属于正常情况
                if not data:
                    raise SourceError("视频流断开了（手机拔线、锁屏，或 scrcpy-server 退出）")
        except Exception as error:                  # 交给迭代的那一侧抛出
            if not self._closed:
                with self._condition:
                    self._error = error
                    self._condition.notify_all()
        finally:
            if record:
                record.close()

    def __iter__(self):
        if self._thread is None:
            self.start()
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self.frames > self._taken or self._error, timeout=self.stall_seconds)
                if self._error:
                    raise self._error
                if self.frames == self._taken:
                    frame = None
                else:
                    self._taken = self.frames
                    frame, stamp = self._latest
            yield (None, time.monotonic()) if frame is None else (frame.to_ndarray(format="bgr24"), stamp)

    def close(self) -> None:
        self._closed = True
        if self._socket:
            self._socket.close()
        if self._server:
            self._server.terminate()
        subprocess.run(self._adb + ["forward", "--remove", f"tcp:{self.port}"], capture_output=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


class H264File:
    """ScrcpyStream 录下来的原始 H.264。文件里没有时间戳，按录制时的帧率推算。

    start / stop 是秒，step 是每隔几帧取一帧（解码仍然要逐帧做，跳过的只是转成数组这一步）。
    """

    def __init__(self, path: Path, fps: float = 10.0, start: float = 0.0, stop: float | None = None, step: int = 1):
        self.path = Path(path)
        self.fps = fps
        self.start = start
        self.stop = stop
        self.step = max(1, step)

    def __iter__(self):
        with _av().open(str(self.path), format="h264") as container:
            for index, frame in enumerate(container.decode(video=0)):
                stamp = index / self.fps
                if self.stop is not None and stamp >= self.stop:
                    return
                if stamp >= self.start and index % self.step == 0:
                    yield frame.to_ndarray(format="bgr24"), stamp
