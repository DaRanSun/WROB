"""把一小块画面读成字符串。只用 numpy。

两步：
  1. 墨迹图：把 BGR 小块变成 0–1 的“这个像素是不是字”，不同区域用不同的规则（见 INKS）。
  2. 解码：用字形模板在墨迹图上滑动匹配，动态规划找出得分最高的字符序列（GlyphSet.decode）；
     时钟这种每个数字占固定格子的，直接逐格分类（GlyphSet.classify）。

为什么不先切字符再识别：底部面板的数字是比例字体，相邻数字的边缘经常粘在一起，按列切不开。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

TEMPLATES = Path(__file__).resolve().parent / "templates"


# ---- 墨迹图 ----

def ink_bright(roi: np.ndarray) -> np.ndarray:
    """纯色底上的亮字（底部面板的经济、KDA）。按这一小块自己的底色和最亮处归一化，所以暗一点的字也能读。"""
    value = roi.max(axis=2).astype(np.float32)
    background = np.median(value)
    peak = np.percentile(value, 99.5)
    if peak - background < 25:                      # 整块都是底色：没有字
        return np.zeros_like(value)
    return np.clip((value - background) / (peak - background), 0, 1)


def ink_blue(roi: np.ndarray) -> np.ndarray:
    """顶部条蓝方的字：蓝色分量远高于红色。"""
    b, r = roi[..., 0].astype(np.float32), roi[..., 2].astype(np.float32)
    return np.clip((b - r - 60) / 110, 0, 1)


def ink_red(roi: np.ndarray) -> np.ndarray:
    """顶部条红方的字：红色分量远高于蓝、绿。"""
    b, g, r = (roi[..., i].astype(np.float32) for i in range(3))
    return np.clip((r - (g + b) / 2 - 40) / 60, 0, 1)


def ink_grey(roi: np.ndarray) -> np.ndarray:
    """时钟：浅灰色的字压在游戏画面上。三个分量都高才算。"""
    return np.clip((roi.min(axis=2).astype(np.float32) - 105) / 65, 0, 1)


INKS = {"bright": ink_bright, "blue": ink_blue, "red": ink_red, "grey": ink_grey}


def ink_span(ink: np.ndarray, level: float = 0.3, min_pixels: int = 2) -> tuple[int, int] | None:
    """有字的列范围 [起, 止)。没有字返回 None。门槛放得低：“7”的横杠这类细笔画比较暗，也要算进去。"""
    columns = np.where((ink > level).sum(axis=0) >= min_pixels)[0]
    return (int(columns[0]), int(columns[-1]) + 1) if len(columns) else None


# ---- 字形模板 ----

DIGITS = frozenset("0123456789")
FLOOR = 0.7             # 一个字形的匹配分低于这个数就不认
EDGE = 2                # 一行字的首尾，模板边缘和墨迹边缘最多差几列
OVERLAP_COST = 0.3      # 相邻两个字形每重叠一列扣多少分
SKIP_COST = 0.05        # 两个字形之间每跳过一份墨迹扣多少分（空白的间距不扣）
MAX_SKIP = 10.0         # 间距里的墨迹超过这么多就不允许跳过。字的毛边加起来有 3–8；最轻的字（小数点）是 13，斜杠 26
SOLID = 0.55            # 墨迹超过这个值算实心笔画（毛边都在这以下）
MAX_SOLID = 2           # 间距里最多容许几个实心像素。实跑时出过把“4474”读成“4174”：跳过了“4”的左半边，把那一竖认成“1”
NARROW = 8              # 不超过这么宽的字形（1、/、.、:）要求更高的匹配分：
NARROW_FLOOR = 0.82     # 它们几乎只有一笔，别的字里随便一竖都能和它们对上七成


class GlyphSet:
    """一种字号的全部字形。每个模板是 行高 × 字宽 的墨迹图，行方向和所在区域的上下边对齐。

    键是字符本身；同一个字符有多个变体时写成 "3"、"3#1"、"3#2"（蓝字和红字、亮字和暗字的笔画粗细略有差别）。
    gap 是相邻两个字的墨迹之间允许的间距范围（像素，可以为负 = 粘连）。
    """

    def __init__(self, glyphs: dict[str, np.ndarray], gap: tuple[int, int] = (-2, 4)):
        self.glyphs = {label: glyph.astype(np.float32) for label, glyph in glyphs.items()}
        self.gap = gap
        self.height = next(iter(self.glyphs.values())).shape[0]
        # 去均值后的模板和它的模长，算相关系数时反复用
        self._centered = {label: glyph - glyph.mean() for label, glyph in self.glyphs.items()}
        self._norms = {label: float(np.sqrt((glyph * glyph).sum())) for label, glyph in self._centered.items()}

    def _floor(self, label: str, floor: float) -> float:
        return max(floor, NARROW_FLOOR) if self.glyphs[label].shape[1] <= NARROW else floor

    @property
    def characters(self) -> set[str]:
        return {label[0] for label in self.glyphs}

    @classmethod
    def load(cls, name: str, gap: tuple[int, int] = (-2, 4)) -> "GlyphSet":
        path = TEMPLATES / f"{name}.npz"
        if not path.is_file():
            raise FileNotFoundError(f"缺少字形模板 {path.name}：先用 tools/build_templates.py 生成")
        with np.load(path) as data:
            return cls({label: data[label] for label in data.files}, gap)

    def _scores(self, ink: np.ndarray) -> dict[str, np.ndarray]:
        """每个字形放在每个 x 位置的匹配分（去均值的归一化相关系数）。scores[label][x] 对应墨迹图第 x 列起的窗口。

        窗口都是整行高，所以窗口内的和、平方和可以用按列的前缀和得到；窗口与模板的内积先算
        “每一列对模板每一列”的乘积矩阵，再沿对角线相加。比逐个窗口相乘快一个数量级。
        """
        ink = ink.astype(np.float32)
        total = ink.shape[1]
        sums = np.concatenate(([0.0], np.cumsum(ink.sum(axis=0, dtype=np.float64))))
        squares = np.concatenate(([0.0], np.cumsum((ink * ink).sum(axis=0, dtype=np.float64))))
        scores = {}
        for label, glyph in self._centered.items():
            width = glyph.shape[1]
            count = total - width + 1
            if count <= 0:
                continue
            products = ink.T @ glyph                                    # 总列数 × 模板列数
            cross = sum(products[j:j + count, j] for j in range(width))
            window_sum = sums[width:] - sums[:-width]
            variance = squares[width:] - squares[:-width] - window_sum * window_sum / (self.height * width)
            scores[label] = (cross / (np.sqrt(np.maximum(variance, 0)) * self._norms[label] + 1e-6)).astype(np.float32)
        return scores

    def decode(self, ink: np.ndarray, floor: float = FLOOR) -> tuple[str, float] | None:
        """把一行字读出来。返回 (字符串, 最差那个字的匹配分)；读不出返回 None。

        从第一列墨迹走到最后一列墨迹，每一步放一个字形，要求首尾相接（允许 gap 范围内的间距）。
        每个字的得分是“匹配分超出 floor 的部分 × 字宽”，取总分最高的走法。这样多塞一个勉强对得上的字
        不划算，宽字形也不会仅仅因为宽就压过真正对得上的窄字形（比如把“1”读成“4”）。
        另外两种情况要扣分：两个字形互相重叠（否则“0”的左边一竖会被再读成一个“1”），
        以及两个字形之间跳过了有墨迹的列（否则可以整个跳过一个字）。
        """
        if ink.shape[0] != self.height:
            raise ValueError(f"墨迹图高度 {ink.shape[0]} 和模板高度 {self.height} 不一致")
        span = ink_span(ink)
        if span is None:
            return None
        left = max(0, span[0] - EDGE - 1)                               # 只在有字的那一段附近算
        ink = ink[:, left:span[1] + EDGE + 1]
        start, end = span[0] - left, span[1] - left
        scores = self._scores(ink)
        mass = np.concatenate(([0.0], np.cumsum(ink.sum(axis=0))))      # mass[b] - mass[a] = a..b 列的墨迹总量
        solid = np.concatenate(([0], np.cumsum((ink > SOLID).sum(axis=0))))     # 同上，但只数实心的笔画像素
        # open_[x] = “下一个字形的左边缘放在 x 列”这个状态下，目前最好的 (加权总分, 最差分, 字符串)。
        # 模板是贴着墨迹裁的，所以第一个字的左边缘就在墨迹起点附近（EDGE 列以内）。
        open_: dict[int, tuple[float, float, str]] = {
            x: (0.0, 1.0, "") for x in range(start - EDGE, start + EDGE + 1) if x >= 0}
        finals: list[tuple[float, float, str]] = []
        for x in range(max(0, start - EDGE), end):      # 每一步都向右推进，所以按列从左到右处理一遍就够
            if x not in open_:
                continue
            total, worst, text = open_[x]
            for label, row in scores.items():
                if x >= len(row) or row[x] < self._floor(label, floor):
                    continue
                width = self.glyphs[label].shape[1]
                state = (total + (float(row[x]) - floor) * width, min(worst, float(row[x])), text + label[0])
                right = x + width
                if abs(right - end) <= EDGE:            # 右边缘正好落在墨迹终点：一种完整的读法
                    finals.append(state)
                    continue
                for gap in range(self.gap[0], self.gap[1] + 1):
                    target = right + gap
                    if not x < target < end:
                        continue
                    skipped = float(mass[target] - mass[right]) if gap > 0 else 0.0
                    # 间距里只能有字的毛边。有一整个字、或者有实心的笔画（半个“4”），都不许跳过：宁可读不出，也不能读错
                    if skipped > MAX_SKIP or (gap > 0 and solid[target] - solid[right] > MAX_SOLID):
                        break
                    cost = OVERLAP_COST * -gap if gap < 0 else SKIP_COST * skipped
                    moved = (state[0] - cost, state[1], state[2])
                    if target not in open_ or moved[0] > open_[target][0]:
                        open_[target] = moved
        if not finals:
            return None
        _, worst, text = max(finals)
        return text, worst

    def find(self, ink: np.ndarray, pattern: list[frozenset[str]], floor: float = FLOOR) -> tuple[str, float] | None:
        """在一块有杂乱背景的区域里找一串固定格式的字（时钟用）。

        pattern 是每一位允许的字符集合，比如 [数字, 数字, {":"}, 数字, 数字]。起点不限，
        评分方式和 decode 一样；任何一位低于 floor 就认为没找到。返回 (字符串, 最差分)。
        """
        scores = self._scores(ink)
        width = ink.shape[1]
        # layer[x] = 已经放好前 k 个字、下一个字的左边缘在 x 时最好的 (总分, 最差分, 字符串)
        layer: dict[int, tuple[float, float, str]] = {x: (0.0, 1.0, "") for x in range(width)}
        finals: list[tuple[float, float, str]] = []
        for position, allowed in enumerate(pattern):
            following: dict[int, tuple[float, float, str]] = {}
            for x, (total, worst, text) in layer.items():
                for label, row in scores.items():
                    if label[0] not in allowed or x >= len(row) or row[x] < self._floor(label, floor):
                        continue
                    span = self.glyphs[label].shape[1]
                    state = (total + (float(row[x]) - floor) * span, min(worst, float(row[x])), text + label[0])
                    right = x + span
                    if position == len(pattern) - 1:
                        finals.append(state)
                        continue
                    for gap in range(self.gap[0], self.gap[1] + 1):
                        target = right + gap
                        if x < target < width and (target not in following or state[0] > following[target][0]):
                            following[target] = state
            layer = following
        if not finals:
            return None
        _, worst, text = max(finals)
        return text, worst

    def save(self, name: str) -> Path:
        TEMPLATES.mkdir(exist_ok=True)
        path = TEMPLATES / f"{name}.npz"
        np.savez_compressed(path, **self.glyphs)
        return path
