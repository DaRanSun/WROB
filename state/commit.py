"""识别结果的去抖提交与校验（claude.md 第 6.3 节）。

纯逻辑，不碰图像：识别服务每 200ms 把读到的候选值喂进来，这里决定哪些值可以提交给状态中心。
阈值是按设计稿定的，还没有拿真实录像调过。
"""
from __future__ import annotations


class Debouncer:
    """同一个候选连续读到 confirm 次才提交。

    monotonic=True 用于只增不减的量（总经济、击杀数）：读到下降时要连续 decrease_confirm 次
    一致才接受（卖装备、识别抖动都可能让数字变小，前者是真的，后者不是）。
    """

    def __init__(self, confirm: int = 2, decrease_confirm: int = 3, monotonic: bool = False):
        self.confirm = confirm
        self.decrease_confirm = decrease_confirm
        self.monotonic = monotonic
        self.value = None
        self._candidate = None
        self._streak = 0

    def offer(self, candidate):
        """喂一次读数。有新值提交时返回它，否则返回 None。读不出来就传 None。"""
        if candidate is None or candidate == self.value:
            self._candidate, self._streak = None, 0
            return None
        if candidate == self._candidate:
            self._streak += 1
        else:
            self._candidate, self._streak = candidate, 1
        falling = self.monotonic and self.value is not None and candidate < self.value
        if self._streak < (self.decrease_confirm if falling else self.confirm):
            return None
        self.value = candidate
        self._candidate, self._streak = None, 0
        return candidate


def plausible_gold_jump(old: int | None, new: int, seconds: float, kda_changed: bool,
                        limit_per_second: int = 1000) -> bool:
    """经济涨幅是否可信：短时间内涨得离谱、而这个人的 KDA 又没变，多半是读错了一位数。"""
    if old is None or new <= old or kda_changed:
        return True
    return new - old <= limit_per_second * max(seconds, 1.0)


def cross_check(player_golds: list[int | None], shown_team_gold: int | None,
                player_kills: list[int | None], shown_team_kills: int | None,
                gold_tolerance: int = 100) -> list[str]:
    """同一方 5 人数据和顶部条互相对账。返回对不上的项（空列表 = 这一帧可信）。

    顶部团队经济只显示到 0.1K，所以经济允许差 gold_tolerance；人头必须完全相等。
    """
    problems = []
    if shown_team_gold is not None and all(gold is not None for gold in player_golds):
        if abs(sum(player_golds) - shown_team_gold) > gold_tolerance:
            problems.append("gold")
    if shown_team_kills is not None and all(kills is not None for kills in player_kills):
        if sum(player_kills) != shown_team_kills:
            problems.append("kills")
    return problems
