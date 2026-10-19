"""结算页“统计 · 数据对比”：比赛结束后读双方十个人的输出、承伤、治疗护盾、参团率、经济。

    reader = StatsReader()
    reading = reader.read(frame)    # 不是这一页 → None；是 → {"left": [行×5], "right": [行×5], "complete": bool}
    commands = StatsWatcher().update(reading, 时间戳秒)       # → [{"type": "postgame.stats", …}]

一行 = {"damage", "damageShare", "taken", "healing", "healingShare", "share", "gold", "goldShare"}（state/postgame.py），
读不出的项是 None。left / right 是画面的左右两半，是哪一方由状态中心对着局内经济来定。

complete：十行都读全了，而且每一方三组“数值 + 占全队百分比”互相对得上（百分比 ≈ 数值 / 五人之和，五人的百分比加起来
≈ 100）。读错一位数几乎不可能还对得上，所以只有 complete 的读数才往外报，而且要连续三次读得一模一样
（页面刚打开时数字从 0 往上跳，动画中途的读数百分比对不上，正好被挡掉）。
"""
from __future__ import annotations

import numpy as np

from .layout import load_layout, to_reference
from .text import TEMPLATES, GlyphSet, ink_bright

HALVES = ("left", "right")
COLUMNS = ("damage", "taken", "healing", "share", "gold")
SHARED = {"damage": "damageShare", "healing": "healingShare", "gold": "goldShare"}     # 数值后面跟着占全队的百分比
KEYS = ("damage", "damageShare", "taken", "healing", "healingShare", "share", "gold", "goldShare")
INK_TOP = 0.4           # 一行里有两个以上像素超过这个墨迹值，算碰到了字的上沿
SPLIT_INK = 0.35        # 一列里有像素超过这个值就算有字（切词、切字用）
GLYPH_FLOOR = 0.75      # 一个字和最像的模板的相关系数低于这个数就不认
GLYPH_MARGIN = 0.04     # 最像的和第二像的（不同的字）至少差这么多，不然算认不准
WIDTH_SLACK = 3         # 切出来的字和模板宽度最多差几列
SHARE_SLACK = 1.5       # 百分比和“数值 / 五人之和”最多差这么多（游戏里四舍五入到整数）
SHARE_SUM = (97, 103)   # 一方五个人的百分比加起来该在这个范围里
RESEND_SECONDS = 3.0    # 报过的数据隔这么久再报一次（状态中心那边可能刚被导播重开，或者刚结束本局）
STABLE_READS = 3        # 连续这么多次读得一模一样才报。这一页刚打开时数字有约 1 秒从 0 往上跳的动画，
                        # 动画最后一两帧的读数已经自洽、但还不是最终值（录像里实测），它每一帧都在变


def header_strip(frame: np.ndarray, spec: dict) -> np.ndarray:
    """表头那一条，灰度、隔一个像素取一个。"""
    (y0, y1), (x0, x1) = spec["rows"], spec["x"]
    return frame[y0:y1:2, x0:x1:2].astype(np.float32).mean(axis=2)


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def field_ink(frame: np.ndarray, layout: dict, half: str, index: int, column: str) -> np.ndarray | None:
    """一格的墨迹图：从字的上沿上面一行起，高度和字形模板一样。整格没有字返回 None。"""
    spec = layout["stats"]
    y0 = int(round(spec["band"][0] + spec["pitch"] * index))
    y1 = int(round(spec["band"][1] + spec["pitch"] * index))
    x0, x1 = spec["columns"][column][half]
    ink = ink_bright(frame[y0:y1, x0:x1])
    rows = np.flatnonzero((ink > INK_TOP).sum(axis=1) >= 2)
    if not len(rows):
        return None
    top = max(0, int(rows[0]) - 1)
    strip = ink[top:top + spec["height"]]
    if strip.shape[0] < spec["height"]:
        strip = np.pad(strip, ((0, spec["height"] - strip.shape[0]), (0, 0)))
    return strip


def split_words(ink: np.ndarray, gap: int) -> list[np.ndarray]:
    """按宽空白把一格切成几个词（数值、百分比），每个词左右各留 2 列。"""
    columns = np.flatnonzero((ink > SPLIT_INK).any(axis=0))
    if not len(columns):
        return []
    breaks = np.flatnonzero(np.diff(columns) > gap)
    starts = np.concatenate(([columns[0]], columns[breaks + 1]))
    ends = np.concatenate((columns[breaks], [columns[-1]])) + 1
    return [ink[:, max(0, a - 2):b + 2] for a, b in zip(starts, ends)]


def characters(word: np.ndarray) -> list[np.ndarray]:
    """一个词按空列切成单个字。这一页字号大，字和字之间总有空列（两张截图里 400 多个字没有一处粘连）。"""
    on = (word > SPLIT_INK).any(axis=0)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8), [0]))))
    return [word[:, a:b] for a, b in zip(edges[::2], edges[1::2])]


def glyph_score(piece: np.ndarray, glyph: np.ndarray) -> float:
    """切出来的一个字和一个模板的相关系数，左右错开几列取最好的。"""
    width = max(piece.shape[1], glyph.shape[1]) + 2 * WIDTH_SLACK
    canvas = np.zeros((glyph.shape[0], width), np.float32)
    canvas[:, WIDTH_SLACK:WIDTH_SLACK + glyph.shape[1]] = glyph
    best = -1.0
    for shift in range(0, width - piece.shape[1] + 1):
        moved = np.zeros_like(canvas)
        moved[:, shift:shift + piece.shape[1]] = piece
        best = max(best, correlation(moved, canvas))
    return best


def number(text: str | None, percent: bool) -> int | None:
    if not text or text.endswith("%") != percent:
        return None
    digits = text[:-1] if percent else text
    return int(digits) if digits.isdigit() else None


def consistent(rows: list[dict]) -> bool:
    """一方五行都读全了，而且三组百分比和数值对得上。"""
    if any(row[key] is None for row in rows for key in KEYS):
        return False
    if any(row["share"] > 100 for row in rows):
        return False
    if not sum(row["damage"] for row in rows) or not sum(row["gold"] for row in rows):
        return False                        # 页面刚打开、数字还没开始跳的那一帧全是 0，也“对得上”
    for value, share in SHARED.items():
        total = sum(row[value] for row in rows)
        if total == 0:
            if any(row[share] for row in rows):
                return False
            continue
        if not SHARE_SUM[0] <= sum(row[share] for row in rows) <= SHARE_SUM[1]:
            return False
        if any(abs(row[share] - row[value] * 100 / total) > SHARE_SLACK for row in rows):
            return False
    return True


class StatsReader:
    def __init__(self, layout: dict | None = None):
        self.layout = layout or load_layout()
        spec = self.layout["stats"]
        self.glyphs = GlyphSet.load(spec["glyphs"], tuple(self.layout["glyph_gaps"][spec["glyphs"]]))
        path = TEMPLATES / "stats-header.npz"
        if not path.is_file():
            raise FileNotFoundError(f"缺少 {path.name}：先用 tools/build_stats.py 生成")
        with np.load(path) as data:
            self.header = data["header"]

    def header_score(self, frame: np.ndarray) -> float:
        """这一帧像不像“数据对比”页：表头和模板的相关系数。"""
        return correlation(header_strip(frame, self.layout["stats"]["header"]), self.header)

    def read_field(self, frame: np.ndarray, half: str, index: int, column: str) -> list[str | None]:
        """一格里的几个词（读不出的词是 None）。"""
        ink = field_ink(frame, self.layout, half, index, column)
        if ink is None:
            return []
        return [self.read_word(word) for word in split_words(ink, self.layout["stats"]["word_gap"])]

    def classify(self, piece: np.ndarray) -> str | None:
        scores = {}
        for label, glyph in self.glyphs.glyphs.items():
            if abs(glyph.shape[1] - piece.shape[1]) <= WIDTH_SLACK:
                char = label[0]
                scores[char] = max(scores.get(char, -1.0), glyph_score(piece, glyph))
        ranked = sorted(scores.items(), key=lambda item: -item[1])
        if not ranked or ranked[0][1] < GLYPH_FLOOR:
            return None
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < GLYPH_MARGIN:
            return None
        return ranked[0][0]

    def read_word(self, word: np.ndarray) -> str | None:
        """先按空列切字、逐个认；切出来的一块比任何字都宽（画面糊了，两个字粘在一起）时，这一块用滑动解码。"""
        widest = max(glyph.shape[1] for glyph in self.glyphs.glyphs.values())
        text = ""
        for piece in characters(word):
            if piece.shape[1] > widest + WIDTH_SLACK:
                decoded = self.glyphs.decode(np.pad(piece, ((0, 0), (2, 2))))
                if decoded is None:
                    return None
                text += decoded[0]
                continue
            char = self.classify(piece)
            if char is None:
                return None
            text += char
        return text or None

    def read_row(self, frame: np.ndarray, half: str, index: int) -> dict:
        row = dict.fromkeys(KEYS)
        for column in COLUMNS:
            words = self.read_field(frame, half, index, column)
            if column in SHARED and len(words) == 2:
                row[column], row[SHARED[column]] = number(words[0], False), number(words[1], True)
            elif column == "taken" and len(words) == 1:
                row[column] = number(words[0], False)
            elif column == "share" and len(words) == 1:
                row[column] = number(words[0], True)
        return row

    def read(self, frame: np.ndarray) -> dict | None:
        frame = to_reference(frame, self.layout)
        spec = self.layout["stats"]
        if self.header_score(frame) < spec["header"]["match"]:
            return None
        reading = {half: [self.read_row(frame, half, index) for index in range(spec["count"])] for half in HALVES}
        reading["complete"] = all(consistent(reading[half]) for half in HALVES)
        return reading


class StatsWatcher:
    """连续三次读到一模一样的完整数据才报；报过之后隔一会儿再报一次，直到状态中心收下（那边重复的会忽略）。"""

    def __init__(self):
        self.last = None
        self.count = 0
        self.sent = None
        self.sent_at = float("-inf")

    def update(self, reading: dict | None, now: float) -> list[dict]:
        if not reading or not reading["complete"]:
            self.last, self.count = None, 0
            return []
        key = repr((reading["left"], reading["right"]))
        self.count = self.count + 1 if key == self.last else 1
        self.last = key
        if self.count < STABLE_READS:
            return []
        if key == self.sent and now - self.sent_at < RESEND_SECONDS:
            return []
        self.sent, self.sent_at = key, now
        return [{"type": "postgame.stats", "source": "vision", "left": reading["left"], "right": reading["right"]}]
