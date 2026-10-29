"""BP 识别：数据机停在选人界面时，读阶段、禁用、选人，变成导播台的 BP 命令。只用 numpy。

    reader = BPReader()
    reading = reader.read(frame)        # 不是选人界面 → None
    commands = BPTracker().update(reading, 时间戳秒, 状态中心快照里的 draft)

界面（config/vision.yaml 的 bp，录屏见 bprecs/）：
  · 正上方是阶段标题：预选阶段 → 禁用阶段 → 禁用显示 → 蓝方 / 红方选择英雄（轮流）→ 准备阶段（3 Ban，双方同时暗禁）；
    5 Ban 是蓝方 / 红方禁用英雄一手一手轮流禁。中间偶尔闪一下“选择你的英雄!”。
  · 顶部两角是禁用的小圆圈：空着是“⊘”，禁了就是那个英雄的头像。3 Ban 每边 3 个，5 Ban 每边 5 个（3 + 2）。
  · 两侧各 5 行选手，从上到下就是选人顺序。轮到的人选英雄时，圆形头像里亮出他正在看的英雄（可以换来换去）；
    锁定之后头像不再变，名字那一栏换成“称号 英雄名”。

读秒：标题下面的数字每跳一下，就把导播台的倒计时对到这个数（draft.timer set）；准备阶段不对（那时导播在调分路，
不读秒）。选人阶段读秒突然跳回去（比如 3 → 29）= 新的一手开始了，上一手（导播台上轮到的那几格）亮着的英雄就是选定的。

“亮出”和“锁定”的区分看名字栏：锁定之后，那一行名字栏的第一行从分路（“中路”）或灰色的“玩家N”变成白色粗体的
“称号 英雄名”（config/vision.yaml 的 bp.names，只看有没有这么一长串白字，不认字）。所以：
  · 名字栏连续两次是锁定的样子、而且头像认出的英雄已经稳定了 → 选用（draft.lock）；
  · 还没锁定、正轮到这一方 → 预选（draft.hover，面板上是一闪一闪的）；不是它的回合时亮出来的只是意向（电脑会在
    禁用阶段就亮出想选的英雄），不发；
  · 兜底：进了准备阶段，或者读秒跳回去（新的一手开始），亮着的就是选定的。
以前只按标题轮次算（轮到另一方时，这一方最后亮着的才算选定），第二轮禁用前的最后几手（蓝 2、3 楼，红 3 楼）会一直
挂在预选上：红 3 楼之后是红方禁用、红 4 楼，要等到蓝 4 楼才轮到“另一方”；电脑秒选时“红方选择英雄”这个标题
不到确认的 0.4 秒就过去了（10-25 实测两局都卡在这几楼）。这一局里名字栏的信号一次都没出现过（界面布局变了之类），
才退回到按标题算，外加“选人开始后又进了禁用，亮着的都算选定”。
禁用不分亮出：顶部圆圈里出现的就是禁掉的。空 Ban 按“轮”算：游戏从禁用切到选人一次，就过去了一轮禁用；
那一轮的禁用位（哪几个由导播台选的流程决定：3 Ban 一轮 3 个，赛事征召先 3 后 2）在选人阶段还是“⊘”、
并且持续了一会儿（最后一手禁用可能正好在切换的那一刻才翻出来），才记成空 Ban。

识别只填还空着（或只是预选）的槽位：导播手动锁过的不动，导播改过的也不会被改回去。
"""
from __future__ import annotations

import json

import numpy as np

from state import SIDES

from .announce import resize as resize_grey
from .champions import MIN_MARGIN, ChampionMatcher
from .stats import characters, glyph_score
from .layout import load_layout, to_reference
from .text import TEMPLATES

PHASES = {"预选阶段": "prepick", "禁用阶段": "ban", "蓝方禁用英雄": "ban", "红方禁用英雄": "ban", "禁用显示": "reveal",
          "选择你的英雄!": "pick", "蓝方选择英雄": "pick", "红方选择英雄": "pick", "准备阶段": "ready"}
SIDE_OF = {"蓝方禁用英雄": "blue", "红方禁用英雄": "red", "蓝方选择英雄": "blue", "红方选择英雄": "red"}
EMPTY = "empty"                 # 禁用圆圈里是“⊘”
EMPTY_SIZE = 28
TITLE_CONFIRM = 2               # 标题连续这么多次读成同一个才换阶段
NUDGE = (-3, 0, 3)              # 认不准时圆心上下左右挪这么多再试（小圆圈差几个像素分数就掉得厉害）
SURE = 0.85                     # 原位置就有这么像，不用再挪
PIXEL_DELTA, PIXEL_COUNT = 24, 12       # 圆圈里变化超过 PIXEL_DELTA 的像素不到 PIXEL_COUNT 个，就沿用上次的结果
DIGIT_FLOOR, DIGIT_MARGIN = 0.8, 0.05   # 读秒的一个数字：最像的至少这么像，而且比第二像的高出这么多
CHAMPION_CONFIRM = 3            # 一个圆圈连续这么多次认成同一个英雄才算数（亮出的英雄换来换去，别跟着每一帧闪）
EMPTY_CONFIRM = 3
SKIP_AFTER = 5                  # 一轮禁用结束后，“⊘”还要再连续读到这么多次才记成空 Ban
COUNT_CONFIRM = 2               # 读秒连续这么多次读成同一个数才算
COUNT_LAG = 0.3                 # 读秒跳到 v 的那一刻还剩 v + 1 秒；确认时已经过去了约这么久
LOCK_CONFIRM = 2                # 名字栏连续这么多次是锁定的样子才算锁定
TIMER_PHASES = ("ban", "reveal", "pick")    # 这几个阶段把导播台的倒计时对到游戏的读秒


def _unit(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.float32).ravel()
    values = values - values.mean()
    return values / (np.linalg.norm(values) + 1e-6)


def title_ink(frame: np.ndarray, layout: dict) -> np.ndarray:
    """标题那一块缩成 size 大小，只留亮的笔画（0–1）。"""
    spec = layout["bp"]["title"]
    (y0, y1), (x0, x1), (h, w) = spec["rows"], spec["x"], spec["size"]
    grey = frame[y0:y1, x0:x1].astype(np.float32).mean(axis=2)
    return np.clip((resize_grey(grey, w, h) - 120) / 80, 0, 1)


def countdown_ink(frame: np.ndarray, layout: dict) -> np.ndarray:
    """标题下面的读秒（白色的大号数字）的墨迹（0–1），原尺寸。"""
    spec = layout["bp"]["countdown"]
    (y0, y1), (x0, x1) = spec["rows"], spec["x"]
    return np.clip((frame[y0:y1, x0:x1].astype(np.float32).mean(axis=2) - 120) / 90, 0, 1)


def _circle(frame: np.ndarray, cx: float, cy: float, radius: float) -> np.ndarray:
    x0, y0, r = int(round(cx - radius)), int(round(cy - radius)), int(round(radius))
    return frame[y0:y0 + 2 * r, x0:x0 + 2 * r]


def ban_circles(frame: np.ndarray, layout: dict) -> dict[str, list[np.ndarray]]:
    spec = layout["bp"]["bans"]
    return {side: [_circle(frame, x, spec["y"], spec["radius"]) for x in spec["x"][side]] for side in SIDES}


def pick_circles(frame: np.ndarray, layout: dict) -> dict[str, list[np.ndarray]]:
    spec = layout["bp"]["picks"]
    return {side: [_circle(frame, spec["x"][side], spec["y"] + spec["pitch"] * row, spec["radius"]) for row in range(5)]
            for side in SIDES}


def name_text_width(band: np.ndarray, spec: dict, flip: bool) -> int:
    """名字栏第一行最长的一串字有多宽（像素）。flip = 红方（名字靠右，头像在右边）。"""
    values = band.astype(np.int16)
    low, high = values.min(axis=2), values.max(axis=2)
    white = (low > spec["bright"]) & (high - low < spec["gray"])
    blue, green, red = values[..., 0], values[..., 1], values[..., 2]
    # 段位徽章是金色、绿色的（行高亮是蓝色、红色，不算）。徽章闪的时候有一块白高光，宽到像一个字，离分路又近
    badge = ((red > 120) & (green > 100) & (blue < 0.6 * green)) | ((green > 120) & (green - red > 40) & (green - blue > 40))
    if flip:
        white, badge = white[:, ::-1], badge[:, ::-1]
    white[:, :spec["skip"]] = False                     # 头像外圈的白光
    pieces, start, last = [], None, None
    for x in np.flatnonzero(white.sum(axis=0) >= 2):    # 一列里至少两个白点
        if start is None or x - last > 8:               # 字里笔画之间的空不超过 8
            if start is not None:
                pieces.append((start, last))
            start = x
        last = x
    if start is not None:
        pieces.append((start, last))
    pieces = [(a, z) for a, z in pieces if z - a + 1 >= spec["glyph"]                       # 太窄的不是字
              and badge[:, max(0, a - 4):z + 5].sum() <= white[:, a:z + 1].sum()]          # 金绿比白还多的是徽章
    best, run = 0, None
    for a, z in pieces:
        run = (run[0], z) if run is not None and a - run[1] <= spec["space"] else (a, z)
        best = max(best, run[1] - run[0] + 1)
    return int(best)


def name_locked(frame: np.ndarray, layout: dict) -> dict[str, list[bool]]:
    """每一行名字栏的第一行是不是“称号 英雄名”（锁定了）。见 config/vision.yaml 的 bp.names。"""
    picks, names = layout["bp"]["picks"], layout["bp"]["names"]
    (top, bottom), out = names["rows"], {}
    for side in SIDES:
        x0, x1 = names["x"][side]
        out[side] = [name_text_width(frame[int(round(picks["y"] + picks["pitch"] * row)) + top:
                                           int(round(picks["y"] + picks["pitch"] * row)) + bottom, x0:x1],
                                     names, side == "red") >= names["width"] for row in range(5)]
    return out


def empty_ink(circle: np.ndarray) -> np.ndarray:
    return resize_grey(circle.astype(np.float32).mean(axis=2), EMPTY_SIZE, EMPTY_SIZE)


class BPReader:
    def __init__(self, layout: dict | None = None):
        self.layout = layout or load_layout()
        path = TEMPLATES / "bp-titles.npz"
        if not path.is_file():
            raise FileNotFoundError(f"缺少 {path.name}：先运行 tools/build_bp_icons.py titles")
        with np.load(path) as data:
            self.labels = json.loads(str(data["labels"]))
            self.titles = np.stack([_unit(title) for title in data["titles"]])
            self.empty = _unit(data["empty"])
            # “蓝方 / 红方选择英雄”整行只差一个字（相关 0.86–0.90），单独比开头那两个字（差距 0.33 以上）来定是哪一方
            columns = np.flatnonzero(data["titles"][self.labels.index("蓝方选择英雄")].max(axis=0) > 0.3)
            self.prefix = slice(max(0, int(columns.min()) - 2), int(columns.min()) + 38)
            self.prefixes = np.stack([_unit(title[:, self.prefix]) for title in data["titles"]])
        self.champions = ChampionMatcher()
        self._cache: dict[str, tuple[np.ndarray, str | None]] = {}
        path = TEMPLATES / "bp-countdown.npz"
        if not path.is_file():
            raise FileNotFoundError(f"缺少 {path.name}：先运行 tools/build_bp_icons.py countdown")
        with np.load(path) as data:
            self.digits = {label: data[label] for label in data.files}

    def title(self, frame: np.ndarray) -> tuple[str, float] | None:
        """正上方的阶段标题；不是选人界面返回 None。"""
        ink = title_ink(frame, self.layout)
        scores = self.titles @ _unit(ink)
        best = int(scores.argmax())
        if scores[best] < self.layout["bp"]["title"]["match"]:
            return None
        label = self.labels[best]
        if label in SIDE_OF:                # 是哪一方：只看“蓝方 / 红方”那两个字
            same_kind = [index for index, other in enumerate(self.labels) if other in SIDE_OF and other[2:] == label[2:]]
            prefix = self.prefixes[same_kind] @ _unit(ink[:, self.prefix])
            label = self.labels[same_kind[int(prefix.argmax())]]
        return label, round(float(scores[best]), 3)

    def countdown(self, frame: np.ndarray) -> int | None:
        """标题下面的读秒；读不出返回 None。数字之间总有空列，先切后认（和结算页的数字一样）。"""
        text = ""
        for piece in characters(countdown_ink(frame, self.layout)):
            if piece.shape[1] < 4:              # 光晕、噪点
                continue
            scores = {}
            for label, glyph in self.digits.items():
                if abs(glyph.shape[1] - piece.shape[1]) <= 4:
                    scores[label[0]] = max(scores.get(label[0], -1.0), glyph_score(piece, glyph))
            ranked = sorted(scores.values(), reverse=True)
            if not ranked or ranked[0] < DIGIT_FLOOR or (len(ranked) > 1 and ranked[0] - ranked[1] < DIGIT_MARGIN):
                return None
            text += max(scores, key=scores.get)
        return int(text) if text and len(text) <= 2 and int(text) <= 30 else None

    def circle(self, frame: np.ndarray, key: str, cx: float, cy: float, radius: float, allow_empty: bool) -> str | None:
        """一个圆圈里是哪个英雄；EMPTY = 空禁用（⊘）；None = 认不出（没东西、正在动画、被挡住）。"""
        spec = self.layout["bp"]
        crop = _circle(frame, cx, cy, radius)
        cached = self._cache.get(key)
        if cached is not None and cached[0].shape == crop.shape:
            changed = np.abs(crop.astype(np.int16) - cached[0]).max(axis=2) > PIXEL_DELTA
            if np.count_nonzero(changed) < PIXEL_COUNT:
                return cached[1]
        result = None
        if allow_empty and float(self.empty @ _unit(empty_ink(crop))) >= spec["empty_match"]:
            result = EMPTY
        else:
            best = None                     # (最像的分, 第二像的分, 英雄)
            for dy in (0,) + tuple(d for d in NUDGE if d):
                for dx in (0,) + tuple(d for d in NUDGE if d):
                    per = self.champions.scores(_circle(frame, cx + dx, cy + dy, radius))
                    top, second = np.partition(per, -2)[-2:][::-1]
                    if best is None or top > best[0]:
                        best = (float(top), float(second), self.champions.names[int(per.argmax())])
                    if best[0] >= SURE:
                        break
                if best[0] >= SURE:
                    break
            clear = spec.get("champion_clear") or {}
            if (best[0] >= spec["champion_match"] and best[0] - best[1] >= MIN_MARGIN) or \
                    (clear and best[0] >= clear["match"] and best[0] - best[1] >= clear["margin"]):
                result = best[2]
        self._cache[key] = (crop.astype(np.int16), result)
        return result

    def read(self, frame: np.ndarray) -> dict | None:
        """{"title", "phase", "side", "bans": {side: [英雄 | EMPTY | None]×5}, "picks": {side: [英雄 | None]×5},
        "locked": {side: [名字栏是不是锁定的样子]×5}, "countdown"}。"""
        frame = to_reference(frame, self.layout)
        title = self.title(frame)
        if title is None:
            return None
        label = title[0]
        spec = self.layout["bp"]
        bans = {side: [self.circle(frame, f"{side}.ban.{index}", x, spec["bans"]["y"], spec["bans"]["radius"], True)
                       for index, x in enumerate(spec["bans"]["x"][side])] for side in SIDES}
        picks = {side: [self.circle(frame, f"{side}.pick.{row}", spec["picks"]["x"][side], spec["picks"]["y"] + spec["picks"]["pitch"] * row,
                                    spec["picks"]["radius"], False) for row in range(5)] for side in SIDES}
        return {"title": label, "phase": PHASES[label], "side": SIDE_OF.get(label), "bans": bans, "picks": picks,
                "locked": name_locked(frame, self.layout), "countdown": self.countdown(frame)}


class _Steady:
    """一个圆圈的读数：连续 need 次一样才换。None（认不出）不打断，也不算数。"""

    def __init__(self):
        self.value, self.candidate, self.streak = None, None, 0

    def offer(self, value, need: int) -> bool:
        if value is None:
            return False
        if value == self.candidate:
            self.streak += 1
        else:
            self.candidate, self.streak = value, 1
        if self.streak >= need and value != self.value:
            self.value = value
            return True
        return False


class BPTracker:
    def __init__(self):
        self.title = None                   # 已确认的标题
        self._title_candidate, self._title_streak = None, 0
        self.slots = {}                     # "blue.ban.0" → _Steady
        self.sent = {}                      # 槽位 → 已经发过的 ("hover" | "lock" | "skip", 英雄)
        self.started = False
        self.in_ban = False                 # 正处在一轮禁用里（含禁用显示）
        self.rounds_done = 0                # 已经结束的禁用轮数（从禁用切到选人一次算一轮）
        self.empty_reads = {}               # 槽位 → 那一轮结束后连续读到“⊘”的次数
        self.picking = False                # 已经到过选人阶段
        self.lock_votes = {}                # 选人槽位 → 名字栏连续是锁定样子的次数
        self.names_work = False             # 这一局名字栏的锁定信号出现过（没出现过就退回按标题轮次算）
        self.count = None                   # 已确认的读秒
        self._count_candidate, self._count_streak = None, 0

    def _countdown(self, value: int | None, phase: str, commands: list) -> bool:
        """确认读秒；数字变了就对表。返回这次是不是跳回去了（新的一手开始）。"""
        if value is None:
            return False
        if value == self._count_candidate:
            self._count_streak += 1
        else:
            self._count_candidate, self._count_streak = value, 1
        if self._count_streak < COUNT_CONFIRM or value == self.count:
            return False
        previous, self.count = self.count, value
        if phase in TIMER_PHASES:
            commands.append({"type": "draft.timer", "action": "set", "seconds": round(value + 1 - COUNT_LAG, 2), "source": "vision"})
        return phase == "pick" and previous is not None and value > previous + 2

    def _finished_bans(self, draft: dict | None, size: int, everything: bool) -> dict[str, set[int]]:
        """已经结束的那几轮禁用里，每一方的禁用位。轮次按导播台流程里禁用阶段的先后（draft["turns"] 的 phase）。"""
        if everything:
            return {side: set(range(size)) for side in SIDES}
        if not draft:                       # 不知道流程：只有一轮
            return {side: set(range(size)) if self.rounds_done else set() for side in SIDES}
        phases = []
        for turn in draft["turns"]:
            if turn["type"] == "ban" and turn["phase"] not in phases:
                phases.append(turn["phase"])
        done = set(phases[:self.rounds_done])
        return {side: {index for turn in draft["turns"] if turn["type"] == "ban" and turn["side"] == side and turn["phase"] in done
                       for index in turn["slots"]} for side in SIDES}

    def _steady(self, key: str) -> _Steady:
        return self.slots.setdefault(key, _Steady())

    def _confirm_title(self, label: str) -> bool:
        if label == self._title_candidate:
            self._title_streak += 1
        else:
            self._title_candidate, self._title_streak = label, 1
        if self._title_streak >= TITLE_CONFIRM and label != self.title:
            self.title = label
            return True
        return False

    def update(self, reading: dict | None, now: float, draft: dict | None = None) -> list[dict]:
        """draft 是状态中心快照里的 draft（没有就不知道哪些槽位导播已经锁了，照常发，状态中心会忽略）。"""
        if reading is None:
            return []
        self._confirm_title(reading["title"])
        if self.title is None:
            return []
        commands = []
        phase, side = PHASES[self.title], SIDE_OF.get(self.title)
        if not self.started:
            self.started = True
            commands.append({"type": "draft.start", "source": "vision"})
        # 禁用显示是揭晓暗禁的时候（圆圈正从“⊘”翻成英雄），过了它、进了选人，这一轮禁用才算结束
        if phase in ("ban", "reveal"):
            self.in_ban = True
        elif phase == "pick" and self.in_ban:
            self.in_ban = False
            self.rounds_done += 1
        if phase == "pick":
            self.picking = True
        sizes = {kind: len(draft["slots"]["blue"][kind]) for kind in ("ban", "pick")} if draft else {"ban": 5, "pick": 5}
        finished = self._finished_bans(draft, sizes["ban"], everything=phase == "ready")
        reset = self._countdown(reading.get("countdown"), phase, commands)
        # 读秒跳回去 = 新的一手开始了：导播台上还轮着的那几格（上一手）亮着的英雄就是选定的
        ended = {(ref["side"], ref["index"]) for ref in (draft or {}).get("active", []) if ref["type"] == "pick"} if reset else set()

        def locked(ref) -> bool:
            if not draft:
                return False
            slot = draft["slots"][ref["side"]][ref["type"]][ref["index"]]
            return bool(slot["locked"])

        def send(kind: str, ref: dict, champion: str | None) -> None:
            key = f"{ref['side']}.{ref['type']}.{ref['index']}"
            if self.sent.get(key) == (kind, champion) or self.sent.get(key, ("",))[0] in ("lock", "skip"):
                return                      # 锁过、空过的不再动（改错了由导播改）
            if locked(ref):                 # 导播（或者之前的识别）已经锁了：不动
                self.sent[key] = (kind, champion)
                return
            self.sent[key] = (kind, champion)
            command = {"type": {"hover": "draft.hover", "lock": "draft.lock", "skip": "draft.skip"}[kind],
                       "source": "vision", "slot": ref}
            if champion:
                command["champion"] = champion
            commands.append(command)

        # 禁用：圆圈里出现英雄就是禁了；已经结束的那几轮里还是“⊘”的是空 Ban
        for ban_side in SIDES:
            for index, value in enumerate(reading["bans"][ban_side][:sizes["ban"]]):
                key = f"{ban_side}.ban.{index}"
                steady = self._steady(key)
                steady.offer(value, EMPTY_CONFIRM if value == EMPTY else CHAMPION_CONFIRM)
                ref = {"side": ban_side, "type": "ban", "index": index}
                if steady.value not in (None, EMPTY):
                    send("lock", ref, steady.value)
                    continue
                self.empty_reads[key] = self.empty_reads.get(key, 0) + 1 if value == EMPTY and index in finished[ban_side] else 0
                if self.empty_reads[key] >= SKIP_AFTER:
                    send("skip", ref, None)

        # 选人：名字栏变成“称号 英雄名”就是锁定了；轮到的一方还没锁的是预选（见文件开头）
        if phase in ("pick", "ready") or (self.picking and phase in ("ban", "reveal")):
            locked_rows = reading.get("locked") or {}
            for pick_side in SIDES:
                for index, value in enumerate(reading["picks"][pick_side][:sizes["pick"]]):
                    key = f"{pick_side}.pick.{index}"
                    steady = self._steady(key)
                    steady.offer(value, CHAMPION_CONFIRM)
                    flags = locked_rows.get(pick_side) or []
                    self.lock_votes[key] = self.lock_votes.get(key, 0) + 1 if index < len(flags) and flags[index] else 0
                    by_name = self.lock_votes[key] >= LOCK_CONFIRM
                    self.names_work = self.names_work or by_name
                    if steady.value is None:
                        continue
                    ref = {"side": pick_side, "type": "pick", "index": index}
                    own_turn = phase == "pick" and side in (None, pick_side)
                    if by_name or phase == "ready" or (pick_side, index) in ended:
                        if steady.candidate == steady.value:        # 头像还在换（最后一秒换了英雄）：等它稳定再锁
                            send("lock", ref, steady.value)
                    elif own_turn:
                        send("hover", ref, steady.value)
                    elif not self.names_work:                       # 名字栏不灵：按标题轮次算
                        send("lock", ref, steady.value)
        return commands
