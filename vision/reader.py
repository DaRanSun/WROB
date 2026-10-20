"""把一帧画面读成一组数值。不做任何时间上的判断（去抖、校验在 tracker.py）。

    Reader().read(frame) → {
        "clock": 325,                     游戏秒；读不出为 None
        "panel": True,                    底部面板是否展开
        "values": {"blue.kills": 8, "blue.gold": 21000, "blue.towers": 0,
                   "blue.0.gold": 3654, "blue.0.kda": (0, 1, 0),
                   "blue.0.items": ["铁板靴", None, "?", …],            6 格，由外到内，第 0 格是鞋子
                   "blue.0.respawn": 26, …},                             读不出的项不出现
        "scores": {"blue.kills": 0.93, …},                                每一项里最差那个字的匹配分
        "weak": ["red.4.level", …],                                       用备用读法读出的等级（tracker 不让它们往下改）
        "dragons": {"blue": ["ocean"], "red": []},                        原生顶部条上双方拿到的元素龙；看不清的一方不出现
        "chips": {"upper": ("baron", True), "lower": None},               两块野怪刷新牌子：(图标, 是否写着“存活”)，没有牌子为 None
        "timeline": {"blue": 0, "red": 310},                              实时 OB 时间轴最右端双方的图标像素数；读不到为 None
        "lights": {"blue.0.spell": [171.0, 48.2], "blue.0.item": [...6]},  每个技能格 / 装备格现在有多亮（冷却中的是暗的）
        "announce": {"side": "red", "count": 3, "score": 0.92},           游戏自己的多杀播报（“红方三连破”）；没有为 None
    }

values 里每个选手还有 "<side>.<i>.level"（等级，1–15）和 "<side>.<i>.ult"（大招转好了多少，0–1；1 = 可以放了）。
还没学大招时那个圆点是暗的，读出来也是 0，要结合等级判断。

values 里每个选手还有 "<side>.<i>.spells"（两个召唤师技能）和 "<side>.<i>.rune"（基石符文），和装备一样：
名字 = 认出来了，"?" 或 None = 这一帧认不出（冷却中被压暗、图标库里没有）。

dragons 和 chips 只在时钟读得出（确实是对局画面）时才有。

顶部条的 blue.gold / red.gold 只精确到 0.1K；blue.<i>.gold 是精确值，i = 0–4 对应上野中下辅。
装备格：名字 = 认出来了，None = 空格子，"?" = 有东西但认不准。
respawn：复活倒计时的秒数，0 = 活着；头像是灰的但数字读不出时这一项不出现。
"""
from __future__ import annotations

import re

import numpy as np

from state import SIDES

from . import layout as layout_mod
from . import objectives
from .announce import Announcer
from .items import ItemMatcher
from .text import DIGITS, INKS, GlyphSet

CLOCK_PATTERN = [DIGITS, DIGITS, frozenset(":"), DIGITS, DIGITS]
CLOCK_MAX_INK = 0.4     # 时钟那一块的墨迹覆盖率：正常读数 0.1–0.33，背景太亮时 0.7–0.96
TEAM_GOLD = re.compile(r"(\d{1,3})\.(\d)K")
KDA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{1,2})")
# 一小块画面和上次读它时相比，明显变了的像素（任一分量差超过 PIXEL_DELTA）不到 PIXEL_COUNT 个，
# 就直接沿用上次的结果。不能用“平均差多少”来判断：只变了一位数字时，平均下来和视频压缩的噪声差不多。
PIXEL_DELTA = 48
PIXEL_COUNT = 4


def light(roi: np.ndarray) -> float:
    """一个图标格子有多亮（去掉边上几个像素）。冷却中的图标是压暗的。"""
    return round(float(roi[4:-4, 4:-4].max(axis=2).mean()), 1)


def parse_level(text: str):
    return int(text) if text.isdigit() and 1 <= int(text) <= 15 else None


def parse_digit(text: str):
    return int(text) if text.isdigit() and len(text) == 1 else None


LEVEL_UNITS_FROM = 14       # 等级那一小块里，个位数字在这一列往右（数字是靠右对齐的）


LEVEL_TOPHAT = 5            # 备用读法的窗口：比数字的笔画（3–4 像素）宽，比头像上的大块亮区窄
LEVEL_TOPHAT_INK = (25, 60)  # 比周围亮出这么多开始算字，亮出后一个数算满墨


def level_ink(roi: np.ndarray) -> np.ndarray:
    """等级数字的墨迹。数字压在头像上，旁边常有头像自己的亮斑：把孤零零的一列（“1”的小尖角断开的那一点、
    头像上的噪点）擦掉，免得解码时因为“有墨迹没认出来”而放弃。"""
    return _drop_lonely(INKS["grey"](roi).copy())


def _grey_filter(image: np.ndarray, size: int, reduce) -> np.ndarray:
    pad = size // 2
    windows = np.lib.stride_tricks.sliding_window_view(np.pad(image, pad, mode="edge"), (size, size))
    return reduce(windows, axis=(2, 3))


def level_tophat_ink(roi: np.ndarray) -> np.ndarray:
    """备用读法：头像在数字那一角是浅色的（萨勒芬妮、剑姬、娑娜的粉白色），整块都和数字一样亮，level_ink 分不开。
    数字的笔画细、外面还有一圈暗描边，所以用“顶帽”：亮度减去它的开运算（先取窗口最小再取最大），大块的亮区被减掉，
    只留下比窗口细的亮笔画。代价是数字紧挨着一大块亮区时，挨着的那半边笔画也会被减掉（“8”剩下右半边像“3”），
    所以它读出的数只许让等级往上走（tracker 里看 reading["weak"]）。"""
    value = roi.min(axis=2).astype(np.float32)
    opened = _grey_filter(_grey_filter(value, LEVEL_TOPHAT, np.min), LEVEL_TOPHAT, np.max)
    low, high = LEVEL_TOPHAT_INK
    return _drop_lonely(np.clip((value - opened - low) / (high - low), 0, 1))


def _drop_lonely(ink: np.ndarray) -> np.ndarray:
    on = (ink > 0.3).sum(axis=0) >= 2
    padded = np.concatenate(([False], on, [False]))
    lonely = on & ~padded[:-2] & ~padded[2:]
    ink[:, lonely | ~on] *= 0.0
    return ink


def ult_progress(roi: np.ndarray) -> float:
    """大招圆点里绿色占了多少：转好了是整块绿，冷却中是一块慢慢变大的绿色扇形。"""
    size = roi.shape[0]
    yy, xx = np.mgrid[:size, :size]
    inside = np.hypot(yy - size // 2, xx - size // 2) <= size // 2
    b, g, r = (roi[..., index].astype(np.int16) for index in range(3))
    green = (g >= 120) & (g - r >= 50) & (g - b >= 50)
    return round(float(green[inside].mean()), 3)


def parse_count(text: str):
    return int(text) if text.isdigit() and len(text) <= 3 else None


def parse_gold(text: str):
    return int(text) if text.isdigit() and len(text) <= 5 else None


def parse_team_gold(text: str):
    match = TEAM_GOLD.fullmatch(text)
    return int(match.group(1)) * 1000 + int(match.group(2)) * 100 if match else None


def parse_kda(text: str):
    match = KDA.fullmatch(text)
    return tuple(int(part) for part in match.groups()) if match else None


class Reader:
    def __init__(self, layout: dict | None = None):
        self.layout = layout or layout_mod.load_layout()
        gaps = self.layout.get("glyph_gaps", {})
        names = {self.layout["clock"]["glyphs"], self.layout["panel"]["glyphs"], self.layout["cards"]["glyphs"],
                 self.layout["levels"]["glyphs"]}
        names |= {spec["glyphs"] for spec in self.layout["top"]["fields"].values()}
        self.sets = {name: GlyphSet.load(name, tuple(gaps.get(name, (-2, 4)))) for name in sorted(names)}
        self.items = ItemMatcher()
        self.spells, self.runes = ItemMatcher("spells"), ItemMatcher("runes")
        self.chips = objectives.Chips(self.layout)
        self.announcer = Announcer(self.layout)
        self._cache: dict[str, tuple] = {}
        self.weak: set[str] = set()             # 这一帧里用备用读法读出的等级

    def _decode(self, name: str, roi: np.ndarray, ink: str, glyphs: str, parse):
        """读一项。画面没变就沿用上次的结果：大多数格子在大多数时刻都没变。读不出的不记，下次重读。"""
        cached = self._cache.get(name)
        if cached is not None:
            delta = np.abs(roi.astype(np.int16) - cached[0]).max(axis=2)
            if np.count_nonzero(delta > PIXEL_DELTA) < PIXEL_COUNT:
                return cached[1]
        decoded = self.sets[glyphs].decode(INKS[ink](roi))
        value = parse(decoded[0]) if decoded else None
        if value is None:
            self._cache.pop(name, None)
            return None
        self._cache[name] = (roi.astype(np.int16), (value, decoded[1]))
        return self._cache[name][1]

    def read_clock(self, frame: np.ndarray) -> int | None:
        spec = self.layout["clock"]
        ink = INKS[spec["ink"]](layout_mod.clock_field(frame, self.layout))
        if ink.mean() > CLOCK_MAX_INK:          # 时钟后面的地面太亮，整块都成了“墨迹”：读出来的是瞎猜的（实测常是 22:22）
            return None
        found = self.sets[spec["glyphs"]].find(ink, CLOCK_PATTERN)
        if not found:
            return None
        minutes, seconds = int(found[0][:2]), int(found[0][3:])
        return minutes * 60 + seconds if seconds < 60 else None

    def _level_from(self, ink: np.ndarray) -> tuple[int, float] | None:
        """整块读；读不出来时（十位那边被头像的亮斑糊住了）只读个位，交给 tracker 结合之前的等级补上十位。"""
        glyphs = self.sets[self.layout["levels"]["glyphs"]]
        decoded = glyphs.decode(ink)
        value = parse_level(decoded[0]) if decoded else None
        if value is None:
            decoded = glyphs.decode(ink[:, LEVEL_UNITS_FROM:])
            value = parse_digit(decoded[0]) if decoded else None
        return None if value is None else (value, decoded[1])

    def read_level(self, name: str, roi: np.ndarray) -> tuple[int, float] | None:
        """头像右下角的等级。先按亮度读；读不出来再用备用读法（level_tophat_ink），那样读出的记进 self.weak。"""
        cached = self._cache.get(name)
        if cached is not None:
            delta = np.abs(roi.astype(np.int16) - cached[0]).max(axis=2)
            if np.count_nonzero(delta > PIXEL_DELTA) < PIXEL_COUNT:
                if cached[2]:
                    self.weak.add(name)
                return cached[1]
        result, weak = self._level_from(level_ink(roi)), False
        if result is None:
            result = self._level_from(level_tophat_ink(roi))
            weak = result is not None
        if result is None:
            self._cache.pop(name, None)
            return None
        if weak:
            self.weak.add(name)
        self._cache[name] = (roi.astype(np.int16), result, weak)
        return result

    def read_respawn(self, roi: np.ndarray) -> int | None:
        """一张选手卡片的复活倒计时。头像有颜色 = 活着（0）；灰的就读上面的红色数字，读不出返回 None。"""
        if not layout_mod.portrait_is_grey(roi):
            return 0
        spec = self.layout["cards"]
        decoded = self.sets[spec["glyphs"]].decode(INKS[spec["ink"]](roi))
        if decoded and decoded[0].isdigit() and len(decoded[0]) <= 2:
            return int(decoded[0])
        return None

    def read(self, frame: np.ndarray) -> dict:
        frame = layout_mod.to_reference(frame, self.layout)
        values, scores, lights = {}, {}, {}
        self.weak = set()

        def put(name, result):
            if result is not None:
                values[name], scores[name] = result

        for name, roi in layout_mod.top_fields(frame, self.layout).items():
            spec = self.layout["top"]["fields"][name]
            parse = parse_team_gold if name.endswith(".gold") else parse_count
            put(name, self._decode(name, roi, spec["ink"], spec["glyphs"], parse))

        panel = self.layout["panel"]
        visible = layout_mod.panel_visible(frame, self.layout)
        if visible:
            for name, roi in layout_mod.panel_fields(frame, self.layout).items():
                parse = parse_kda if name.endswith(".kda") else parse_gold
                put(name, self._decode(name, roi, panel["ink"], panel["glyphs"], parse))
            for name, cells in layout_mod.item_cells(frame, self.layout).items():
                values[f"{name}.items"] = [self.items.match(cell) for cell in cells]
                lights[f"{name}.item"] = [light(cell) for cell in cells]
        clock = self.read_clock(frame)
        result = {"clock": clock, "panel": visible, "values": values, "scores": scores}
        # 播报压在画面上方，背景亮的时候时钟会读不出，所以不看时钟读没读出来
        announce = self.announcer.read(frame)
        if announce is not None:
            result["announce"] = announce
        if clock is not None:                           # 时钟读得出，说明确实是对局画面，卡片、图标、牌子才有意义
            for name, roi in layout_mod.card_fields(frame, self.layout).items():
                seconds = self.read_respawn(roi)
                if seconds is not None:
                    values[name] = seconds
            for name, roi in layout_mod.level_fields(frame, self.layout).items():
                put(name, self.read_level(name, roi))
            if self.weak:
                result["weak"] = sorted(self.weak)
            for name, roi in layout_mod.ult_fields(frame, self.layout).items():
                values[name] = ult_progress(roi)
            cells = layout_mod.spell_cells(frame, self.layout)
            for side in SIDES:
                for index in range(self.layout["cards"]["count"]):
                    player = f"{side}.{index}"
                    pair = [cells[f"{player}.spell.{slot}"] for slot in range(2)]
                    values[f"{player}.spells"] = [self.spells.match(roi) for roi in pair]
                    values[f"{player}.rune"] = self.runes.match(cells[f"{player}.rune"])
                    lights[f"{player}.spell"] = [light(roi) for roi in pair]
            result["lights"] = lights
            result["dragons"] = objectives.read_dragons(frame, self.layout)
            result["chips"] = self.chips.read(frame)
            result["timeline"] = objectives.read_timeline(frame, self.layout) if visible else None
        return result
