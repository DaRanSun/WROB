"""画面识别的测试。

读数部分用从真实录像里裁下来的三帧（tests/fixtures/hud-*.npz，只保留顶部条、底部面板、两侧头像卡片、
顶部条两端的元素龙图标、小地图右边的野怪刷新牌子这几块）。
数字的期望值没有靠人工抄：每一帧里五人经济之和、五人击杀之和都和顶部条对得上，时钟也和录像时间一致。
装备和复活倒计时的期望值是把每一格和认出来的图标并排对过图的。
"""
from pathlib import Path

import numpy as np
import pytest

from vision import layout as layout_mod
from vision import objectives
from vision.items import UNKNOWN, ItemMatcher
from vision.reader import Reader, hp_bar, resource_bar, ult_progress
from vision.text import DIGITS, TEMPLATES, GlyphSet
from vision.tracker import (BAR_EVERY, BAR_KIND_CONFIRM, CHIP_GONE_CONFIRM, DRAGON_CONFIRM, ITEM_GONE_CONFIRM, REVIVE_CONFIRM,
                            Slot, Tracker)

FIXTURES = Path(__file__).parent / "fixtures"
# 每一块在 2800×1264 画面里的左上角 (y, x)
REGIONS = {"top": (0, 1000), "panel": (990, 850), "cards_blue": (500, 190), "cards_red": (500, 2536),
           "dragons_blue": (10, 890), "dragons_red": (10, 1770), "chips": (90, 680),
           "spells_blue": (485, 146), "spells_red": (485, 2615)}          # 技能和符文那两列只有 hud-205 里有

EXPECTED = {
    "hud-000": {
        "clock": 7 * 60 + 30,
        "top": {"blue.towers": 1, "blue.gold": 29000, "blue.kills": 10, "red.kills": 6, "red.gold": 24900, "red.towers": 0},
        "blue": ([5564, 6634, 6026, 5995, 4796], [(1, 1, 0), (4, 1, 2), (0, 0, 0), (4, 2, 1), (1, 2, 5)]),
        "red": ([4749, 5296, 5220, 5144, 4514], [(0, 2, 1), (2, 3, 1), (2, 0, 0), (2, 3, 0), (0, 2, 3)]),
        "respawn": {"red.1": 1},                    # 蛮王还有 1 秒复活
        "dragons": {"blue": [], "red": []},
        "chips": {"upper": None, "lower": ("ocean", True)},             # 第一条龙（海龙）还活着，9 秒后被蓝方拿下
        "items": {
            "blue": [["铁板靴", None, None, None, "红水晶", "黄昏与黎明"],
                     ["爆发之靴", None, None, None, None, "德拉克萨的暮刃"],
                     ["法力之靴", None, None, None, None, "卢登的回声"],
                     ["狂战士胫甲", None, None, "长剑", "反曲之弓", "海妖杀手"],
                     ["明朗之靴", None, None, "狂徒铠甲", "女神之泪", "山脉壁垒"]],
            "red": [["速度之靴", None, None, None, "破败王者之刃", "绝境制裁"],
                    ["速度之靴", None, None, "短剑", "考尔菲德的战锤", "岚切"],
                    ["爆发之靴", None, None, None, "长剑", "星蚀"],
                    ["狂战士胫甲", None, None, None, "基舍艾斯碎片", "夺萃之镰"],
                    ["铁板靴", None, None, "爆裂魔杖", "班德尔玻璃镜", "黑雾巨镰"]],
        },
    },
    "hud-205": {
        "clock": 10 * 60 + 55,
        "top": {"blue.towers": 3, "blue.gold": 44100, "blue.kills": 20, "red.kills": 8, "red.gold": 36400, "red.towers": 1},
        "blue": ([9055, 10042, 8361, 9161, 7491], [(3, 1, 0), (6, 1, 5), (0, 1, 3), (7, 3, 3), (4, 2, 10)]),
        "red": ([6775, 8464, 7923, 7365, 5853], [(0, 4, 1), (2, 4, 1), (4, 1, 0), (2, 6, 2), (0, 5, 3)]),
        "respawn": {"red.3": 22},
        "dragons": {"blue": ["ocean"], "red": []},
        "chips": {"upper": ("baron", False), "lower": ("mountain", False)},      # 男爵 00:04、山龙 00:59 后刷新
        "items": {
            "blue": [["铁板靴", None, None, None, "破舰者", "黄昏与黎明"],
                     ["爆发之靴", None, None, "长剑", "收集者", "德拉克萨的暮刃"],
                     ["法力之靴", None, None, "增幅典籍", "视界专注", "卢登的回声"],
                     ["狂战士胫甲", None, None, "短剑", "鬼索的狂暴之刃", "海妖杀手"],
                     ["明朗之靴", None, None, "狂徒铠甲", "耳语头环", "山脉壁垒"]],
            "red": [["铁板靴", None, None, "紫雨林之拳", "破败王者之刃", "绝境制裁"],
                    ["狂战士胫甲", None, None, "长剑", "夺萃之镰", "岚切"],
                    ["碎甲者之靴", None, None, None, "巨蛇之牙", "星蚀"],
                    ["狂战士胫甲", None, None, None, "岚切", "夺萃之镰"],
                    ["铁板靴", None, "增幅典籍", "耀光", "帝国指令", "黑雾巨镰"]],
        },
    },
    "hud-306": {
        "clock": 12 * 60 + 37,
        "top": {"blue.towers": 4, "blue.gold": 51200, "blue.kills": 24, "red.kills": 9, "red.gold": 42800, "red.towers": 2},
        "blue": ([10454, 11861, 10009, 10503, 8410], [(3, 1, 1), (8, 1, 5), (1, 1, 5), (8, 4, 3), (4, 2, 12)]),
        "red": ([8706, 9606, 9509, 8189, 6844], [(0, 5, 1), (2, 5, 1), (5, 1, 0), (2, 7, 2), (0, 6, 3)]),
        "respawn": {"red.0": 26, "red.4": 26},      # 两个人同时阵亡
        "dragons": {"blue": ["ocean", "mountain"], "red": []},
        "chips": {"upper": ("baron", True), "lower": None},             # 男爵存活；山龙刚被拿下，牌子没了
        "items": {
            "blue": [["装甲战靴", None, None, "红水晶", "破舰者", "黄昏与黎明"],
                     ["爆发之靴", None, None, "岚切", "收集者", "德拉克萨的暮刃"],
                     ["法力之靴", None, None, "增幅典籍", "视界专注", "卢登的回声"],
                     ["狂战士胫甲", None, None, "卢安娜的飓风", "鬼索的狂暴之刃", "海妖杀手"],
                     ["明朗之靴", None, None, "狂徒铠甲", "耳语头环", "山脉壁垒"]],
            "red": [["铁板靴", None, None, "焚天", "破败王者之刃", "绝境制裁"],
                    ["炮铜胫甲", None, None, "绝境制裁", "夺萃之镰", "岚切"],
                    ["碎甲者之靴", None, "长剑", "长剑", "巨蛇之牙", "星蚀"],
                    ["狂战士胫甲", None, None, "布甲", "岚切", "夺萃之镰"],
                    ["铁板靴", "增幅典籍", "以太精魂", "耀光", "帝国指令", "黑雾巨镰"]],
        },
    },
}


def frame_from(name: str, *skip: str) -> np.ndarray:
    """把裁下来的几块贴回一张全黑的整帧；skip 里的块不贴。"""
    canvas = np.zeros((1264, 2800, 3), np.uint8)
    with np.load(FIXTURES / f"{name}.npz") as data:
        for key, (y, x) in REGIONS.items():
            if key in skip or key not in data.files:
                continue
            block = data[key]
            canvas[y:y + block.shape[0], x:x + block.shape[1]] = block
    return canvas


@pytest.fixture(scope="module")
def reader():
    return Reader()


@pytest.fixture(autouse=True)
def fresh_reader_cache(reader):
    reader._cache.clear()                   # 每个测试都当成第一帧来读，不沾上一个测试的缓存


# ---- 读一帧 ----

@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_reads_real_frames(reader, name):
    expected = EXPECTED[name]
    result = reader.read(frame_from(name))
    values = result["values"]
    assert result["clock"] == expected["clock"]
    assert result["panel"] is True
    assert {key: values.get(key) for key in expected["top"]} == expected["top"]
    for side in ("blue", "red"):
        golds, kdas = expected[side]
        assert [values.get(f"{side}.{i}.gold") for i in range(5)] == golds
        assert [values.get(f"{side}.{i}.kda") for i in range(5)] == kdas
        # 画面自己的冗余：逐人之和对得上顶部条（顶部条四舍五入到 0.1K，而且比面板晚刷新一拍）
        assert abs(sum(golds) - expected["top"][f"{side}.gold"]) <= 100
        assert sum(kda[0] for kda in kdas) == expected["top"][f"{side}.kills"]
    assert min(result["scores"].values()) > 0.7


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_reads_items(reader, name):
    values = reader.read(frame_from(name))["values"]
    for side in ("blue", "red"):
        assert [values[f"{side}.{i}.items"] for i in range(5)] == EXPECTED[name]["items"][side]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_reads_respawn(reader, name):
    """阵亡的头像变灰、上面压着红色的倒计时；活着的读成 0。"""
    values = reader.read(frame_from(name))["values"]
    dead = EXPECTED[name]["respawn"]
    for side in ("blue", "red"):
        for index in range(5):
            assert values[f"{side}.{index}.respawn"] == dead.get(f"{side}.{index}", 0), (side, index)


def test_grey_looking_champions_are_not_dead():
    """蛮王、时光的头像本来就偏灰，不能因此当成阵亡；真阵亡的（整张压成灰色）要认得出。"""
    layout = layout_mod.load_layout()
    cards = {name: layout_mod.card_fields(frame_from(name), layout) for name in EXPECTED}
    for name, card in (("hud-205", "red.1"), ("hud-306", "red.1"), ("hud-000", "blue.4"), ("hud-306", "blue.4")):
        assert not layout_mod.portrait_is_grey(cards[name][f"{card}.respawn"]), (name, card)
    for name, card in (("hud-000", "red.1"), ("hud-205", "red.3"), ("hud-306", "red.0"), ("hud-306", "red.4")):
        assert layout_mod.portrait_is_grey(cards[name][f"{card}.respawn"]), (name, card)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_reads_dragons_and_spawn_chips(reader, name):
    result = reader.read(frame_from(name))
    assert result["dragons"] == EXPECTED[name]["dragons"]
    assert result["chips"] == EXPECTED[name]["chips"]


def test_red_dragons_are_read_at_the_mirrored_position(reader):
    """录像里红方没拿过龙。红方图标的位置是按蓝方镜像推的：把蓝方的两个图标搬过去，应该读成红方的。"""
    frame = frame_from("hud-306")
    spec = reader.layout["dragons"]
    for blue_x, red_x in zip(spec["x"]["blue"][:2], spec["x"]["red"][:2]):
        rows = slice(spec["top"], spec["top"] + spec["size"])
        frame[rows, red_x:red_x + spec["size"]] = frame[rows, blue_x:blue_x + spec["size"]]
    assert objectives.read_dragons(frame, reader.layout) == {"blue": ["ocean", "mountain"], "red": ["ocean", "mountain"]}


def test_icon_colours():
    def blob(bgr):
        roi = np.full((34, 34, 3), 25, np.uint8)
        yy, xx = np.mgrid[:34, :34]
        roi[np.hypot(yy - 16.5, xx - 16.5) < 10] = bgr
        return roi
    assert objectives.icon_kind(blob((164, 177, 116))) == "ocean"
    assert objectives.icon_kind(blob((112, 143, 186))) == "mountain"
    assert objectives.icon_kind(blob((55, 106, 220))) == "infernal"         # 火龙只在刷新牌子上见过，颜色取自那里
    assert objectives.icon_kind(blob((211, 6, 146))) == "baron"
    assert objectives.icon_kind(blob((208, 83, 213))) == "herald"           # 先锋是粉色的，和男爵的紫色要分开
    assert objectives.icon_kind(blob((200, 200, 200))) == "other"           # 是个图标，但不是认识的颜色
    assert objectives.icon_kind(np.full((34, 34, 3), 25, np.uint8)) is None
    assert objectives.icon_kind(np.full((34, 34, 3), 190, np.uint8)) == objectives.UNSURE      # 背后的画面太亮


def live_frame(**blocks) -> np.ndarray:
    """实时 OB 录像（人机局）里裁下来的几小块，贴回整帧。键是 live-ob.npz 里的名字，值是左上角 (y, x)。"""
    canvas = np.zeros((1264, 2800, 3), np.uint8)
    with np.load(FIXTURES / "live-ob.npz") as data:
        for key, (y, x) in blocks.items():
            block = data[key]
            canvas[y:y + block.shape[0], x:x + block.shape[1]] = block
    return canvas


def test_herald_chip(reader):
    """06:17：上面那个坑是先锋（粉色图标），下面是火龙，都写着“存活”。"""
    assert reader.chips.read(live_frame(chips=(90, 680))) == {"upper": ("herald", True), "lower": ("infernal", True)}


def chips_frame(key: str) -> np.ndarray:
    canvas = np.zeros((1264, 2800, 3), np.uint8)
    with np.load(FIXTURES / "chips-elder.npz") as data:
        canvas[90:270, 680:880] = data[key]
    return canvas


def test_buff_timer_chip(reader):
    """10-20 直播 20:51：蓝方打掉远古龙，原生界面把两块刷新牌子（男爵存活、远古龙存活）都收起来，
    只剩一块蓝底的远古龙 buff 计时（02:00）。"""
    assert reader.chips.read(chips_frame("before")) == {"upper": ("baron", True), "lower": ("other", True)}
    assert reader.chips.read(chips_frame("after")) == {"upper": None, "lower": None, "buff": ("elder", "blue")}


def test_elder_buff_does_not_kill_baron():
    """男爵的“存活”牌子是被 buff 计时挤掉的，不是被打掉：只记远古龙，阵营看 buff 牌子的底色。"""
    tracker = Tracker()
    alive = {"upper": ("baron", True), "lower": ("other", True)}
    after = {"upper": None, "lower": None, "buff": ("elder", "blue")}
    out = kills(tracker, [({}, alive)] * 6 + [({}, after)] * 15, clock=20 * 60 + 45)
    assert [(c["what"], c.get("side")) for c in out] == [("elder", "blue")]


def test_timeline_head_shows_who_took_it(reader):
    """实时 OB：蓝方打掉男爵后，时间轴最右端蓝方那一行多出一个图标；红方那一行没有变化。"""
    before = objectives.read_timeline(live_frame(timeline_before=(860, 1780)), reader.layout)
    after = objectives.read_timeline(live_frame(timeline_after=(860, 1780)), reader.layout)
    assert before == {"blue": 0, "red": 0}                  # 旁边那条暗红色的击杀竖线不算图标
    assert after["blue"] > 250 and after["red"] == 0
    assert objectives.read_timeline(frame_from("hud-205"), reader.layout) is None       # 回放：播放头不在最右端
    assert objectives.read_timeline(np.zeros((1264, 2800, 3), np.uint8), reader.layout) is None


def test_reads_spells_and_runes(reader):
    """10:55 这一帧：十个人的召唤师技能和基石符文。正在冷却的技能被压暗，认不出（“?”），亮度也明显低。"""
    result = reader.read(frame_from("hud-205"))
    values, lights = result["values"], result["lights"]
    assert [values[f"blue.{i}.spells"] for i in range(5)] == [
        [UNKNOWN, "引燃"], ["惩戒", UNKNOWN], ["解封的传送", "闪现"], ["屏障", "闪现"], ["治疗术", "闪现"]]
    assert [values[f"red.{i}.spells"] for i in range(5)] == [
        ["屏障", "闪现"], [UNKNOWN, "惩戒"], ["闪现", "引燃"], ["屏障", "闪现"], ["引燃", "闪现"]]
    assert [values[f"blue.{i}.rune"] for i in range(5)] == ["不灭之握", "电击", "奥术彗星", "致命节奏", "艾黎"]
    assert [values[f"red.{i}.rune"] for i in range(5)] == ["征服者", "致命节奏", "相位猛冲", "强攻", "电击"]
    assert lights["blue.0.spell"][0] < 0.5 * lights["blue.0.spell"][1]          # 狗熊的闪现在冷却
    assert len(lights["red.4.item"]) == 6 and min(lights["red.4.spell"]) > 150


def test_reads_levels(reader):
    """头像右下角的等级。数字压在头像上：阵亡时不显示（None）；十位被头像的亮斑糊住时只读得出个位（12 → 2、
    10 → 0）；带小尖角的 11 在个别头像上会读成 14。后两种由 tracker 结合之前的等级纠正。"""
    def levels(name):
        values = reader.read(frame_from(name))["values"]
        return [[values.get(f"{side}.{i}.level") for i in range(5)] for side in ("blue", "red")]
    assert levels("hud-000") == [[10, 9, 10, 8, None], [9, None, 9, 8, 8]]         # 实际：10 9 10 8 7 / 9 阵亡 9 8 8
    assert levels("hud-205") == [[2, 11, 11, 11, 0], [11, 11, 14, None, 9]]        # 实际：12 11 11 11 10 / 11 11 11 阵亡 9
    assert levels("hud-306") == [[13, 2, 12, 11, 1], [None, 11, 12, 10, None]]     # 实际：13 12 12 11 11 / 阵亡 11 12 10 阵亡


def test_reads_levels_on_light_portraits(reader):
    """10-19 直播：萨勒芬妮、剑姬、娑娜的头像在等级那一角是浅粉白色，和数字一样亮，按亮度整块都是“字”，读不出来，
    面板上的等级卡在了 9 级 / 1 级。备用读法（顶帽）读得出，但标成 weak。夹具是那段录像里的五小块，人眼核对过。"""
    with np.load(FIXTURES / "levels-bright.npz") as data:
        crops = {key: data[key] for key in data.files}

    def read(key):
        reader._cache.clear()
        reader.weak = set()
        result = reader.read_level("red.4.level", crops[key])
        return result and result[0], bool(reader.weak)
    assert read("seraphine9") == (9, True)
    assert read("fiora6") == (6, True)
    assert read("sona9") == (9, True)
    assert read("fiora11") == (4, True)         # 实际 11：只读出个位，“1”还读成了“4”，tracker 按已有等级纠正
    assert read("seraphine3") == (3, True)      # 实际 8：左半边笔画挨着一大块亮区，被当成背景减掉了


def test_weak_level_reads_only_go_up():
    """备用读法读出的等级只许往上走：“8”被读成“3”不会让面板掉级；往上的照常认，十几级的个位也照常补。"""
    tracker = Tracker()

    def feed(value, weak, times=6):
        out = {}
        for _ in range(times):
            tracker._extras({"values": {"red.4.level": value}, "weak": ["red.4.level"] if weak else []}, out)
        return out.get("red.4.level")
    assert feed(8, False) == 8
    assert feed(3, True, times=40) is None      # 读多少次都不掉级
    assert feed(9, True) == 9
    assert feed(0, True) == 10                  # 只读出个位
    assert feed(4, True) == 11                  # 11 的“1”读成“4”


def test_ult_progress():
    """大招圆点：没学 / 刚用掉是暗的，冷却中是一块绿色扇形，转好了整块绿。"""
    size = 19
    yy, xx = np.mgrid[:size, :size]
    disc = np.hypot(yy - 9, xx - 9) <= 10
    angle = (np.degrees(np.arctan2(xx - 9, 9 - yy)) + 360) % 360
    def dot(done):
        roi = np.full((size, size, 3), 30, np.uint8)
        roi[disc & (angle < done * 360)] = (40, 200, 60)
        return roi
    assert ult_progress(dot(0)) == 0
    assert ult_progress(dot(1)) >= 0.97
    assert 0.2 <= ult_progress(dot(0.25)) <= 0.3 and 0.7 <= ult_progress(dot(0.75)) <= 0.8
    assert ult_progress(np.full((size, size, 3), 200, np.uint8)) == 0           # 白的不算绿


def test_panel_collapsed(reader):
    result = reader.read(frame_from("hud-205", "panel"))
    assert result["panel"] is False
    assert result["clock"] == EXPECTED["hud-205"]["clock"]
    values = result["values"]
    assert {key: value for key, value in values.items() if key.count(".") == 1} == EXPECTED["hud-205"]["top"]
    # 逐人的经济、KDA、装备都在面板里，一项都不出；复活倒计时、技能、符文在两侧卡片上，照常读
    assert not [key for key in values if key.endswith((".gold", ".kda", ".items")) and key.count(".") == 2]
    assert values["blue.4.spells"] == ["治疗术", "闪现"] and not [key for key in result["lights"] if key.endswith(".item")]
    assert values["red.3.respawn"] == 22 and values["blue.0.respawn"] == 0


def test_blank_frame(reader):
    result = reader.read(np.zeros((1264, 2800, 3), np.uint8))
    assert result == {"clock": None, "panel": False, "values": {}, "scores": {}}


def test_other_aspect_ratio_is_refused(reader):
    with pytest.raises(layout_mod.LayoutError, match="宽高比"):
        reader.read(np.zeros((1080, 1920, 3), np.uint8))


# ---- 认装备 ----

def test_item_matcher_on_its_own_library():
    matcher = ItemMatcher()
    assert len(matcher.names) == 177 and "潜行药剂" not in matcher.names and "放血者的诅咒" in matcher.names
    with np.load(TEMPLATES / "items.npz") as data:
        icons = data["icons"]
    assert [matcher.match(icon) for icon in icons] == matcher.names         # 库里的每一件都认得出自己
    assert matcher.match(np.full((44, 44, 3), 37, np.uint8)) is None        # 空格子
    rng = np.random.default_rng(7)
    assert matcher.match(rng.integers(0, 255, (44, 44, 3), dtype=np.uint8)) == UNKNOWN      # 有东西但哪件都不像


def test_darkened_item_is_never_mistaken():
    """主动装备冷却时整格被压暗：要么还认得出，要么说认不准（或者暗到像空格子），不能认成别的。"""
    matcher = ItemMatcher()
    cells = layout_mod.item_cells(frame_from("hud-306"), layout_mod.load_layout())
    for card in ("blue.3", "red.4"):
        for cell in cells[card]:
            name = matcher.match(cell)
            for dim in (0.6, 0.35, 0.15):
                assert matcher.match((cell * dim).astype(np.uint8)) in (name, UNKNOWN, None)


# ---- 取画面的设备、小地图 ----

def test_pick_the_data_device_by_screen_shape():
    from vision.sources import pick_device
    phone, emulator = (2800, 1264), (2560, 1440)
    assert pick_device({"AJTL0": phone, "127.0.0.1:16384": emulator}, 2800 / 1264) == "AJTL0"
    assert pick_device({"AJTL0": phone, "X": phone}, 2800 / 1264) is None              # 两台都对得上：分不出来
    assert pick_device({"127.0.0.1:16384": emulator, "Y": None}, 2800 / 1264) is None



# ---- 解码器 ----

def line(glyph_set: GlyphSet, text: str, gap: int = 2, pad: int = 6) -> np.ndarray:
    """用模板自己拼一行字，字和字之间隔 gap 列（负数 = 压在一起）。"""
    glyphs = [glyph_set.glyphs[char] for char in text]
    width = pad * 2 + sum(glyph.shape[1] for glyph in glyphs) + gap * (len(glyphs) - 1)
    ink = np.zeros((glyph_set.height, width), np.float32)
    x = pad
    for glyph in glyphs:
        ink[:, x:x + glyph.shape[1]] = np.maximum(ink[:, x:x + glyph.shape[1]], glyph)
        x += glyph.shape[1] + gap
    return ink


@pytest.mark.parametrize("text", ["500", "6640", "5150", "10024", "7014", "6717", "0/0/0", "12/10/7", "1/11/1"])
def test_decode_synthetic_lines(text):
    panel = GlyphSet.load("panel", (-2, 7))
    gaps = (-1, 0, 2, 5) if text.isdigit() else (0, 2, 5)          # 数字之间会粘连；斜杠只有一笔，压住了就认不出
    for gap in gaps:
        decoded = panel.decode(line(panel, text, gap))
        assert decoded is not None and decoded[0] == text, (text, gap, decoded)


def test_decode_does_not_read_half_a_four_as_one():
    """“4”的右边一竖单看很像“1”。左半边对不上模板时，不能跳过它去认那一竖。"""
    panel = GlyphSet.load("panel", (-2, 7))
    for text in ("4474", "3443", "545"):
        ink = line(panel, text, gap=1)
        clean = panel.decode(ink)
        assert clean is not None and clean[0] == text
        # 把第二个“4”整体调暗、加一点噪声，让它对模板的匹配分掉下去
        position = 6 + sum(panel.glyphs[char].shape[1] + 1 for char in text[:text.index("4", 1)])
        width = panel.glyphs["4"].shape[1]
        rng = np.random.default_rng(4)
        damaged = ink.copy()
        damaged[:, position:position + width - 4] *= 0.75
        damaged[:, position:position + width - 4] += rng.random((panel.height, width - 4)).astype(np.float32) * 0.25
        decoded = panel.decode(damaged)
        assert decoded is None or decoded[0] == text, (text, decoded)


def test_decode_never_drops_a_character():
    """认不出的字不能悄悄跳过：“5150”读成“550”比读不出糟糕得多。"""
    panel = GlyphSet.load("panel", (-2, 7))
    ink = line(panel, "5150", gap=2)
    start = 6 + panel.glyphs["5"].shape[1] + 2
    ink[:8, start:start + panel.glyphs["1"].shape[1]] = 0           # 把“1”的上半截抹掉，让它对不上模板
    decoded = panel.decode(ink)
    assert decoded is None or decoded[0] == "5150"


def test_decode_rejects_noise():
    panel = GlyphSet.load("panel", (-2, 7))
    assert panel.decode(np.zeros((panel.height, 80), np.float32)) is None
    rng = np.random.default_rng(1)
    assert panel.decode((rng.random((panel.height, 80)) > 0.5).astype(np.float32)) is None


def test_find_clock_among_clutter():
    clock = GlyphSet.load("clock", (1, 10))
    pattern = [DIGITS, DIGITS, frozenset(":"), DIGITS, DIGITS]
    for text in ("08:10", "11:11", "04:19", "17:26"):
        ink = line(clock, text, gap=4, pad=12)
        ink[2:9, 0:6] = 1.0                         # 画面背景里的亮斑
        ink[10:30, -4:] = 0.8
        found = clock.find(ink, pattern)
        assert found is not None and found[0] == text, (text, found)
    assert clock.find(np.zeros((clock.height, 120), np.float32), pattern) is None


def test_every_group_has_all_digits():
    for name in ("panel", "clock", "top-gold", "top-kills", "top-towers", "respawn"):
        assert DIGITS <= GlyphSet.load(name).characters, name
    assert {"/"} <= GlyphSet.load("panel").characters
    assert {".", "K"} <= GlyphSet.load("top-gold").characters


# 选手卡片上的血条（tests/fixtures/bars.npz）：两段实时录像里裁的卡片下半截（36×108，左上角是那张卡片的
# (566 + 155×序号, 蓝方 172 / 红方 2520)）。期望值是把每一块放大 4 倍，量绿色 / 灰色 / 蓝黄色到哪一列，和读数对过的。
BARS = {
    "blue normal": ((0.38, 0.0), (0.21, "mana")),
    "low yellow": ((0.17, 0.0), (0.10, "mana")),          # 低血量：血是黄的，空的那段是暗红
    "dead": (None, (0.0, None)),                           # 阵亡：血条框不在
    "energy": ((0.82, 0.0), (1.0, "energy")),
    "riven": ((0.80, 0.04), (0.0, None)),                  # 没有资源条的英雄
    "red low": ((0.14, 0.0), (0.95, "mana")),
    "red normal": ((0.52, 0.0), (0.93, "mana")),
    "shield": ((0.68, 0.11), (0.35, "energy")),            # 血后面接着一段灰色的护盾
}


def bar_frames():
    with np.load(FIXTURES / "bars.npz") as data:
        for crop, side, index, name in zip(data["crops"], data["sides"], data["index"], data["names"]):
            frame = np.zeros((1264, 2800, 3), np.uint8)
            y, x = 566 + 155 * int(index), 172 if side == "blue" else 2520
            frame[y:y + crop.shape[0], x:x + crop.shape[1]] = crop
            yield str(name), layout_mod.bar_fields(frame, layout_mod.load_layout())[f"{side}.{int(index)}"]


def test_reads_hp_and_resource_bars():
    seen = set()
    for name, (hp, gap, resource) in bar_frames():
        seen.add(name)
        health, mana = hp_bar(hp, gap), resource_bar(resource)
        if name == "flash":                     # 低血量时整条闪成亮红，橙色的血和底色分不太开：只要求读出来很少
            assert health is not None and health[0] < 0.25
            continue
        expected_health, expected_mana = BARS[name]
        if expected_health is None:
            assert health is None, name
        else:
            assert health == pytest.approx(expected_health, abs=0.02), name
        assert mana[0] == pytest.approx(expected_mana[0], abs=0.02) and mana[1] == expected_mana[1], name
    assert seen == set(BARS) | {"flash"}


def test_bars_are_not_read_off_the_map():
    """阵亡时那里是地图：花花绿绿的地面不能被当成血条。"""
    rng = np.random.default_rng(1)
    for _ in range(50):
        block = rng.integers(0, 256, (3, 76, 3), dtype=np.uint8)
        gap = rng.integers(40, 256, (2, 76, 3), dtype=np.uint8)
        assert hp_bar(block, gap) is None


# ---- 去抖、校验、时钟 ----

def reading(clock=None, panel=True, **values):
    return {"clock": clock, "panel": panel, "values": {key.replace("_", "."): value for key, value in values.items()}}


def collect(commands, kind):
    return [command for command in commands if command["type"] == kind]


def set_values(commands):
    merged = {}
    for command in collect(commands, "game.set"):
        assert command["source"] == "vision"
        merged.update(command["values"])
    return merged


def test_value_needs_two_reads():
    tracker = Tracker()
    assert set_values(tracker.update(reading(blue_kills=3), 0.0)) == {}
    assert set_values(tracker.update(reading(blue_kills=3), 0.2)) == {"blue.kills": 3}
    assert set_values(tracker.update(reading(blue_kills=3), 0.4)) == {}             # 没变化不重复发
    assert set_values(tracker.update(reading(blue_kills=8), 0.6)) == {}             # 一闪而过的错读
    assert set_values(tracker.update(reading(blue_kills=3), 0.8)) == {}
    tracker.update(reading(blue_kills=4), 1.0)
    assert set_values(tracker.update(reading(blue_kills=4), 1.2)) == {"blue.kills": 4}


def player_reading(golds, team=None, kda=(0, 0, 0), **extra):
    values = {f"blue.{i}.gold": gold for i, gold in enumerate(golds)}
    values.update({f"blue.{i}.kda": kda for i in range(5)})
    if team is not None:
        values["blue.gold"] = team
    return {"clock": None, "panel": True, "values": values, **extra}


def test_kda_and_gold_commit_per_player():
    tracker = Tracker()
    tracker.update(player_reading([500] * 5), 0.0)
    values = set_values(tracker.update(player_reading([500] * 5), 0.2))
    assert values["blue.2.gold"] == 500 and values["blue.4.assists"] == 0 and len(values) == 20
    assert "blue.gold" not in values                    # 面板展开时不用顶部条那个四舍五入的团队经济


def test_gold_with_an_extra_digit_is_rejected():
    tracker = Tracker()
    for now in (0.0, 0.2):
        tracker.update(player_reading([6634, 5000, 5000, 5000, 5000], team=26600), now)
    wrong = player_reading([66340, 5000, 5000, 5000, 5000], team=26600)     # 6634 多读了一位
    for now in (0.4, 0.6, 0.8):
        assert set_values(tracker.update(wrong, now)) == {}
    assert tracker.rejected >= 3
    assert tracker.fields["blue.0.gold"].value == 6634
    # 没有顶部条可对账时，靠“涨得离谱而 KDA 没变”挡住
    other = Tracker()
    for now in (0.0, 0.2):
        other.update(player_reading([6634, 5000, 5000, 5000, 5000]), now)
    for now in (0.4, 0.6):
        assert set_values(other.update(player_reading([66340, 5000, 5000, 5000, 5000]), now)) == {}


def test_gold_drop_needs_three_reads():
    """经济只增不减；真的变小了（新的一局、回放往回拖）要连续三次才认。"""
    tracker = Tracker()
    for now in (0.0, 0.2):
        tracker.update(player_reading([9000] * 5), now)
    seen = [set_values(tracker.update(player_reading([500] * 5), 5.0 + i * 0.2)) for i in range(10)]
    assert all(values == {} for values in seen[:-1])                # 前 1.8 秒不认
    assert seen[-1]["blue.0.gold"] == 500                            # 持续了 2 秒：是真的


def test_brief_misread_downwards_is_ignored():
    """实跑时出过：4474 被读成 4174，持续不到一秒（下一次经济变化就好了）。"""
    tracker = Tracker()
    for now in (0.0, 0.2):
        tracker.update(player_reading([4471, 5000, 5000, 5000, 5000]), now)
    for i in range(5):                                               # 整整一秒都读成 4174
        assert set_values(tracker.update(player_reading([4174, 5000, 5000, 5000, 5000]), 1.0 + i * 0.2)) == {}
    tracker.update(player_reading([4477, 5000, 5000, 5000, 5000]), 2.0)
    assert set_values(tracker.update(player_reading([4477, 5000, 5000, 5000, 5000]), 2.2)) == {"blue.0.gold": 4477}


def test_collapsed_panel_freezes_players_and_uses_top_gold():
    tracker = Tracker()
    for now in (0.0, 0.2):
        tracker.update(player_reading([5000] * 5, team=25000), now)
    hidden = {"clock": None, "panel": False, "values": {"blue.gold": 26100, "blue.kills": 2}}
    seen = []
    for now in (0.4, 0.6, 0.8, 1.0):
        seen += tracker.update(hidden, now)
    status = collect(seen, "vision.status")
    assert status and status[-1]["panel"] is False
    values = set_values(seen)
    assert values == {"blue.gold": 26100, "blue.kills": 2}
    assert tracker.fields["blue.0.gold"].value == 5000
    # 面板重新展开：把顶部条那个团队经济清掉，状态中心回到“五人之和”
    seen = []
    for now in (1.2, 1.4, 1.6, 1.8):
        seen += tracker.update(player_reading([5600] * 5, team=28000), now)
    values = set_values(seen)
    assert values["blue.gold"] is None and values["red.gold"] is None and values["blue.0.gold"] == 5600


# ---- 装备和复活倒计时 ----

def test_slot_needs_repeated_reads():
    slot = Slot()
    assert not slot.known
    assert slot.offer("长剑") is False and slot.offer("长剑") is True and slot.value == "长剑"
    assert slot.offer("短剑") is False and slot.offer("长剑") is False        # 一闪而过的错读
    assert slot.offer("短剑") is False and slot.offer("短剑") is True         # 要重新连续两次
    assert [slot.offer(None, need=4) for _ in range(4)] == [False, False, False, True] and slot.value is None


def extras_reading(**values):
    return {"clock": 600, "panel": True, "values": {key.replace("_", "."): value for key, value in values.items()}}


BUILD = ["铁板靴", None, None, "长剑", "破舰者", "黄昏与黎明"]


def test_items_commit_as_a_whole_row():
    tracker = Tracker()
    assert "blue.0.items" not in set_values(tracker.update(extras_reading(blue_0_items=BUILD), 0.0))
    assert set_values(tracker.update(extras_reading(blue_0_items=BUILD), 0.2))["blue.0.items"] == BUILD
    assert "blue.0.items" not in set_values(tracker.update(extras_reading(blue_0_items=BUILD), 0.4))
    # 长剑合成了岚切：只换那一格，发出去的仍是完整的 6 格
    upgraded = BUILD[:3] + ["岚切"] + BUILD[4:]
    tracker.update(extras_reading(blue_0_items=upgraded), 0.6)
    assert set_values(tracker.update(extras_reading(blue_0_items=upgraded), 0.8))["blue.0.items"] == upgraded


def test_first_row_waits_for_every_slot():
    """一开局就有一格认不准（“?”）：其余五格先攒着，等那一格认出来再整行发出。"""
    tracker = Tracker()
    unsure = [UNKNOWN] + BUILD[1:]
    for now in (0.0, 0.2, 0.4):
        assert "red.2.items" not in set_values(tracker.update(extras_reading(red_2_items=unsure), now))
    assert tracker.items_of("red.2.items") is None
    tracker.update(extras_reading(red_2_items=BUILD), 0.6)
    assert set_values(tracker.update(extras_reading(red_2_items=BUILD), 0.8))["red.2.items"] == BUILD


def test_unsure_and_briefly_empty_slots_keep_the_item():
    """主动装备进冷却：格子被压暗，一会儿认不准、一会儿像空格子。面板上那件装备不能跟着闪。"""
    tracker = Tracker()
    for now in (0.0, 0.2):
        tracker.update(extras_reading(blue_0_items=BUILD), now)
    unsure = BUILD[:4] + [UNKNOWN] + BUILD[5:]
    empty = BUILD[:4] + [None] + BUILD[5:]
    for i in range(ITEM_GONE_CONFIRM * 3 + 1):                       # 六秒：空、空、认不准、空、空、认不准……
        reading_ = extras_reading(blue_0_items=empty if (i + 1) % 3 else unsure)
        assert "blue.0.items" not in set_values(tracker.update(reading_, 1.0 + i * 0.2))
    tracker.update(extras_reading(blue_0_items=unsure), 7.4)
    assert tracker.items_of("blue.0.items") == BUILD
    # 真的卖掉了：连续 ITEM_GONE_CONFIRM 次都是空格子
    seen = [set_values(tracker.update(extras_reading(blue_0_items=empty), 5.0 + i * 0.2)) for i in range(ITEM_GONE_CONFIRM)]
    assert all("blue.0.items" not in values for values in seen[:-1])
    assert seen[-1]["blue.0.items"] == empty


def respawn_values(tracker, samples, start=0.0):
    """samples: 每 0.2 秒读到的倒计时 → 每一次发给状态中心的值（没发的位置是 "-"）。"""
    out = []
    for i, seconds in enumerate(samples):
        values = set_values(tracker.update(extras_reading(red_3_respawn=seconds), start + i * 0.2))
        out.append(values.get("red.3.respawn", "-"))
    return out


def test_respawn_countdown():
    tracker = Tracker()
    assert respawn_values(tracker, [0, 0]) == ["-", None]                    # 活着：状态中心里是 None
    assert respawn_values(tracker, [22, 22, 22, 21, 21, 20, 20]) == ["-", 22, "-", "-", 21, "-", 20]
    # 读错一帧（22 读成 2）不算
    assert respawn_values(tracker, [2, 19, 19]) == ["-", "-", 19]
    # 正常复活：倒计时走到头
    assert respawn_values(tracker, [1, 1, 0, 0]) == ["-", 1, "-", None]


def test_early_revive_needs_longer():
    """倒计时还剩十几秒，头像被技能特效盖住读成“活着”：要持续一秒才认。"""
    tracker = Tracker()
    respawn_values(tracker, [15, 15])
    assert respawn_values(tracker, [0] * (REVIVE_CONFIRM - 1) + [14, 14]) == ["-"] * REVIVE_CONFIRM + [14]
    seen = respawn_values(tracker, [0] * REVIVE_CONFIRM)
    assert seen == ["-"] * (REVIVE_CONFIRM - 1) + [None]                     # 持续够久：真的活了（比如被复活）


def test_respawn_and_items_reach_the_store(store):
    tracker = Tracker()
    frames = [extras_reading(blue_0_items=BUILD, blue_0_respawn=18, red_4_respawn=0) for _ in range(2)]
    for index, frame in enumerate(frames):
        for command in tracker.update(frame, index * 0.2):
            store.apply(command, index * 200)
    player = store.game["players"]["blue"][0]
    assert player["items"] == BUILD and player["respawn"] == 18 and player["alive"] is False
    assert store.game["players"]["red"][4]["alive"] is True

    # 状态中心被清空：对账把装备和阵亡状态补回去；导播锁住的那一项不碰
    store.apply({"type": "game.new"}, 1000)
    store.apply({"type": "game.set", "path": "blue.0.respawn", "value": 5, "lock": True}, 1000)
    for command in tracker.reconcile(store.snapshot(1000), 1.0):
        store.apply(command, 1000)
    player = store.game["players"]["blue"][0]
    assert player["items"] == BUILD and player["respawn"] == 5
    assert not [c for c in tracker.reconcile(store.snapshot(1000), 1.0) if c["type"] == "game.set"]


# ---- 等级和大招 ----

def level_run(tracker, reads, start=0.0):
    """reads: 每 0.2 秒读到的等级（None = 没读出来）。返回提交的等级序列。"""
    out = []
    for i, value in enumerate(reads):
        values = {} if value is None else {"blue.0.level": value}
        commands = tracker.update({"clock": 600, "panel": True, "values": values}, start + i * 0.2)
        out += [v["blue.0.level"] for v in map(lambda c: c.get("values", {}), commands) if "blue.0.level" in v]
    return out


def test_level_goes_up_one_at_a_time():
    tracker = Tracker()
    assert level_run(tracker, [7] * 5 + [8] * 5) == [7, 8]
    assert level_run(tracker, [8] * 3 + [18, 3, None, 8] + [9] * 5, start=2.0) == [9]      # 零星的错读不算
    assert level_run(tracker, [12] * 14, start=4.0) == []                                   # 一下子涨三级：再看看
    assert level_run(tracker, [12] * 3, start=7.0) == [12]                                  # 稳稳读了 3 秒，认了（中间漏读了几级）


def test_level_fills_in_the_tens_digit():
    """十位被头像的亮斑糊住时只读得出个位：之前已经 9 级以上了，读到 0–5 就是十几级。"""
    tracker = Tracker()
    assert level_run(tracker, [9] * 5 + [0] * 5 + [1] * 5 + [2] * 5) == [9, 10, 11, 12]
    assert level_run(tracker, [2] * 30 + [3] * 5, start=10.0) == [13]                       # 个位一直是 2：还是 12，不是掉到 2 级
    other = Tracker()
    assert level_run(other, [3] * 5 + [2] * 20) == [3]                                      # 3 级时读到 2：不是 12
    assert level_run(other, [2] * 6, start=5.0) == [2]                                      # 稳稳 5 秒都是 2：开局调对位换了人


def test_flagged_one_read_as_four():
    """带小尖角的“1”在个别头像上会被认成“4”：10 级之后读到 14，其实是 11。"""
    tracker = Tracker()
    assert level_run(tracker, [10] * 5 + [14] * 40 + [12] * 5 + [13] * 5 + [14] * 5) == [10, 11, 12, 13, 14]
    assert level_run(Tracker(), [3] * 5 + [4] * 5) == [3, 4]                                # 真的 4 级不受影响


def test_ult_progress_is_reported_in_steps():
    tracker = Tracker()
    def run(reads, start):
        out = []
        for i, value in enumerate(reads):
            commands = tracker.update({"clock": 600, "panel": True, "values": {"red.1.ult": value}}, start + i * 0.2)
            out += [c["values"]["red.1.ult"] for c in commands if c["type"] == "game.set" and "red.1.ult" in c["values"]]
        return out
    assert run([0.98, 0.99], 0.0) == [1.0]                                          # 转好了
    assert run([0.99, 0.97, 0.98, 0.99], 1.0) == []
    assert run([0.0, 0.0], 2.0) == [0.0]                                            # 用掉了
    climbing = run([round(i * 0.004, 3) for i in range(250)], 3.0)                  # 50 秒转回来
    assert climbing[-1] == 1.0 and 8 <= len(climbing) <= 14 and climbing == sorted(climbing)


def bar_run(tracker, reads, start=0.0, step=0.1):
    """reads: 每 step 秒一份 {"blue.0": (血量, 护盾, 资源, 颜色)}。返回 [(时间, {"blue.0": {...}})]：每次发出的血条。"""
    out = []
    for i, bars in enumerate(reads):
        now = start + i * step
        values = {f"{player}.bars": value for player, value in bars.items()}
        sent = {key[:-len(".bars")]: value for key, value in set_values(tracker.update(reading(clock=600, **{}) | {"values": values}, now)).items()
                if key.endswith(".bars")}
        if sent:
            out.append((round(now, 2), sent))
    return out


def test_bars_need_two_steady_reads_and_a_real_change():
    tracker = Tracker()
    sent = bar_run(tracker, [{"blue.0": (0.80, 0.0, 0.5, "mana")}] * 8)
    assert sent[0] == (0.1, {"blue.0": {"hp": 0.8, "shield": 0.0, "mana": 0.5, "resource": None}})      # 第二次读到才报
    assert [(at, bars["blue.0"]["resource"]) for at, bars in sent] == [(0.1, None), (0.5, "mana")]  # 颜色连续 5 次一样才认
    assert bar_run(tracker, [{"blue.0": (0.81, 0.0, 0.51, "mana")}] * 5, start=0.8) == []            # 差 1% 不报
    # 一下子掉了一大截：掉的那一帧和前一帧对不上，等下一帧读稳了再报
    sent = bar_run(tracker, [{"blue.0": (0.30, 0.0, 0.5, "mana")}] * 3, start=1.3)
    assert [(at, bars["blue.0"]["hp"]) for at, bars in sent] == [(1.4, 0.3)]
    assert bar_run(tracker, [{"blue.0": (1.0, 0.0, 1.0, "mana")}, {"blue.0": (0.5, 0.0, 1.0, "mana")}] * 4, start=1.6) == []  # 一直在跳：不报


def test_bars_are_sent_at_most_five_times_a_second():
    tracker = Tracker()
    players = [f"{side}.{index}" for side in ("blue", "red") for index in range(5)]
    reads = []
    for i in range(40):                                     # 打团：十个人的血每帧都在掉，四秒
        hp = round(1.0 - i * 0.02, 2)
        reads.append({player: (hp, 0.0, 0.5, "mana") for player in players})
    sent = bar_run(tracker, reads)
    assert 12 <= len(sent) <= 4 / BAR_EVERY + 1
    assert all(later - earlier >= BAR_EVERY - 1e-9 for (earlier, _), (later, _) in zip(sent, sent[1:]))
    assert all(len(bars) == 10 for _, bars in sent)         # 十个人一起报
    assert sent[-1][1]["red.4"]["hp"] == pytest.approx(0.24, abs=0.03)


def test_bars_reach_the_panel_and_come_back_after_a_reset(store):
    tracker = Tracker()
    for i in range(3 + BAR_KIND_CONFIRM):
        for command in tracker.update({"clock": 600, "panel": True, "values": {"red.2.bars": (0.45, 0.1, 0.0, "energy")}}, i * 0.3):
            store.apply(command, int(i * 300))
    assert store.game["players"]["red"][2]["bars"] == {"hp": 0.45, "shield": 0.1, "mana": 0.0, "resource": "energy"}
    store.apply({"type": "game.new"}, 5000)
    assert store.game["players"]["red"][2]["bars"] is None
    for command in tracker.reconcile(store.snapshot(5000), 5.0):
        store.apply(command, 5000)
    assert store.game["players"]["red"][2]["bars"]["hp"] == 0.45
    assert not collect(tracker.reconcile(store.snapshot(5000), 6.0), "game.set")           # 对上了就不再补


def test_bars_field_and_log(store):
    from state import CommandError
    from vision.__main__ import describe
    with pytest.raises(CommandError):
        store.apply({"type": "game.set", "path": "blue.0.bars", "value": [0.5, 0.0]}, 0)
    store.apply({"type": "game.set", "path": "blue.0.bars", "value": {"hp": 1.4, "shield": -1, "resource": "mana"}}, 0)
    assert store.game["players"]["blue"][0]["bars"] == {"hp": 1.0, "shield": 0.0, "mana": 0.0, "resource": "mana"}
    # 血条一秒报好几次，识别服务的日志里不打印；和别的数一起报时只打印别的
    bars = {"hp": 0.5, "shield": 0.0, "mana": 0.2, "resource": "mana"}
    assert describe({"type": "game.set", "values": {"blue.0.bars": bars}}) == ""
    assert describe({"type": "game.set", "values": {"blue.0.bars": bars, "blue.0.gold": 900}}) == "blue.0.gold=900"


# ---- 冷却读条 ----

def lamp_run(tracker, steps, start=0.0, clock=600, **fixed):
    """steps: 每 0.2 秒一份 {"blue.0.spell": [亮度, 亮度], …}；fixed 是每一帧都带着的识别结果。返回每一帧发出的 cooldowns。"""
    out = []
    for i, lights in enumerate(steps):
        now = start + i * 0.2
        reading = {"clock": clock + int(now), "panel": True, "lights": lights,
                   "values": {key.replace("_", "."): value for key, value in fixed.items()}}
        values = set_values(tracker.update(reading, now))
        out.append({key: value for key, value in values.items() if key.endswith(".cooldowns")})
    return out


def test_spell_goes_dark_starts_its_cooldown():
    tracker = Tracker()
    who = dict(blue_0_spells=["闪现", "治疗术"], blue_0_items=BUILD)
    bright, flashed = {"blue.0.spell": [172, 170]}, {"blue.0.spell": [50, 170]}
    assert not any(lamp_run(tracker, [bright] * 10, **who))
    seen = lamp_run(tracker, [flashed] * 3, start=2.0, **who)
    start, end = next(step for step in seen if step)["blue.0.cooldowns"]["spell.0"]
    assert 601 <= start <= 603 and end - start == 150                    # 从变暗的那一刻起 150 秒
    # 图标一点点亮回来（原生是扇形扫过去），读条不受影响
    assert not any(lamp_run(tracker, [{"blue.0.spell": [50 + i * 2, 170]} for i in range(30)], start=3.0, **who))
    # 读条还剩一大半，图标却已经完全亮了、而且一直亮着：那一下其实不是用掉（或者冷却被大幅缩短了），提前结束
    seen = lamp_run(tracker, [bright] * 12, start=9.0, **who)
    assert [step["blue.0.cooldowns"] for step in seen if step] == [{}]
    assert sum(1 for step in seen if not step) >= 7                     # 要完全亮够 1.5 秒才算


def test_cooldown_runs_to_the_end_while_the_icon_sweeps_back():
    """实战里治疗术 85 秒的读条只显示了 57 秒：原生图标在冷却中从 0.25 匀速亮回到 1，亮过八成时读条就被当成结束了。
    现在读条读满；图标亮回来的过程（包括最后几秒已经很亮）都不影响。"""
    tracker = Tracker()
    who = dict(red_4_spells=["治疗术", "闪现"], red_4_items=BUILD)       # 铁板靴：治疗术 100 秒
    lamp_run(tracker, [{"red.4.spell": [172, 172]}] * 5, **who)
    seen = lamp_run(tracker, [{"red.4.spell": [43, 172]}] * 3, start=1.0, **who)
    start, end = next(step for step in seen if step)["red.4.cooldowns"]["spell.0"]
    assert end - start == 100
    sweep = [{"red.4.spell": [172 * (0.25 + 0.75 * min(1.0, i / 490)), 172]} for i in range(540)]     # 98 秒亮回来
    seen = lamp_run(tracker, sweep, start=1.6, **who)
    ended = next(index for index, step in enumerate(seen) if step and step["red.4.cooldowns"] == {})
    assert end - 1 <= 600 + int(1.6 + ended * 0.2) <= end + 1          # 读满了才结束


def test_lucidity_boots_shorten_spell_cooldowns():
    for boots, seconds in (("铁板靴", 100), ("明朗之靴", 85), ("猩红明朗", 80)):
        tracker = Tracker()
        who = dict(red_4_spells=["治疗术", "闪现"], red_4_items=[boots] + BUILD[1:])
        lamp_run(tracker, [{"red.4.spell": [170, 172]}] * 5, **who)
        seen = lamp_run(tracker, [{"red.4.spell": [45, 172]}] * 3, start=1.0, **who)
        start, end = next(step for step in seen if step)["red.4.cooldowns"]["spell.0"]
        assert end - start == pytest.approx(seconds)


def test_smite_and_spells_seen_dark_only_dim():
    tracker = Tracker()                                                  # 惩戒是充能的：只跟着原生图标暗 / 亮
    who = dict(blue_1_spells=["惩戒", "闪现"])
    lamp_run(tracker, [{"blue.1.spell": [175, 174]}] * 5, **who)
    seen = lamp_run(tracker, [{"blue.1.spell": [80, 174]}] * 3, start=1.0, **who)
    assert next(step for step in seen if step)["blue.1.cooldowns"]["spell.0"][1] is None
    other = Tracker()                                                    # 识别启动时闪现已经在冷却：不知道还剩多久
    lamp_run(other, [{"blue.1.spell": [175, 174]}] * 2 + [{"blue.1.spell": [175, 60]}] * 3, **who)
    assert other.cooldowns["blue.1"]["spell.1"][1] is None


def test_teleport_opening_cooldown_and_unleashed_swap():
    """传送开局就在冷却，游戏时间 0:30 才第一次可用；6 分钟时变成“解封的传送”，两者共用一条冷却。"""
    tracker = Tracker()
    who = dict(blue_0_spells=["传送", "闪现"])
    seen = lamp_run(tracker, [{"blue.0.spell": [165, 172]}] * 2 + [{"blue.0.spell": [50, 172]}] * 3, clock=5, **who)
    assert next(step for step in seen if step)["blue.0.cooldowns"]["spell.0"][1] == 30
    # 之后正常使用：150 秒
    tracker = Tracker()
    lamp_run(tracker, [{"blue.0.spell": [165, 172]}] * 5, clock=340, **who)
    seen = lamp_run(tracker, [{"blue.0.spell": [50, 172]}] * 3, start=1.0, clock=340, **who)
    start, end = next(step for step in seen if step)["blue.0.cooldowns"]["spell.0"]
    assert end - start == 150
    # 冷却途中过了 6 分钟，亮回来时已经是“解封的传送”：读条照常结束，不会因为换了图标重新来过；再用还是 150 秒
    unleashed = dict(blue_0_spells=["解封的传送", "闪现"])
    seen = lamp_run(tracker, [{"blue.0.spell": [170, 172]}] * 10, start=30.0, clock=340, **unleashed)
    assert [step["blue.0.cooldowns"] for step in seen if step] == [{}]
    seen = lamp_run(tracker, [{"blue.0.spell": [48, 172]}] * 3, start=40.0, clock=340, **unleashed)
    start, end = next(step for step in seen if step)["blue.0.cooldowns"]["spell.0"]
    assert end - start == 150


def test_item_cooldowns_only_for_long_ones():
    tracker = Tracker()
    build = ["铁板靴", None, None, "耀光", "中娅沙漏", "黄昏与黎明"]
    who = dict(blue_2_items=build, blue_2_spells=["闪现", "引燃"])
    ready = {"blue.2.item": [140, 40, 40, 138, 136, 114]}
    lamp_run(tracker, [ready] * 5, **who)
    used = {"blue.2.item": [140, 40, 40, 41, 38, 30]}                    # 耀光、中娅、黄昏与黎明同时暗了
    seen = lamp_run(tracker, [used] * 3, start=1.0, **who)
    running = next(step for step in seen if step)["blue.2.cooldowns"]
    assert list(running) == ["item.4"] and running["item.4"][1] - running["item.4"][0] == 90      # 一两秒的被动不读条
    # 中娅卖掉换成了别的：那一格的读条作废
    sold = dict(who, blue_2_items=build[:4] + ["守护天使"] + build[5:])
    seen = lamp_run(tracker, [used] * 4, start=2.0, **sold)
    assert [step["blue.2.cooldowns"] for step in seen if step] == [{}]


def test_spells_rune_and_cooldowns_reach_the_store(store):
    tracker = Tracker()
    who = dict(blue_0_spells=["闪现", UNKNOWN], blue_0_rune="不灭之握", blue_0_items=BUILD)
    steps = [{"blue.0.spell": [172, 170]}] * 4 + [{"blue.0.spell": [50, 170]}] * 3
    for i, lights in enumerate(steps):
        reading = {"clock": 600 + i // 5, "panel": True, "lights": lights, "values": {k.replace("_", "."): v for k, v in who.items()}}
        for command in tracker.update(reading, i * 0.2):
            store.apply(command, i * 200)
    player = store.game["players"]["blue"][0]
    assert player["spells"] == ["闪现", None] and player["rune"] == "不灭之握"          # 认不出的那个先空着
    assert player["cooldowns"]["spell.0"][1] - player["cooldowns"]["spell.0"][0] == 150
    store.apply({"type": "game.new"}, 5000)
    for command in tracker.reconcile(store.snapshot(5000), 2.0):
        store.apply(command, 5000)
    player = store.game["players"]["blue"][0]
    assert player["spells"] == ["闪现", None] and player["rune"] == "不灭之握" and "spell.0" in player["cooldowns"]


# ---- 野怪 ----

def objective_reading(clock, dragons=None, chips=None, timeline=None):
    return {"clock": clock, "panel": True, "values": {}, "dragons": dragons or {}, "chips": chips or {}, "timeline": timeline}


def kills(tracker, samples, start=0.0, clock=600):
    """samples: 每 0.2 秒一份 (dragons, chips)；时钟照常走。返回发出的击杀命令。"""
    out = []
    for i, (dragons, chips) in enumerate(samples):
        now = start + i * 0.2
        out += collect(tracker.update(objective_reading(clock + int(now), dragons, chips), now), "game.objective")
    return out


def test_dragon_icons_become_kills():
    tracker = Tracker()
    none, one, two = {"blue": [], "red": []}, {"blue": ["ocean"], "red": []}, {"blue": ["ocean"], "red": ["infernal"]}
    assert kills(tracker, [(none, {})] * 10) == []
    seen = kills(tracker, [(one, {})] * DRAGON_CONFIRM, start=2.0)
    assert len(seen) == 1 and seen[0]["source"] == "vision"
    assert (seen[0]["what"], seen[0]["side"], seen[0]["element"]) == ("dragon", "blue", "ocean")
    assert 600 <= seen[0]["t"] <= 602                                   # 记的是图标第一次出现的时刻（再减 1 秒的滞后），不是确认的时刻
    assert kills(tracker, [(one, {})] * 20, start=3.0) == []            # 一直挂着，不重复报
    # 一闪而过的误读、某一方看不清的帧，都不算
    assert kills(tracker, [(two, {})] * 2 + [(one, {})] * 3 + [({"red": []}, {})] * 5, start=7.0) == []
    seen = kills(tracker, [(two, {})] * DRAGON_CONFIRM, start=9.0)
    assert [(k["side"], k["element"]) for k in seen] == [("red", "infernal")]


def test_spawn_chip_losing_alive_is_a_kill():
    tracker = Tracker()
    alive, countdown = {"upper": ("baron", True), "lower": None}, {"upper": ("baron", False), "lower": None}
    gone = {"upper": None, "lower": None}
    assert kills(tracker, [({}, countdown)] * 5 + [({}, alive)] * 10, clock=655) == []      # 刷新出来不是击杀
    assert kills(tracker, [({}, gone)] * 3 + [({}, alive)] * 5, start=3.0, clock=655) == [] # 牌子被挡了一下
    seen = kills(tracker, [({}, gone)] * CHIP_GONE_CONFIRM, start=10.0, clock=655)
    assert len(seen) == 1 and seen[0]["what"] == "baron" and "side" not in seen[0]         # 只知道被打掉了
    assert 664 <= seen[0]["t"] <= 666                                                       # 时刻是牌子消失的那一下
    assert kills(tracker, [({}, gone)] * 20, start=12.0, clock=655) == []


def pit_kill(marks_before, marks_after, lead=0.0):
    """男爵的牌子“存活”10 秒后消失；时间轴最右端的读数在消失前 lead 秒从 before 变成 after。"""
    tracker = Tracker()
    alive, gone = {"upper": ("baron", True), "lower": None}, {"upper": None, "lower": None}
    out = []
    for i in range(50 + CHIP_GONE_CONFIRM + 2):
        now = i * 0.2
        marks = marks_after if now >= 10.0 - lead else marks_before
        out += tracker.update(objective_reading(700 + int(now), None, alive if i < 50 else gone, marks), now)
    return [(command["what"], command.get("side")) for command in collect(out, "game.objective")]


def test_pit_kill_gets_its_side_from_the_timeline():
    nothing, blue, red = {"blue": 0, "red": 0}, {"blue": 340, "red": 0}, {"blue": 0, "red": 310}
    assert pit_kill(nothing, blue) == [("baron", "blue")]
    assert pit_kill(nothing, red, lead=1.8) == [("baron", "red")]           # 图标比牌子消失早一两秒出现
    # 蓝方十几秒前刚推了一座塔，图标还在最右端附近：看的是“多出来”的那一个
    assert pit_kill({"blue": 280, "red": 0}, {"blue": 280, "red": 310}) == [("baron", "red")]
    assert pit_kill({"blue": 280, "red": 0}, {"blue": 520, "red": 0}) == [("baron", "blue")]
    # 说不准的时候不猜：两行同时多了图标、哪一行都没多、时间轴读不到（回放、面板收起）
    assert pit_kill(nothing, {"blue": 300, "red": 300}) == [("baron", None)]
    assert pit_kill(nothing, nothing) == [("baron", None)]
    assert pit_kill(None, None) == [("baron", None)]
    assert pit_kill(nothing, {"blue": 60, "red": 0}) == [("baron", None)]   # 多了几条击杀竖线的量，不够一个图标


def test_an_older_icon_drifting_nearby_does_not_hide_the_new_one():
    """实跑时漏判过：蓝方十几秒前的图标还在最右端附近，读数从 263 慢慢涨到 354；红方打掉男爵，图标一下子冒出来。"""
    tracker = Tracker()
    alive, gone = {"upper": ("baron", True), "lower": None}, {"upper": None, "lower": None}
    out = []
    for i in range(60 + CHIP_GONE_CONFIRM + 2):
        now = i * 0.2
        marks = {"blue": 263 + int(i * 1.5), "red": 380 if i >= 57 else 0}
        out += tracker.update(objective_reading(1060 + int(now), None, alive if i < 60 else gone, marks), now)
    assert [(c["what"], c.get("side")) for c in collect(out, "game.objective")] == [("baron", "red")]


def test_which_monster_is_in_the_pit():
    def killed(slot, icon, clock):
        tracker = Tracker()
        states = [{slot: (icon, True)}] * 5 + [{slot: None}] * CHIP_GONE_CONFIRM
        return [command["what"] for command in kills(tracker, [({}, chips) for chips in states], clock=clock)]
    assert killed("upper", "herald", 430) == ["herald"]         # 看图标：粉色是先锋，紫色是男爵
    assert killed("upper", "herald", 700) == ["herald"]
    assert killed("upper", "other", 400) == ["herald"]          # 图标认不出时看时间：男爵刷新（11:00）之前是先锋
    assert killed("upper", "other", 700) == ["baron"]
    assert killed("upper", "baron", 700) == ["baron"]
    assert killed("lower", "other", 1200) == ["elder"]
    assert killed("lower", "ocean", 500) == []                  # 元素龙归顶部条的图标管
    assert killed("lower", "other", 1000) == []                 # 远古龙刷新（19:00）之前，下面那个坑里不会有别的


def test_seeking_the_replay_is_not_a_kill():
    tracker = Tracker()
    alive = {"upper": ("baron", True), "lower": None}
    out = []
    for i in range(10):
        out += tracker.update(objective_reading(980 + i // 5, {"blue": ["ocean"], "red": []}, alive), i * 0.2)
    # 回放被拖回 03:47：牌子没了、龙图标也没了，时钟一路在跳
    for i in range(30):
        out += tracker.update(objective_reading(227 + i // 5, {"blue": [], "red": []}, {"upper": None, "lower": None}), 2.0 + i * 0.2)
    assert [(c["what"], c.get("side")) for c in collect(out, "game.objective")] == [("dragon", "blue")]
    assert tracker.dragons == {"blue": [], "red": []}


def test_reconcile_puts_back_dragons_but_not_chip_kills(store):
    tracker = Tracker()
    one = {"blue": ["ocean"], "red": []}
    alive, gone = {"upper": ("baron", True), "lower": None}, {"upper": None, "lower": None}
    for command in kills(tracker, [(one, alive)] * 6 + [(one, gone)] * CHIP_GONE_CONFIRM, clock=700):
        store.apply(command, 0)
    assert [(event["what"], event["side"]) for event in store.game["events"]] == [("dragon", "blue"), ("baron", None)]
    store.apply({"type": "game.new"}, 1000)
    for command in tracker.reconcile(store.snapshot(1000), 5.0):
        store.apply(command, 1000)
    assert [(event["what"], event["side"], event["element"]) for event in store.game["events"]] == [("dragon", "blue", "ocean")]
    store.apply({"type": "game.remove_objective"}, 1000)
    store.apply({"type": "game.lock", "path": "objectives", "on": True}, 1000)
    assert not collect(tracker.reconcile(store.snapshot(1000), 5.0), "game.objective")      # 导播说了野怪由他管


def clock_commands(tracker, samples):
    """samples: [(时间, 读到的游戏秒)] → 发出的对表命令 [(时间, 秒, 是否在走)]。"""
    out = []
    for now, observed in samples:
        for command in collect(tracker.update(reading(clock=observed), now), "game.clock"):
            assert command["action"] == "sync" and command["source"] == "vision"
            out.append((now, command["seconds"], command["running"]))
    return out


def ticking(start_time, start_clock, seconds, phase=0.1, step=0.2):
    """每 step 秒读一次；游戏时钟在 start_time + phase 之后每整秒跳一下。"""
    count = int(seconds / step)
    return [(round(start_time + i * step, 3), start_clock + int((i * step - phase) // 1 + 1)) for i in range(count)]


def test_clock_syncs_once_then_free_runs():
    tracker = Tracker()
    commands = clock_commands(tracker, ticking(0.0, 450, 120))
    assert len(commands) <= 2                           # 第一次粗对，下一次跳秒时精对，之后两分钟不再打扰
    now, seconds, running = commands[-1]
    assert running and abs((seconds - now) - (450 + 1 - 0.1)) <= 0.25      # 状态中心的时钟和画面差不到四分之一秒


def test_clock_ignores_a_single_misread():
    tracker = Tracker()
    samples = ticking(0.0, 780, 10)
    clock_commands(tracker, samples[:20])
    samples[25] = (samples[25][0], 1081)                # 13:0x 被读成 18:01
    assert clock_commands(tracker, samples[20:]) == []


def test_clock_follows_a_seek():
    tracker = Tracker()
    clock_commands(tracker, ticking(0.0, 980, 5))
    commands = clock_commands(tracker, ticking(5.0, 243, 5))        # 回放被拖回 04:03
    assert commands and commands[0][2] is True and 243 <= commands[0][1] <= 245
    assert len(commands) <= 2


def test_clock_ignores_a_short_burst_of_misreads():
    """实战里背景太亮时，时钟连着一两秒被读成 22:22，状态中心跟着对了表。一大步跳变现在要持续 1 秒才认。"""
    tracker = Tracker()
    samples = ticking(0.0, 300, 10)
    clock_commands(tracker, samples[:20])
    for k in range(25, 29):                             # 连续 4 次（0.8 秒）都读成 22:22
        samples[k] = (samples[k][0], 1342)
    assert clock_commands(tracker, samples[20:]) == []


def test_bright_background_does_not_give_a_clock(reader):
    frame = frame_from("hud-205")
    (y0, y1), (x0, x1) = reader.layout["clock"]["rows"], reader.layout["clock"]["x"]
    assert reader.read_clock(frame) == 10 * 60 + 55
    frame[y0:y1, x0:x1] = np.maximum(frame[y0:y1, x0:x1], 200)          # 时钟后面是很亮的地面
    assert reader.read_clock(frame) is None


def test_clock_pause_and_resume():
    tracker = Tracker()
    clock_commands(tracker, ticking(0.0, 300, 5))
    frozen = tracker.clock_seen[0]
    paused = clock_commands(tracker, [(5.0 + i * 0.2, frozen) for i in range(15)])
    assert paused == [(pytest.approx(paused[0][0]), frozen, False)]             # 只发一次“暂停”
    resumed = clock_commands(tracker, [(8.2, frozen + 1), (8.4, frozen + 1)])
    assert resumed and resumed[0][2] is True and abs(resumed[0][1] - (frozen + 1)) <= 0.2


def test_heartbeat():
    tracker = Tracker()
    status = []
    for i in range(30):
        status += collect(tracker.update(reading(clock=100, panel=True), i * 0.2), "vision.status")
    assert 3 <= len(status) <= 5                        # 6 秒里大约每 2 秒一次
    assert status[-1] == {"type": "vision.status", "panel": True, "clock": True}


def test_reconcile_refills_a_reset_server(store):
    """导播中途点了“进入局内”：状态中心被清空，识别要把没在变的数和时钟补回去；锁住的不碰。"""
    tracker = Tracker()
    frames = [dict(player_reading([5000, 5100, 5200, 5300, 5400], kda=(2, 1, 0)), clock=clock) for clock in (600, 600, 601, 601)]
    for index, frame in enumerate(frames):
        frame["values"].update({"blue.kills": 10, "blue.towers": 2})
        for command in tracker.update(frame, index * 0.2):
            store.apply(command, int(index * 200))
    assert store.game["teams"]["blue"]["kills"] == 10 and store.game["players"]["blue"][3]["gold"] == 5300

    store.apply({"type": "game.new"}, 1000)                                  # 清空
    store.apply({"type": "game.set", "path": "blue.towers", "value": 9, "lock": True}, 1000)     # 导播手动改并锁住
    assert store.game["teams"]["blue"]["kills"] == 0 and not store.game["clock"]["running"]

    commands = tracker.reconcile(store.snapshot(1000), 1.0)
    for command in commands:
        store.apply(command, 1000)
    game = store.game
    assert game["teams"]["blue"]["kills"] == 10
    assert game["teams"]["blue"]["towers"] == 9                              # 锁住的没被改回去
    assert [player["gold"] for player in game["players"]["blue"]] == [5000, 5100, 5200, 5300, 5400]
    assert game["players"]["blue"][0]["kills"] == 2
    assert game["clock"]["running"] and 601 <= game["clock"]["baseSec"] <= 602
    assert tracker.reconcile(store.snapshot(1000), 1.0) == []               # 对上了就不再发


def test_soul_element_from_the_first_dragon_chip():
    """第一条小龙（6:00）刷新前，下面那块牌子上的属性就是这一局的龙魂属性；刷新之后的牌子是下一条龙，不算。"""
    tracker = Tracker()
    early = {"upper": None, "lower": ("infernal", False)}
    seen = kills(tracker, [({}, early)] * 2, clock=280)
    assert seen == []
    out = []
    for i in range(4):
        out += collect(tracker.update(objective_reading(282 + i, {}, early), 0.4 + i * 0.2), "game.soul_element")
    assert out == [{"type": "game.soul_element", "source": "vision", "element": "infernal"}]
    late = Tracker()
    for i in range(6):
        assert not collect(late.update(objective_reading(700 + i, {}, {"upper": None, "lower": ("ocean", False)}), i * 0.2), "game.soul_element")


def test_readings_carry_the_game_time():
    """识别发出去的逐人数据带着读到它们时的游戏时间，连杀的间隔按它算（重放录像时墙上时间不准）。"""
    tracker = Tracker()
    commands = []
    for i, clock in enumerate((600, 600, 601, 601)):
        frame = dict(player_reading([5000] * 5, kda=(i // 2, 0, 0)), clock=clock)
        commands += collect(tracker.update(frame, i * 0.2), "game.set")
    assert commands and all(600 <= command["t"] <= 602 for command in commands if "t" in command)
    assert any("t" in command for command in commands)


# ---- 多杀播报 ----

def announce_frame(key: str) -> np.ndarray:
    """实战录像里裁下来的播报那一块（tests/fixtures/announce.npz），贴回整帧。"""
    canvas = np.zeros((1264, 2800, 3), np.uint8)
    with np.load(FIXTURES / "announce.npz") as data:
        canvas[195:290, 1150:1650] = data[key]
    return canvas


@pytest.mark.parametrize("key, side, count", [
    ("red2", "red", 2), ("red3", "red", 3), ("red4", "red", 4),
    ("blue2", "blue", 2), ("blue3", "blue", 3), ("blue4", "blue", 4), ("blueskin2", "blue", 2), ("blue2faint", "blue", 2)])
def test_reads_multikill_announcements(reader, key, side, count):
    """红方粉白字、蓝方浅青字；蓝方有一种亮蓝紫底板的样式（皮肤特效）也认得出。
    blue4 是 10-18 测试赛 15:11 漏掉的那条（蓝方“四”的模板就是拿它做的）；blue2faint 是 10-15 录像 902.6 秒的一帧，
    “二”的两道细横被视频压缩抹淡，严格门槛下整个字不见了，要靠数字那一块的宽门槛。"""
    announce = reader.announcer.read(announce_frame(key))
    assert announce is not None and (announce["side"], announce["count"]) == (side, count)


@pytest.mark.parametrize("key", ["kill", "godlike"])
def test_other_announcements_are_not_multikills(reader, key):
    """“红方击败了敌人”“如有神助!”这些不是多杀。"""
    assert reader.announcer.read(announce_frame(key)) is None
    assert reader.announcer.read(np.zeros((1264, 2800, 3), np.uint8)) is None


def multikill_run(tracker, steps, start=0.0):
    """steps: 每 0.2 秒一份 (announce, {人头})。返回发出的 game.multikill。"""
    out = []
    for i, (announce, kills) in enumerate(steps):
        values = {f"red.{index}.kda": (count, 0, 0) for index, count in kills.items()}
        values.update({f"red.{index}.gold": 5000 for index in kills})
        reading = {"clock": 600 + int((start + i * 0.2)), "panel": True, "values": values}
        if announce:
            reading["announce"] = {"side": announce[0], "count": announce[1], "score": 0.9}
        out += collect(tracker.update(reading, start + i * 0.2), "game.multikill")
    return out


def test_multikill_banner_is_reported_once_with_its_owner():
    tracker = Tracker()
    quiet = {0: 1, 1: 3, 2: 0, 3: 0, 4: 0}
    multikill_run(tracker, [(None, quiet)] * 5)
    after = {**quiet, 1: 5}                                       # 打野一下子拿了两个人头
    steps = [(None, after)] * 2 + [(("red", 2), after)] * 6 + [(None, after)] * 4
    seen = multikill_run(tracker, steps, start=1.0)
    assert [(c["side"], c["index"], c["count"]) for c in seen] == [("red", 1, 2)]
    # 紧接着三连破：同一条播报换了字，再报一次
    after3 = {**after, 1: 6}
    seen = multikill_run(tracker, [(("red", 3), after3)] * 6 + [(None, after3)] * 4, start=4.0)
    assert [(c["index"], c["count"]) for c in seen] == [(1, 3)]


def test_multikill_waits_for_the_kill_then_gives_up():
    tracker = Tracker()
    kills = {index: 0 for index in range(5)}
    multikill_run(tracker, [(None, kills)] * 5)
    # 播报先到，人头晚一点才读到
    late = {**kills, 3: 2}
    steps = [(("red", 2), kills)] * 4 + [(("red", 2), late)] * 4 + [(None, late)] * 5
    assert [(c["index"], c["count"]) for c in multikill_run(tracker, steps, start=1.0)] == [(3, 2)]
    # 怎么也等不到人头：不知道是谁，照报
    seen = multikill_run(tracker, [(("red", 2), late)] * 4 + [(None, late)] * 20, start=10.0)
    assert [(c["index"], c["count"]) for c in seen] == [(None, 2)]


def test_unreadable_count_after_a_quadra_is_a_penta():
    tracker = Tracker()
    kills = {index: 0 for index in range(5)}
    assert [c["count"] for c in multikill_run(tracker, [(("red", 4), kills)] * 4 + [(None, kills)] * 15)] == [4]
    seen = multikill_run(tracker, [(("red", None), kills)] * 4 + [(None, kills)] * 20, start=5.0)
    assert [c["count"] for c in seen] == [5]


def test_unreadable_count_follows_the_last_one():
    """10-18 测试赛 15:11：蓝方三连破 6 秒后一条“蓝方四连破”，当时还没有蓝方“四”的模板，数字认不出，漏报了。
    现在认不出数字时按同一方上一条加一；隔得太久就不猜。"""
    tracker = Tracker()
    kills = {index: 0 for index in range(5)}
    assert [c["count"] for c in multikill_run(tracker, [(("blue", 3), kills)] * 4 + [(None, kills)] * 15)] == [3]
    seen = multikill_run(tracker, [(("blue", None), kills)] * 6 + [(None, kills)] * 20, start=6.0)
    assert [c["count"] for c in seen] == [4]
    late = multikill_run(Tracker(), [(("blue", 2), kills)] * 4 + [(None, kills)] * 15)
    assert [c["count"] for c in late] == [2]
    tracker = Tracker()
    multikill_run(tracker, [(("blue", 2), kills)] * 4 + [(None, kills)] * 15)
    assert multikill_run(tracker, [(("blue", None), kills)] * 6 + [(None, kills)] * 20, start=30.0) == []


def test_multikill_with_the_panel_folded_is_reported_at_once():
    """团战时原生面板收起了，读不到逐人人头：不等，马上报“不知道是谁”。"""
    tracker = Tracker()
    out = []
    for i in range(4):
        reading = {"clock": 600, "panel": False, "values": {}, "announce": {"side": "blue", "count": 2, "score": 0.9}}
        out += collect(tracker.update(reading, i * 0.2), "game.multikill")
    assert [(c["side"], c["index"], c["count"]) for c in out] == [("blue", None, 2)]
