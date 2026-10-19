#!/bin/zsh
# 一键开播：状态中心 + 画面识别 + 手机投屏窗口。手机先用 USB 连好；先运行这个，再打开 OBS
# （OBS 的浏览器源在打开的那一刻连不上面板地址，就会一直空着，不会自己重试）。
#     ./start.sh          单设备：OBS 采集手机投屏窗口
#     ./start.sh dual     双设备：画面来自 MuMu 模拟器，不开手机投屏窗口（它每秒要解码 60 帧大图，白占资源）
# 都在这一个终端里跑，按 Ctrl+C 一起停。识别服务会把画面录到 data/recordings/live-日期-时间.h264。
cd "$(dirname "$0")"
ENV=/opt/anaconda3/envs/wrob
export PATH="$ENV/bin:$PATH" PYTHONUNBUFFERED=1

pids=()
cleanup() { for pid in $pids; do kill $pid 2>/dev/null; done; wait 2>/dev/null; echo "[开播] 都停了"; }
trap cleanup INT TERM EXIT

if lsof -nP -iTCP:8780 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[开播] 状态中心已经在跑了，直接用它"
else
    python -m server & pids+=$!
    sleep 2
fi
# 投屏窗口：无边框（OBS 窗口采集时就不会带上标题栏），窗口名 LOLM
if [[ "$1" != "dual" ]]; then
    scrcpy --no-audio --video-bit-rate=16M --max-fps=60 --stay-awake --window-borderless --window-title=LOLM >/dev/null 2>&1 & pids+=$!
fi
python -m vision --quiet --record "data/recordings/live-$(date +%m%d-%H%M%S).h264" 2>&1 | grep -v "objc\[" & pids+=$!
echo "[开播] 导播台 http://127.0.0.1:8780/director/    按 Ctrl+C 停止"
wait
