"""结算页“统计 · 数据对比”的识别。

夹具 tests/fixtures/stats.npz 是三帧真实画面里表头和 5 行数字那几条：两张截图（data/screenshots/结算/），
一帧实时录像（live-1016-002213.h264 第 730 秒，H.264 实时流的画质）。期望值是人工抄的，
抄的数每一方三组百分比都和数值对得上。
"""
from pathlib import Path

import numpy as np
import pytest

from vision.stats import HALVES, KEYS, StatsReader, StatsWatcher, consistent

from test_vision import frame_from

FIXTURES = Path(__file__).parent / "fixtures"


def shot(n: int) -> tuple[np.ndarray, dict]:
    canvas = np.zeros((1264, 2800, 3), np.uint8)
    with np.load(FIXTURES / "stats.npz") as data:
        canvas[176:218, 380:2600] = data[f"shot{n}.header"]
        for k in range(5):
            y = int(round(303 + 158.5 * k)) - 3
            canvas[y:y + 40, 380:2600] = data[f"shot{n}.row{k}"]
        table = data[f"shot{n}.expected"]
    expected = {half: [dict(zip(KEYS, map(int, row))) for row in table[h]] for h, half in enumerate(HALVES)}
    return canvas, expected


@pytest.fixture(scope="module")
def reader():
    return StatsReader()


@pytest.mark.parametrize("n", [0, 1, 2])
def test_reads_the_stats_page(reader, n):
    frame, expected = shot(n)
    reading = reader.read(frame)
    assert reading is not None and reading["complete"]
    for half in HALVES:
        assert reading[half] == expected[half]


def test_other_screens_are_not_the_stats_page(reader):
    assert reader.read(frame_from("hud-000")) is None
    assert reader.read(np.zeros((1264, 2800, 3), np.uint8)) is None


def test_a_misread_digit_breaks_the_percentages(reader):
    _, expected = shot(0)
    rows = [dict(row) for row in expected["left"]]
    assert consistent(rows)
    rows[0]["damage"] = 21409               # 27409 读成 21409：百分比就对不上了
    assert not consistent(rows)
    rows = [dict(row) for row in expected["left"]]
    rows[2]["gold"] = None                  # 有一格没读出来
    assert not consistent(rows)
    zeros = [dict.fromkeys(row, 0) for row in expected["left"]]
    assert not consistent(zeros)            # 页面刚打开，数字还没开始跳


def test_watcher_reports_three_identical_complete_reads(reader):
    frame, expected = shot(1)
    reading = reader.read(frame)
    watcher = StatsWatcher()
    assert watcher.update(reading, 0.0) == [] and watcher.update(reading, 0.2) == []
    commands = watcher.update(reading, 0.4)
    assert len(commands) == 1 and commands[0]["type"] == "postgame.stats"
    assert commands[0]["left"] == expected["left"] and commands[0]["right"] == expected["right"]
    assert watcher.update(reading, 0.6) == []           # 刚报过
    assert len(watcher.update(reading, 4.0)) == 1       # 隔一会儿再报一次（状态中心那边重复的会忽略）
    assert watcher.update(None, 8.0) == []              # 离开了这一页：要重新连续读到三次
    assert watcher.update(reading, 8.2) == [] and watcher.update(reading, 8.4) == []
    assert len(watcher.update(reading, 8.6)) == 1
