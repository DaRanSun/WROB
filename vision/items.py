"""认装备：把装备栏里的一个格子和装备库里的图标逐个比对。只用 numpy。

装备库来自掌盟的圆形图标（tools/build_items.py），它正好是游戏里方形图标的内切圆，
所以只比格子中间那个圆里的像素。

个别装备在游戏里的图标和掌盟的不一样（比如破败王者之刃），认不出来时，从录像里把那一格裁下来当它的“变体”
（tools/item_variant.py，存在 <组名>-variants.npz）。一件装备可以有好几个模板，取其中最像的那个。
"""
from __future__ import annotations

import json

import numpy as np

from .text import TEMPLATES

UNKNOWN = "?"           # 格子里有东西，但认不准是哪件（比如主动装备冷却时被压暗）
EMPTY_TEXTURE = 12      # 空格子是一块纯色，像素的标准差低于这个数
MIN_SCORE = 0.62        # 最像的那一件至少要这么像
MIN_MARGIN = 0.12       # 而且要比第二像的高出这么多


class ItemMatcher:
    """一个图标库。name 是 vision/templates/ 里的文件名：items（装备）、spells（召唤师技能）、runes（基石符文）。"""

    def __init__(self, name: str = "items"):
        path = TEMPLATES / f"{name}.npz"
        if not path.is_file():
            tool = "tools/build_items.py" if name == "items" else "tools/build_icons.py build"
            raise FileNotFoundError(f"缺少模板 {path.name}：先运行 {tool}")
        with np.load(path) as data:
            self.names: list[str] = json.loads(str(data["names"]))
            icons = data["icons"]
        owners = list(range(len(self.names)))           # 每个模板是哪一件
        variants = TEMPLATES / f"{name}-variants.npz"
        if variants.is_file():
            with np.load(variants) as data:
                extra_names, extra = json.loads(str(data["names"])), data["icons"]
            if len(extra):
                owners += [self.names.index(item) for item in extra_names]
                icons = np.concatenate([icons, extra.astype(icons.dtype)])
        self.owners = np.array(owners)
        size = icons.shape[1]
        yy, xx = np.mgrid[:size, :size]
        self.mask = np.hypot(yy - (size - 1) / 2, xx - (size - 1) / 2) <= size / 2 - 2.5
        self.size = size
        self.templates = np.stack([self._vector(icon) for icon in icons])

    def _vector(self, cell: np.ndarray) -> np.ndarray:
        vector = cell[self.mask].astype(np.float32).ravel()
        vector -= vector.mean()
        return vector / (np.linalg.norm(vector) + 1e-6)

    def match(self, cell: np.ndarray) -> str | None:
        """装备的名字；空格子返回 None；认不准返回 UNKNOWN。"""
        if cell.shape[:2] != (self.size, self.size):
            return UNKNOWN
        if cell[self.mask].std() < EMPTY_TEXTURE:
            return None
        per_item = np.full(len(self.names), -1.0, np.float32)
        np.maximum.at(per_item, self.owners, self.templates @ self._vector(cell))     # 同一件装备的几个模板取最像的
        best, second = np.partition(per_item, -2)[-2:][::-1]
        if best < MIN_SCORE or best - second < MIN_MARGIN:
            return UNKNOWN
        return self.names[int(per_item.argmax())]
