"""读屏幕上方游戏自己的击杀播报，认出多杀：“红方二连破”“蓝方四连破!”这一类。只用 numpy。

播报在画面上方居中（击杀者头像在左、被击杀者在右，中间一行字），出现一秒多。字的颜色跟着阵营：
红方粉白色，蓝方浅青色（蓝方有的播报底板是亮蓝紫色的，所以不能简单地按亮度找字）。

做法：按两种颜色各做一张墨迹图，找出那一行字（按原尺寸，上下微调几个像素对齐），在里面滑动找“连破”两个字；
找到了，它左边那个字就是几连破，和模板里的“二 / 三 / 四 / 五”比。模板来自录像（tools/build_announce.py）；
游戏里还没见过五连破，“五”先用系统字体渲染的，录到真的之后补进去。
"""
from __future__ import annotations

import json

import numpy as np

from .text import TEMPLATES

HEIGHT = 34                 # 播报的字有这么高（画面已经缩放到参考分辨率 2800×1264，所以字的大小是固定的）
NUDGE = 3                   # 字行的上下位置在找到的那一行附近再试这么几个像素（墨迹的上沿每次差一两个像素）
MIN_INK = 500               # 播报那一块里两种颜色的墨迹都不到这么多：没有播报
BAND = (22, 50)             # 一行字的高度范围
LIANPO_MIN = 0.6            # “连破”两个字的匹配分至少这么多
DIGIT_MIN = 0.6             # 真实字模板的匹配分
DIGIT_MARGIN = 0.1          # 而且比第二像的高出这么多
RENDERED_MIN = 0.45         # 渲染出来的字（还没见过真的）要求低一些，但必须是渲染字里最像的
DIGIT_SPAN = (-39, -3)      # 数字在“连”左边的这一段（缩放后的列）
DIGIT_SHIFT = 4             # 数字的位置左右允许偏几列


def ink_red(roi: np.ndarray) -> np.ndarray:
    b, g, r = (roi[..., index].astype(np.int16) for index in range(3))
    return ((r > 195) & (g > 140) & (b > 130) & (r - g > 18)).astype(np.float32)


def ink_blue(roi: np.ndarray) -> np.ndarray:
    b, g, r = (roi[..., index].astype(np.int16) for index in range(3))
    return ((g > 205) & (b > 205) & (g - r > 35)).astype(np.float32)


# 数字那一小块另外用宽一点的门槛再看一遍：“二”只有两道细横，视频压缩后笔画被抹淡，亮度会掉到上面的门槛以下，
# 整个字从墨迹图里消失（10-15 录像 902 秒那条“蓝方二连破”就是这样漏的）。“连破”笔画粗，不受影响，仍用上面的找。
def soft_red(roi: np.ndarray) -> np.ndarray:
    b, g, r = (roi[..., index].astype(np.int16) for index in range(3))
    return ((r > 170) & (g > 115) & (b > 105) & (r - g > 12)).astype(np.float32)


def soft_blue(roi: np.ndarray) -> np.ndarray:
    b, g, r = (roi[..., index].astype(np.int16) for index in range(3))
    return ((g > 180) & (b > 180) & (g - r > 27)).astype(np.float32)


INKS = {"red": ink_red, "blue": ink_blue}
SOFT_INKS = {"red": soft_red, "blue": soft_blue}


def text_lines(ink: np.ndarray) -> list[np.ndarray]:
    """墨迹图里那一行字：以找到的那一行为中心，上下各挪几个像素，切出几条 HEIGHT 高的候选。找不到返回空列表。"""
    return [ink[y0:y0 + HEIGHT] for y0 in text_rows(ink)]


def text_rows(ink: np.ndarray) -> list[int]:
    """text_lines 的那几条候选各自的上沿。"""
    rows = ink.sum(axis=1) >= 6
    runs, start, gap = [], None, 0
    for y, on in enumerate(np.append(rows, False)):
        if on:
            start, gap = (y if start is None else start), 0
        elif start is not None:
            gap += 1
            if gap > 2:
                runs.append((start, y - gap + 1))
                start, gap = None, 0
    runs = [(a, b) for a, b in runs if BAND[0] <= b - a <= BAND[1]]
    if not runs:
        return []
    top, bottom = max(runs, key=lambda run: ink[run[0]:run[1]].sum())
    centre = (top + bottom) // 2
    return [centre - HEIGHT // 2 + shift for shift in range(-NUDGE, NUDGE + 1)
            if 0 <= centre - HEIGHT // 2 + shift and centre - HEIGHT // 2 + shift + HEIGHT <= ink.shape[0]]


def resize(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """面积平均缩放（只用 numpy）：在积分图上双线性取样，相邻四点一减就是每个输出格子覆盖的那块面积之和。"""
    h, w = image.shape
    integral = np.zeros((h + 1, w + 1))
    integral[1:, 1:] = image.cumsum(axis=0).cumsum(axis=1)
    ys, xs = np.linspace(0, h, height + 1), np.linspace(0, w, width + 1)
    y0 = np.minimum(ys.astype(int), h - 1)[:, None]
    x0 = np.minimum(xs.astype(int), w - 1)[None, :]
    fy, fx = ys[:, None] - y0, xs[None, :] - x0
    grid = (integral[y0, x0] * (1 - fy) * (1 - fx) + integral[y0 + 1, x0] * fy * (1 - fx)
            + integral[y0, x0 + 1] * (1 - fy) * fx + integral[y0 + 1, x0 + 1] * fy * fx)
    sums = grid[1:, 1:] - grid[:-1, 1:] - grid[1:, :-1] + grid[:-1, :-1]
    return (sums / (np.diff(ys)[:, None] * np.diff(xs)[None, :])).astype(np.float32)


def _unit(a: np.ndarray) -> np.ndarray:
    a = a - a.mean()
    return a / (np.linalg.norm(a) + 1e-6)


class Announcer:
    def __init__(self, layout: dict):
        spec = layout["announce"]
        (self.y0, self.y1), (self.x0, self.x1) = spec["rows"], spec["x"]
        path = TEMPLATES / "announce.npz"
        if not path.is_file():
            raise FileNotFoundError(f"缺少模板 {path.name}：先运行 tools/build_announce.py")
        with np.load(path) as data:
            self.lianpo = [_unit(t) for t in data["lianpo"]]
            self.digits = [(label, _unit(t), bool(rendered)) for label, t, rendered in
                           zip(json.loads(str(data["digit_labels"])), data["digits"], data["digit_rendered"])]
        self.lianpo_width = self.lianpo[0].shape[1]
        self.digit_width = self.digits[0][1].shape[1]

    def _find_lianpo(self, line: np.ndarray) -> tuple[float, int]:
        width = self.lianpo_width
        if line.shape[1] < width:
            return 0.0, 0
        windows = np.lib.stride_tricks.sliding_window_view(line, (HEIGHT, width))[0]
        centred = windows - windows.mean(axis=(1, 2), keepdims=True)
        norms = np.linalg.norm(centred.reshape(len(centred), -1), axis=1) + 1e-6
        best, where = 0.0, 0
        for template in self.lianpo:
            scores = np.einsum("nhw,hw->n", centred, template) / norms
            index = int(scores.argmax())
            if scores[index] > best:
                best, where = float(scores[index]), index
        return best, where

    def _digit(self, lines: list[np.ndarray], at: int) -> int | None:
        """lines：同一行字按两种门槛做的墨迹图（见 SOFT_INKS），每个字取两者里更像的。"""
        real, rendered = {}, {}
        for line in lines:
            for shift in range(-DIGIT_SHIFT, DIGIT_SHIFT + 1):
                left = at + DIGIT_SPAN[0] + shift
                if left < 0:
                    continue
                window = line[:, left:left + self.digit_width]
                if window.shape[1] != self.digit_width or window.sum() == 0:
                    continue
                window = _unit(window)
                for label, template, is_rendered in self.digits:
                    bucket = rendered if is_rendered else real
                    bucket[label] = max(bucket.get(label, -1.0), float((window * template).sum()))
        ranked = sorted(real.items(), key=lambda item: -item[1])
        if ranked and ranked[0][1] >= DIGIT_MIN and (len(ranked) < 2 or ranked[0][1] - ranked[1][1] >= DIGIT_MARGIN):
            return int(ranked[0][0])
        # 真实模板都不像：看渲染字。只有最像的是一个还没有真实模板的字（现在只有“五”）才认
        if rendered:
            label, score = max(rendered.items(), key=lambda item: item[1])
            if label not in real and score >= RENDERED_MIN:
                return int(label)
        return None

    def read(self, frame: np.ndarray) -> dict | None:
        """{"side": "red", "count": 3, "score": 0.86}；count 可能是 None（是多杀播报，但认不准几连破）。没有多杀播报返回 None。"""
        strip = frame[self.y0:self.y1, self.x0:self.x1]
        best = None
        for side, ink in INKS.items():
            mask = ink(strip)
            if mask.sum() < MIN_INK:
                continue
            found = max(((*self._find_lianpo(mask[y0:y0 + HEIGHT]), y0) for y0 in text_rows(mask)),
                        default=None, key=lambda item: item[0])
            if found is None:
                continue
            score, at, y0 = found
            if score >= LIANPO_MIN and (best is None or score > best["score"]):
                soft = SOFT_INKS[side](strip)
                best = {"side": side, "count": self._digit([mask[y0:y0 + HEIGHT], soft[y0:y0 + HEIGHT]], at),
                        "score": round(score, 3)}
        return best
