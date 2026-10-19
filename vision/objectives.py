"""原生界面上和野怪有关的两处。只用 numpy。

  · 顶部条两端的元素龙图标：一方每拿下一条元素龙，推塔数旁边就多一个图标（海龙青色、山龙土黄色、火龙橙红色）。
    这是一直挂在那里的状态，所以“哪一方、什么属性、什么时候拿的”都能从它读出来。
  · 小地图右边的两块刷新牌子：上面一块是先锋 / 男爵那个坑，下面一块是小龙 / 远古龙那个坑。牌子上是一个图标
    （先锋粉色、男爵紫色、元素龙和顶部条同款），加上“存活”或者刷新倒计时。野怪被打掉的那一刻牌子消失，
    下次刷新前 1 分半再出现。所以“存活”的牌子不见了 = 被打掉了；但牌子不说是哪一方打的。
  · 底部面板上方的事件时间轴：红方的事件画在上面一行，蓝方在下面一行；推塔、野怪是亮色的图标，击杀是暗色的细竖线。
    实时 OB 里播放头一直在最右端，新事件的图标就出现在播放头那里——哪一行多出一个图标，就是哪一方打的。

图标都压在调暗了的游戏画面上，所以靠“够亮的像素是什么颜色”来认，不比形状。
"""
from __future__ import annotations

import numpy as np

from state import SIDES

from .text import TEMPLATES, ink_grey

ELEMENTS = ("infernal", "mountain", "ocean")
UNSURE = "?"            # 格子里有亮的东西，但不像图标（多半是背后的游戏画面太亮）
LIT = 140               # 图标的像素，最亮的那个分量至少这么亮
ICON_MIN, ICON_MAX = 0.15, 0.40     # 图标占格子面积的比例；实测 0.21–0.33
EMPTY_MAX = 0.04        # 亮像素不到这个比例就是空格子
RING = 3                # 图标四周应该是一圈暗的：格子最外面这么多像素里，亮的不能超过 RING_MAX
RING_MAX = 0.12
AGREE = 0.7             # 亮像素里至少这么多和整体是同一种颜色
TEXT_MIN = 0.02         # 牌子上有字时，文字区域的平均墨迹至少这么多
ALIVE_SCORE = 0.6       # 文字和“存活”模板的相关系数：实测“存活”0.82–0.99，倒计时不超过 0.16


def colours(pixels: np.ndarray) -> np.ndarray:
    """每个像素（B, G, R）像哪种图标：0 都不像，1 男爵（紫），2 海龙（青），3 山龙（土黄），4 火龙（橙红），5 先锋（粉）。"""
    b, g, r = (pixels[:, index].astype(np.int16) for index in range(3))
    kind = np.zeros(len(pixels), np.int8)
    warm = (r - g >= 25) & (g - b >= 15)
    kind[warm & (g * 10 >= r * 6)] = 3
    kind[warm & (g * 10 < r * 6)] = 4
    kind[(g - r >= 30) & (g >= b - 10)] = 2
    # 男爵和先锋都偏紫：男爵几乎没有绿（211, 6, 146），先锋是粉的，红和蓝一样多、绿也多一些（208, 83, 213）
    purple = (b > 140) & (r > 90) & (g * 10 < b * 6)
    kind[purple & (g * 5 < b)] = 1
    kind[purple & (g * 5 >= b) & (r > b - 50)] = 5
    return kind


KINDS = (None, "baron", "ocean", "mountain", "infernal", "herald")


def icon_kind(roi: np.ndarray) -> str | None:
    """一个图标格子里是什么：None 空的，UNSURE 说不准，"other" 是个图标但不是认识的颜色，否则是 KINDS 里的名字。"""
    lit = roi.max(axis=2) >= LIT
    share = lit.mean()
    if share < EMPTY_MAX:
        return None
    ring = np.ones(lit.shape, bool)
    ring[RING:-RING, RING:-RING] = False
    if not ICON_MIN <= share <= ICON_MAX or lit[ring].mean() > RING_MAX:
        return UNSURE
    kinds = colours(roi[lit])
    counts = np.bincount(kinds, minlength=len(KINDS))
    best = int(counts.argmax())
    if counts[best] < AGREE * len(kinds):
        return UNSURE
    return KINDS[best] or "other"


def read_dragons(frame: np.ndarray, layout: dict) -> dict[str, list[str]]:
    """{"blue": ["ocean", "mountain"], "red": []}：双方按拿下的先后排。某一方这一帧看不清就不出现在结果里。"""
    spec, size = layout["dragons"], layout["dragons"]["size"]
    out = {}
    for side in SIDES:
        kinds = [icon_kind(frame[spec["top"]:spec["top"] + size, x:x + size]) for x in spec["x"][side]]
        count = next((index for index, kind in enumerate(kinds) if kind is None), len(kinds))
        # 图标一个挨一个往外排：中间不会空着，也不会有别的颜色
        if all(kind in ELEMENTS for kind in kinds[:count]) and all(kind is None for kind in kinds[count:]):
            out[side] = kinds[:count]
    return out


class Chips:
    """读两块刷新牌子。"""

    def __init__(self, layout: dict):
        self.spec = layout["chips"]
        path = TEMPLATES / "chips.npz"
        if not path.is_file():
            raise FileNotFoundError(f"缺少模板 {path.name}：先运行 tools/build_chips.py")
        with np.load(path) as data:
            alive = data["alive"].astype(np.float32)
        self.alive = (alive - alive.mean()) / (np.linalg.norm(alive - alive.mean()) + 1e-6)

    def crops(self, frame: np.ndarray, top: int) -> tuple[np.ndarray, np.ndarray]:
        (iy0, iy1), (ix0, ix1) = self.spec["icon"]["rows"], self.spec["icon"]["x"]
        (ty0, ty1), (tx0, tx1) = self.spec["text"]["rows"], self.spec["text"]["x"]
        return frame[top + iy0:top + iy1, ix0:ix1], frame[top + ty0:top + ty1, tx0:tx1]

    def read_one(self, frame: np.ndarray, top: int) -> tuple[str, bool] | None:
        """(图标, 是否写着“存活”)；这个位置没有牌子返回 None。"""
        icon, text = self.crops(frame, top)
        kind = icon_kind(icon)
        if kind in (None, UNSURE):
            return None
        ink = ink_grey(text)
        if ink.mean() < TEXT_MIN:
            return None
        ink = ink - ink.mean()
        score = float((ink * self.alive).sum() / (np.linalg.norm(ink) + 1e-6))
        return kind, score >= ALIVE_SCORE

    def read(self, frame: np.ndarray) -> dict[str, tuple[str, bool] | None]:
        return {name: self.read_one(frame, top) for name, top in self.spec["top"].items()}


# ---- 事件时间轴 ----

def _marks(roi: np.ndarray, side: str) -> int:
    """这一小块里有多少像素是这一方“事件图标”的亮色（击杀的细竖线是暗色的，不算）。"""
    b, g, r = (roi[..., index].astype(np.int16) for index in range(3))
    if side == "blue":
        return int(np.count_nonzero((b >= 150) & (g >= 115) & (r <= 130) & (b - r >= 60)))
    return int(np.count_nonzero((r >= 160) & (g <= 105) & (b <= 115)))


def read_timeline(frame: np.ndarray, layout: dict) -> dict[str, int] | None:
    """实时 OB 的时间轴最右端（播放头那里），双方各有多少图标像素：{"blue": 0, "red": 310}。

    播放头不在最右端（回放），或者时间轴没显示出来，返回 None。
    """
    spec = layout["timeline"]
    head, reach = spec["head"], spec["window"]
    (top, _), (_, bottom) = spec["rows"]["red"], spec["rows"]["blue"]
    line = frame[top:bottom, head - 2:head + 4].astype(np.int16)
    gold = (line[..., 2] - line[..., 0] >= 30) & (line[..., 1] - line[..., 0] >= 15) & (line[..., 2] >= 90)
    if gold.sum(axis=0).max() < (bottom - top) * 0.4:        # 播放头是一条贯穿两行的土金色竖线
        return None
    return {side: _marks(frame[y0:y1, head - reach:head + reach], side) for side, (y0, y1) in spec["rows"].items()}
