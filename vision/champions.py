"""认英雄头像（BP 界面）：选手那一行的圆形头像、顶部两角的禁用小圆圈。只用 numpy。

模板 vision/templates/champions.npz 由 tools/build_bp_icons.py 从 assets/bp-icons/<英雄>.png 生成：手游选英雄格子里的
方形头像，从全英雄录屏（bprecs/allchamp.mp4）里裁的。WRPedia 的头像有十几个和手游不是同一张图（婕拉、奈德丽、
辛德拉、诺拉、波比……），所以不用它。

圆形头像是同一张图缩小后裁成圆，比例和中心和方形的略有出入，所以每个模板按几种比例取中间的一块、上下左右挪一点，
都缩成 SIZE×SIZE，只比圆里的像素（去均值的归一化相关），取最像的。
"""
from __future__ import annotations

import json

import numpy as np

from .announce import resize as resize_grey
from .text import TEMPLATES

SIZE = 40
FRACTIONS = (1.0, 0.9, 0.8, 0.7)                                    # 取模板中间多大的一块（占边长）
SHIFTS = ((0.0, 0.0), (0.0, -0.06), (0.0, 0.06), (-0.05, 0.0), (0.05, 0.0))     # 再挪多少（占边长）
MIN_SCORE = 0.55        # 最像的那个英雄至少要这么像
MIN_MARGIN = 0.08       # 而且比第二像的高出这么多


def resize(image: np.ndarray, size: int) -> np.ndarray:
    """彩色图面积平均缩放成 size×size。"""
    return np.stack([resize_grey(image[..., channel].astype(np.float32), size, size) for channel in range(3)], axis=2)


_yy, _xx = np.mgrid[:SIZE, :SIZE]
MASK = np.hypot(_yy - (SIZE - 1) / 2, _xx - (SIZE - 1) / 2) <= SIZE * 0.42      # 圆的边上是描边，不比


def vector(image: np.ndarray) -> np.ndarray:
    values = image[MASK].astype(np.float32).ravel()
    values -= values.mean()
    return values / (np.linalg.norm(values) + 1e-6)


class ChampionMatcher:
    def __init__(self):
        path = TEMPLATES / "champions.npz"
        if not path.is_file():
            raise FileNotFoundError(f"缺少模板 {path.name}：先运行 tools/build_bp_icons.py build")
        with np.load(path) as data:
            self.names: list[str] = json.loads(str(data["names"]))
            icons = data["icons"]
        vectors, owners = [], []
        for owner, icon in enumerate(icons):
            side = icon.shape[0]
            for fraction in FRACTIONS:
                crop = int(round(side * fraction))
                for dx, dy in SHIFTS:
                    x0 = int(round(min(side - crop, max(0, (side - crop) / 2 + dx * side))))
                    y0 = int(round(min(side - crop, max(0, (side - crop) / 2 + dy * side))))
                    vectors.append(vector(resize(icon[y0:y0 + crop, x0:x0 + crop], SIZE)))
                    owners.append(owner)
        self.bank = np.stack(vectors)
        self.owners = np.array(owners)

    def scores(self, circle: np.ndarray) -> np.ndarray:
        """每个英雄和这块圆形头像有多像（-1 到 1）。circle 是外接正方形那一块。"""
        per = np.full(len(self.names), -1.0, np.float32)
        np.maximum.at(per, self.owners, self.bank @ vector(resize(circle, SIZE)))
        return per

    def match(self, circle: np.ndarray, min_score: float = MIN_SCORE) -> tuple[str, float] | None:
        """(英雄, 匹配分)；认不准（不是头像，或者两个英雄差不多像）返回 None。"""
        per = self.scores(circle)
        best, second = np.partition(per, -2)[-2:][::-1]
        if best < min_score or best - second < MIN_MARGIN:
            return None
        return self.names[int(per.argmax())], round(float(best), 3)
