"""BP 识别（vision/bp.py、vision/champions.py）和状态中心对识别 BP 命令的处理。

夹具 tests/fixtures/bp.npz：用户 10-20 在数据机上录的两段选人界面（bprecs/，1280×576）里的 7 帧，只存了标题、
顶部两角的禁用圆圈、两侧选手头像那几块。picking.mp4 是 3 Ban 全流程，allchamp.mp4 是赛事征召（第一轮禁用时
滚了一遍全英雄）。期望值对着画面上的名字核对过。
"""
import json
from pathlib import Path

import numpy as np
import pytest

from state import CommandError
from vision.bp import EMPTY, BPReader, BPTracker

from conftest import BLUE_PICKS, RED_PICKS

FIXTURES = Path(__file__).parent / "fixtures"
E = EMPTY


def frame(key: str) -> np.ndarray:
    canvas = np.zeros((576, 1280, 3), np.uint8)
    with np.load(FIXTURES / "bp.npz") as data:
        for name, (y0, y1, x0, x1) in json.loads(str(data["regions"])).items():
            canvas[y0:y1, x0:x1] = data[f"{key}.{name}"]
    return canvas


@pytest.fixture(scope="module")
def reader():
    return BPReader()


CASES = {
    # 3 Ban：禁用阶段里双方的圆圈陆续填上；两边都禁了艾瑞莉娅（暗禁可以重复）
    "ban": ("禁用阶段", ["gwen", E, "irelia"], [E, "irelia", "nasus"], [None] * 5, [None] * 5),
    "reveal": ("禁用显示", ["gwen", "ahri", "irelia"], ["akali", "irelia", "nasus"], [None] * 5, [None] * 5),
    # 蓝方 1 号正在挑：亮出的是黑默丁格（最后锁的是彗）
    "hover": ("蓝方选择英雄", ["gwen", "ahri", "irelia"], ["akali", "irelia", "nasus"], ["heimerdinger"] + [None] * 4, [None] * 5),
    "redturn": ("红方选择英雄", ["gwen", "ahri", "irelia"], ["akali", "irelia", "nasus"], ["hwei"] + [None] * 4,
                ["morgana", "garen"] + [None] * 3),
    "ready": ("准备阶段", ["gwen", "ahri", "irelia"], ["akali", "irelia", "nasus"],
              ["hwei", "gragas", "samira", "senna", "shyvana"], ["morgana", "garen", "ashe", "alistar", "khazix"]),
    # 赛事征召：每边 5 个禁用圆圈；蓝方第一手没禁（空 Ban）
    "t-empty": ("蓝方禁用英雄", [E] * 5, [E] * 5, [None] * 5, [None] * 5),
    "t-round1": ("蓝方禁用英雄", [E, "riven", E, E, E], ["singed", "olaf", E, E, E], [None] * 5, [None] * 5),
}


@pytest.mark.parametrize("key", list(CASES))
def test_reads_bp_screens(reader, key):
    title, blue_bans, red_bans, blue_picks, red_picks = CASES[key]
    reader._cache.clear()
    reading = reader.read(frame(key))
    assert reading["title"] == title
    assert reading["bans"]["blue"][:len(blue_bans)] == blue_bans
    assert reading["bans"]["red"][:len(red_bans)] == red_bans
    assert reading["picks"] == {"blue": blue_picks, "red": red_picks}


@pytest.mark.parametrize("key, count", [("ban", 19), ("reveal", 3), ("hover", 15), ("redturn", 22), ("ready", 20),
                                        ("t-empty", 18), ("t-round1", 25)])
def test_reads_the_countdown(reader, key, count):
    """标题下面的读秒（游戏里一手 30 秒显示 29→0；3 Ban 选人一轮 25 秒显示 24→0；禁用显示 4→0）。"""
    assert reader.read(frame(key))["countdown"] == count


def test_other_screens_are_not_bp(reader):
    assert reader.read(np.zeros((1264, 2800, 3), np.uint8)) is None


# ---- 追踪：读数 → 命令 ----

def reading(title, bans=None, picks=None, countdown=None):
    from vision.bp import PHASES, SIDE_OF
    bans = bans or {"blue": [E] * 5, "red": [E] * 5}
    picks = picks or {"blue": [None] * 5, "red": [None] * 5}
    return {"title": title, "phase": PHASES[title], "side": SIDE_OF.get(title), "bans": bans, "picks": picks, "countdown": countdown}


def run(tracker, item, times=4, draft=None, start=0.0):
    out = []
    for i in range(times):
        out += tracker.update(item, start + i * 0.2, draft)
    return [(c["type"].split(".")[1], c.get("slot", {}).get("side"), c.get("slot", {}).get("type"), c.get("slot", {}).get("index"),
             c.get("champion")) for c in out]


def picks(blue, red):
    return {"blue": blue + [None] * (5 - len(blue)), "red": red + [None] * (5 - len(red))}


def test_hover_until_the_turn_passes(store):
    """轮到的一方亮出的是预选（换一个发一次）；轮到另一方时，最后亮着的才锁定。导播锁过的不再动。"""
    store.apply({"type": "draft.reset", "format": "blind3"}, 0)
    draft = store.snapshot(0)["draft"]
    tracker = BPTracker()
    bans = {"blue": ["gwen", "ahri", "irelia", None, None], "red": ["akali", "irelia", "nasus", None, None]}
    out = run(tracker, reading("禁用显示", bans), draft=draft)
    assert out[0][0] == "start" and ("lock", "red", "ban", 1, "irelia") in out
    assert run(tracker, reading("蓝方选择英雄", bans, picks(["lux"], [])), draft=draft) == [("hover", "blue", "pick", 0, "lux")]
    assert run(tracker, reading("蓝方选择英雄", bans, picks(["hwei"], [])), draft=draft) == [("hover", "blue", "pick", 0, "hwei")]
    out = run(tracker, reading("红方选择英雄", bans, picks(["hwei"], ["morgana"])), draft=draft)
    assert out == [("lock", "blue", "pick", 0, "hwei"), ("hover", "red", "pick", 0, "morgana")]
    # 导播已经把红方 2 号锁成了别的英雄：识别不去动它
    draft["slots"]["red"]["pick"][1].update(champion="garen", locked=True)
    out = run(tracker, reading("准备阶段", bans, picks(["hwei"], ["morgana", "ashe"])), draft=draft)
    assert out == [("lock", "red", "pick", 0, "morgana")]


def test_empty_bans_wait_for_the_round_to_end(store):
    """暗禁揭晓（禁用显示）时圆圈正从“⊘”翻成英雄，不算空 Ban；进了选人还是“⊘”的才是。
    赛事征召：第一轮禁用结束只判前 3 个，第二轮的 2 个还没开始。"""
    store.apply({"type": "draft.reset", "format": "tournament"}, 0)
    draft = store.snapshot(0)["draft"]
    tracker = BPTracker()
    bans = {"blue": [E, "riven", "jax", E, E], "red": ["singed", "olaf", "lillia", E, E]}
    out = run(tracker, reading("红方禁用英雄", bans), draft=draft)
    assert ("skip", "blue", "ban", 0, None) not in out
    out = run(tracker, reading("选择你的英雄!", bans), times=8, draft=draft, start=2.0)
    assert [item for item in out if item[0] == "skip"] == [("skip", "blue", "ban", 0, None)]


def test_last_ban_flipping_at_the_switch_is_not_an_empty_ban():
    """最后一手禁用正好在切到选人的那一刻才翻出来：“⊘”要在选人阶段持续一会儿才判空。"""
    tracker = BPTracker()
    before = {"blue": ["zed", "ahri", "jinx", None, None], "red": ["vi", "lux", E, None, None]}
    after = {"blue": ["zed", "ahri", "jinx", None, None], "red": ["vi", "lux", "lillia", None, None]}
    run(tracker, reading("禁用阶段", before))
    out = run(tracker, reading("蓝方选择英雄", before), times=2, start=1.0) + run(tracker, reading("蓝方选择英雄", after), start=1.4)
    assert ("lock", "red", "ban", 2, "lillia") in out and not [item for item in out if item[0] == "skip"]


# ---- 状态中心 ----

def test_store_takes_vision_bp_but_never_overrides(store):
    store.apply({"type": "draft.reset", "format": "blind3"}, 0)
    store.apply({"type": "draft.start", "source": "vision"}, 0)
    assert store.draft["status"] == "running"
    ref = {"side": "blue", "type": "ban", "index": 0}
    store.apply({"type": "draft.lock", "slot": ref, "champion": "zed"}, 0)                     # 导播先填了
    store.apply({"type": "draft.lock", "source": "vision", "slot": ref, "champion": "ahri"}, 0)
    store.apply({"type": "draft.skip", "source": "vision", "slot": ref}, 0)
    assert store.draft["slots"]["blue"]["ban"][0]["champion"] == "zed"
    store.apply({"type": "draft.lock", "source": "vision", "slot": {"side": "blue", "type": "ban", "index": 4}, "champion": "vi"}, 0)
    pick = {"side": "red", "type": "pick", "index": 0}
    store.apply({"type": "draft.hover", "source": "vision", "slot": pick, "champion": "lux"}, 0)
    assert store.draft["slots"]["red"]["pick"][0] == {"champion": "lux", "locked": False, "role": None}
    store.apply({"type": "ui.set", "values": {"autoBp": False}}, 0)                           # 导播关掉自动 BP
    store.apply({"type": "draft.lock", "source": "vision", "slot": pick, "champion": "lux"}, 0)
    assert store.draft["slots"]["red"]["pick"][0]["locked"] is False


def test_vision_bp_works_while_the_last_game_is_still_on_screen(store):
    """上一局结束、赛后画面还挂着时，下一局的选人界面就出现了：识别的 BP 照收（局内数据仍然不收）。"""
    store.apply({"type": "draft.reset", "format": "blind3"}, 0)
    store.apply({"type": "draft.start"}, 0)
    for side in ("blue", "red"):
        for index in range(3):
            store.apply({"type": "draft.skip", "slot": {"side": side, "type": "ban", "index": index}}, 0)
    for side, champions in (("blue", BLUE_PICKS), ("red", RED_PICKS)):
        for index, champion in enumerate(champions):
            store.apply({"type": "draft.lock", "champion": champion, "slot": {"side": side, "type": "pick", "index": index}}, 0)
    store.apply({"type": "draft.confirm"}, 0)
    store.apply({"type": "game.new"}, 0)
    store.apply({"type": "game.finish", "winner": "a"}, 0)
    assert store.draft["status"] == "idle" and store.game["status"] == "ended"
    store.apply({"type": "draft.lock", "source": "vision", "slot": {"side": "red", "type": "ban", "index": 0}, "champion": "zed"}, 0)
    assert store.draft["status"] == "running" and store.draft["slots"]["red"]["ban"][0]["champion"] == "zed"
    with pytest.raises(CommandError):                                       # 识别也不能填本局已经禁掉的
        store.apply({"type": "draft.lock", "source": "vision", "slot": {"side": "red", "type": "pick", "index": 0},
                     "champion": "zed"}, 0)


def test_every_bp_command_can_be_logged():
    """识别服务把每条命令打印出来（vision/__main__.py 的 describe）：BP 的每一种命令都要能打印，打印出错会让识别服务退出。"""
    from vision.__main__ import describe
    ref = {"side": "red", "type": "pick", "index": 1}
    for command in ({"type": "draft.start"}, {"type": "draft.timer", "action": "set", "seconds": 24.7},
                    {"type": "draft.hover", "slot": ref, "champion": "lux"}, {"type": "draft.lock", "slot": ref, "champion": "lux"},
                    {"type": "draft.skip", "slot": {"side": "blue", "type": "ban", "index": 0}}):
        assert describe(command).startswith("BP：")



def test_countdown_drives_the_timer():
    """读秒每跳一下就对一次表（跳到 v 时还剩约 v + 1 秒）；准备阶段不对（那时在调分路，不读秒）。"""
    tracker = BPTracker()
    timers = lambda item, start: [c["seconds"] for c in tracker.update(item, start) + tracker.update(item, start + 0.2)
                                  if c["type"] == "draft.timer"]
    tracker.update(reading("禁用阶段", countdown=29), -0.2)               # 标题先要连续确认两次
    assert timers(reading("禁用阶段", countdown=29), 0.0) == [29.7]
    assert timers(reading("禁用阶段", countdown=29), 0.4) == []          # 没变不重复发
    assert timers(reading("禁用阶段", countdown=28), 1.0) == [28.7]
    assert timers(reading("准备阶段", countdown=24), 2.0) == []


def test_countdown_restart_locks_the_previous_pick(store):
    """赛事征召同一方连选两手：标题不变，读秒从 29 重新数，这时导播台上还轮着的那一格（上一手）亮着的英雄就是选定的。"""
    store.apply({"type": "draft.reset", "format": "tournament"}, 0)
    store.apply({"type": "draft.start"}, 0)
    for side, index in [("blue", 0), ("red", 0), ("blue", 1), ("red", 1), ("blue", 2), ("red", 2)]:
        store.apply({"type": "draft.skip", "slot": {"side": side, "type": "ban", "index": index}}, 0)
    store.apply({"type": "draft.lock", "champion": "jinx", "slot": {"side": "blue", "type": "pick", "index": 0}}, 0)
    draft = store.snapshot(0)["draft"]
    assert draft["active"] == [{"side": "red", "type": "pick", "index": 0}]
    tracker = BPTracker()
    rows = picks(["jinx"], ["lux"])
    out = run(tracker, reading("选择你的英雄!", picks=rows, countdown=12), draft=draft)
    assert ("hover", "red", "pick", 0, "lux") in out and not [item for item in out if item[0] == "lock" and item[1] == "red"]
    out = run(tracker, reading("选择你的英雄!", picks=rows, countdown=29), draft=draft, start=2.0)
    assert ("lock", "red", "pick", 0, "lux") in out


def test_reads_the_real_phone_screen(reader):
    """10-20 晚直播：数据机实际的画面（2800×1264，adb 截图）比 1280 的录屏清晰得多，标题只对上 0.82，
    当时的门槛 0.85 没过，整个选人界面都没认出来。补了实机的标题模板、门槛降到 0.7。"""
    canvas = np.zeros((1264, 2800, 3), np.uint8)
    with np.load(FIXTURES / "bp-live.npz") as data:
        for name, (y0, y1, x0, x1) in json.loads(str(data["regions"])).items():
            canvas[y0:y1, x0:x1] = data[f"live.{name}"]
    reader._cache.clear()
    reading = reader.read(canvas)
    assert reading["title"] == "蓝方选择英雄" and reading["countdown"] == 28
    assert reading["bans"] == {"blue": ["singed", "yone", "yuumi", E, E], "red": ["katarina", "xin-zhao", "jax", E, E]}
    assert reading["picks"] == {"blue": ["irelia", None, None, None, None], "red": ["galio", "teemo", None, None, None]}
