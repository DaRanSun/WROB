"""读 config/vision.yaml，把画面裁成一块块要识别的区域。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from state import SIDES

CONFIG = Path(__file__).resolve().parent.parent / "config" / "vision.yaml"


class LayoutError(RuntimeError):
    pass


def load_layout(path: Path = CONFIG) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def to_reference(frame: np.ndarray, layout: dict) -> np.ndarray:
    """把画面变成参考分辨率。宽高比不同就拒绝：那种情况下坐标对不上，硬读只会读出错的数。"""
    width, height = layout["reference"]
    h, w = frame.shape[:2]
    if (w, h) == (width, height):
        return frame
    if abs(w / h - width / height) > 0.01:
        raise LayoutError(
            f"画面是 {w}×{h}，和标定用的 {width}×{height} 宽高比不同，需要重新标定 config/vision.yaml")
    # 最近邻取样足够：只差一个整体缩放，之后还要做模板相关
    ys = (np.arange(height) * h / height).astype(int)
    xs = (np.arange(width) * w / width).astype(int)
    return frame[ys][:, xs]


def clock_field(frame: np.ndarray, layout: dict) -> np.ndarray:
    (y0, y1), (x0, x1) = layout["clock"]["rows"], layout["clock"]["x"]
    return frame[y0:y1, x0:x1]


def top_fields(frame: np.ndarray, layout: dict) -> dict[str, np.ndarray]:
    y0, y1 = layout["top"]["rows"]
    return {name: frame[y0:y1, spec["x"][0]:spec["x"][1]] for name, spec in layout["top"]["fields"].items()}


def panel_rows(layout: dict) -> list[tuple[int, int]]:
    panel = layout["panel"]
    y0, y1 = panel["rows"]
    return [(y0 + panel["pitch"] * index, y1 + panel["pitch"] * index) for index in range(panel["count"])]


def panel_fields(frame: np.ndarray, layout: dict) -> dict[str, np.ndarray]:
    """{"blue.2.gold": 小块, "red.0.kda": 小块, …}，序号 0–4 是上野中下辅。"""
    cells = {}
    for index, (y0, y1) in enumerate(panel_rows(layout)):
        for name, (x0, x1) in layout["panel"]["fields"].items():
            side, field = name.split(".")
            cells[f"{side}.{index}.{field}"] = frame[y0:y1, x0:x1]
    return cells


def item_cells(frame: np.ndarray, layout: dict) -> dict[str, list[np.ndarray]]:
    """{"blue.0": [6 个格子], …}。每人的格子由外到内：第 0 个是鞋子。"""
    spec, size = layout["items"], layout["items"]["size"]
    cells = {}
    for index in range(layout["panel"]["count"]):
        y = spec["top"] + layout["panel"]["pitch"] * index
        for side in SIDES:
            cells[f"{side}.{index}"] = [frame[y:y + size, x:x + size] for x in spec["x"][side]]
    return cells


def card_fields(frame: np.ndarray, layout: dict) -> dict[str, np.ndarray]:
    """{"blue.2.respawn": 头像中间出复活倒计时的那一块, …}"""
    spec = layout["cards"]
    fields = {}
    for index in range(spec["count"]):
        for side in SIDES:
            (y0, y1), (x0, x1) = spec["rows"][side], spec["x"][side]
            fields[f"{side}.{index}.respawn"] = frame[y0 + spec["pitch"] * index:y1 + spec["pitch"] * index, x0:x1]
    return fields


def spell_cells(frame: np.ndarray, layout: dict) -> dict[str, np.ndarray]:
    """{"blue.0.spell.0": 小图, "blue.0.spell.1": …, "blue.0.rune": …, …}：选手卡片上的两个召唤师技能和基石符文。"""
    spec, size = layout["spells"], layout["spells"]["size"]
    cells = {}
    for side in SIDES:
        x, y = spec["origin"][side]
        for index in range(layout["cards"]["count"]):
            top = y + layout["cards"]["pitch"] * index
            for slot in range(2):
                y0 = top + spec["skill_pitch"] * slot
                cells[f"{side}.{index}.spell.{slot}"] = frame[y0:y0 + size, x:x + size]
            y0 = top + spec["rune_drop"]
            cells[f"{side}.{index}.rune"] = frame[y0:y0 + size, x - 1:x - 1 + size]
    return cells


def level_fields(frame: np.ndarray, layout: dict) -> dict[str, np.ndarray]:
    """{"blue.2.level": 头像右下角写着等级的那一小块, …}"""
    spec = layout["levels"]
    fields = {}
    for side in SIDES:
        (y0, y1), (x0, x1) = spec["rows"][side], spec["x"][side]
        for index in range(layout["cards"]["count"]):
            drop = layout["cards"]["pitch"] * index
            fields[f"{side}.{index}.level"] = frame[y0 + drop:y1 + drop, x0:x1]
    return fields


def ult_fields(frame: np.ndarray, layout: dict) -> dict[str, np.ndarray]:
    """{"blue.2.ult": 头像上角大招圆点周围的一个小方块, …}"""
    spec, radius = layout["ults"], layout["ults"]["radius"]
    fields = {}
    for side in SIDES:
        x, y = spec["centre"][side]
        for index in range(layout["cards"]["count"]):
            top = y + layout["cards"]["pitch"] * index
            fields[f"{side}.{index}.ult"] = frame[top - radius:top + radius + 1, x - radius:x + radius + 1]
    return fields


def portrait_is_grey(roi: np.ndarray) -> bool:
    """头像这一块是不是被游戏置成了灰色（阵亡）。红色的倒计时数字不算在内，只看其余像素有没有颜色。

    不能只看“大部分像素没颜色”：有些英雄的头像本来就偏灰（蛮王、时光）。真正置灰的头像连最鲜艳的那一成
    像素都没什么颜色，所以中位数和九成分位都要低。
    """
    pixels = roi.reshape(-1, 3).astype(np.int16)
    b, g, r = pixels[:, 0], pixels[:, 1], pixels[:, 2]
    rest = pixels[r - (g + b) // 2 < 40]                       # 去掉红色的数字
    if len(rest) < len(pixels) // 4:
        return False
    chroma = rest.max(axis=1) - rest.min(axis=1)
    middle, high = np.percentile(chroma, (50, 90))
    return bool(middle < 12 and high < 30)


def panel_visible(frame: np.ndarray, layout: dict) -> bool:
    """底部面板是不是展开着：每一行两侧没有字的位置，应该分别是偏蓝和偏红的暗色纯色底。"""
    for y0, y1 in panel_rows(layout):
        for side in SIDES:
            x0, x1 = layout["panel"]["visible"][side]["x"]
            patch = frame[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
            b, g, r = patch.mean(axis=0)
            flat = patch.std(axis=0).max() < 14                 # 纯色底：几乎没有纹理
            dark = 30 < max(b, g, r) < 140
            tinted = (b - r > 15) if side == "blue" else (r - b > 15)
            if not (flat and dark and tinted):
                return False
    return True
