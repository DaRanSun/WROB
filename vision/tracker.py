"""把逐帧的读数变成发给状态中心的命令：去抖、校验、时钟对表。纯逻辑，不碰图像也不碰网络。

    tracker = Tracker()
    commands = tracker.update(reader.read(frame), 时间戳秒)      # → [{"type": "game.set", …}, …]

规则（claude.md 第 6.3 节）：
  · 同一个值连续读到两次才提交；只增不减的量读到下降时要连续三次。
  · 经济一下子涨得离谱、而这个人的 KDA 没变，多半是多读了一位数，丢掉这次读数。
  · 一方五个人的经济之和与顶部条差得太远时，这一帧的逐人经济整体不采信。
  · 底部面板收起后逐人数据冻结（不提交新值），并告诉状态中心改用顶部条的团队经济。
  · 时钟：平时让状态中心自己走，只有偏差超过容许范围、暂停或恢复时才重新对表。
  · 装备：每一格连续两次认成同一件才换；认不准（冷却时被压暗）就保持原样；“变成空格子”要持续约 2 秒才认。
  · 复活倒计时：数字连续两次一致才认；倒计时还剩不少却突然“活了”，多半是头像被特效盖住，要持续 1 秒才认。
  · 元素龙：原生顶部条上某一方多出一个图标，持续 1 秒就报一次击杀（带阵营和属性）。
  · 先锋、男爵、远古龙：刷新牌子上的“存活”不见了并持续约 1.6 秒，报一次击杀。打掉男爵 / 远古龙后，原生界面会把
    刷新牌子整列收起，只显示一块 buff 计时牌子（图标是那个野怪、底色是拿到的一方）：这时只有和 buff 对得上的那个坑
    算击杀，阵营就是底色；另一个坑的“存活”只是被收起来了，不算（10-20 直播打远古龙时男爵被误报过）。
    没有 buff 牌子时，是哪一方打的看时间轴：
    实时 OB 里新事件的图标出现在时间轴最右端，哪一行（红上蓝下）突然多出一个图标就是哪一方；
    两行都多了或者都没多（回放、面板收起）就不猜，留给导播点。
  · 多杀：以游戏自己的播报为准（“红方三连破”），同一条播报连续两次读成一样才报，一条播报只报一次。
    数字认不出来时，同一方不久前刚报过 N 连破就当作 N+1（二、三连破之后 15 秒内，四连破之后 30 秒内）。
    是谁：这一方刚涨了人头的那名选手（人头和播报差不多同时出现，前后等几秒）；原生面板收起时读不到人头，
    不等，报“不知道是谁”。
  · 冷却：召唤师技能和装备的图标由亮转暗的那一刻算“用掉了”，从那一刻起按 config/cooldowns.yaml 的时长读条，
    读满为止。原生图标在冷却中是一圈圈慢慢亮回来的，不能拿“亮回来了”当结束（那样最后两三成的时间读条就没了）；
    只有图标已经完全亮了好一会儿、而读条还剩不少的时候（技能急速让冷却变短了，或者那一下其实不是用掉），才提前结束。
    惩戒这种充能的、不知道冷却多久的，只报“暗着”，完全亮了再结束。
"""
from __future__ import annotations

from collections import deque

from state import SIDES
from state.catalog import load_config
from state.commit import Debouncer, plausible_gold_jump
from state.game import parse_clock

from .items import UNKNOWN
from .objectives import ELEMENTS

PLAYERS = range(5)
# 经济、击杀这些量在一局里只增不减。读到变小，要连续这么多次（约 2 秒）都是同一个值才认——真的变小只有新开一局
# 或回放往回拖，那会一直保持；读错造成的“变小”撑不过下一次数值变化（经济每秒都在涨）。
DECREASE_CONFIRM = 10
GOLD_SUM_TOLERANCE = 1500       # 顶部条比面板晚刷新一拍，击杀瞬间能差几百；差到上千才是读错了位数
CLOCK_PAUSE_SECONDS = 1.6       # 时钟读数这么久没变，认为比赛暂停了
CLOCK_EARLY, CLOCK_LATE = 0.3, 0.5      # 状态中心的时钟比画面慢 / 快超过这么多秒才重新对表
BIG_JUMP = 30                   # 时钟一下子跳这么多秒以上（拖动回放、或者读错了）……
JUMP_CONFIRM = 1.0              # ……要连续这么久都读成那附近的时间才认
TICK_WINDOW = 0.35              # 两次读数相隔不超过这么久，才用它们之间的跳秒来校准（取样间隔是 0.2 秒）
PANEL_CONFIRM = 3               # 面板展开 / 收起要连续几帧一致才算数
ITEM_GONE_CONFIRM = 10          # 装备格从“有东西”变成“空”，要连续这么多次才认（带冷却的装备会被压暗得像空格子）
REVIVE_CONFIRM = 5              # 倒计时还没走完就读成“活着”，要连续这么多次才认
REVIVE_NATURAL = 2              # 倒计时剩这么几秒以内再读成“活着”，属于正常复活，按普通的两次确认
DRAGON_CONFIRM = 5              # 顶部条上的元素龙图标变多了，要连续这么多次才认
CHIP_CONFIRM = 3                # 刷新牌子的状态变了，要连续这么多次才认
CHIP_GONE_CONFIRM = 8           # 写着“存活”的牌子不见了（= 被打掉了），要连续这么多次才认
BUFF_RECENT = 5.0               # buff 计时牌子在这么多秒内见过，就拿它来判断是哪个野怪、哪一方
DRAGON_ICON_LAG = 1.0           # 顶部条的图标比击杀晚出现约 1 秒（录像里两次都是），记击杀时刻时减掉
SPAWN_SLACK = 5                 # 判断“这时候坑里是哪种野怪”时，在刷新时间前后留几秒余量
MARK_GAIN = 120                 # 时间轴最右端某一行多出这么多图标像素，算多了一个图标（男爵的图标约 300–430）
MARK_WINDOW = 4.0               # 从牌子消失前这么多秒看起：图标可能比牌子消失早一两秒出现
MARK_SPAN = 3                   # 图标是一下子出现的，算“相隔不超过这么多次读数”的涨幅；之前的图标慢慢挪动不算
MARK_HISTORY = 12.0             # 时间轴读数留多少秒
LEVEL_MAX_JUMP = 2              # 等级平时一次涨一两级；涨得更多的（中间漏读了好几级）要多看几眼才认
LEVEL_CONFIRM = 5               # 等级要连续这么多次读成同一个数才认（约 1 秒）：数字压在头像上，偶尔会读错
ULT_STEP = 0.05                 # 大招进度变化超过这么多才报一次
ULT_READY = 0.93                # 绿色占到这么多算转好了（圆点边缘有一圈描边，读不到 1）
ANNOUNCE_CONFIRM = 2            # 多杀播报连续这么多次读成同一个才报
ANNOUNCE_GONE = 0.6             # 播报消失这么久，下一条才算新的
KILL_BEFORE, KILL_AFTER = 6.0, 2.5      # 播报之前这么多秒内、之后这么多秒内涨了人头的，才可能是它的主人
NEXT_WITHIN = {2: 15.0, 3: 15.0, 4: 30.0}   # 认不出几连破时：同一方 N 连破之后这么多秒内的，当作 N+1 连破
LIGHT_ON, LIGHT_OFF = 0.8, 0.5  # 图标亮度相对它最亮时的比例：高于前者算亮着，低于后者算暗了（刚用掉时约 0.25）
READY = 0.97                    # 冷却中图标从 0.25 左右匀速亮回到 1，到这么亮才算原生的冷却真的转好了
READY_SECONDS = 1.5             # 而且要保持这么久
EARLY_SHARE = 0.15              # 读条还剩超过这么多（占总时长）时，原生已经转好，才提前结束；最后这一段一律读满
LIGHT_FLOOR = 60                # 最亮时也不到这个数的格子不判断（还没见过它亮的样子）


class Lamp:
    """一个技能格 / 装备格的亮暗。"""

    def __init__(self, name):
        self.name = name            # 格子里现在是什么；换了东西就换一盏新的
        self.peak = 0.0             # 见过的最亮的样子
        self.lit = Slot()           # 去抖后的亮 / 暗
        self.dark_since = None      # 这一轮第一次读到“暗”的游戏时间
        self.ready_since = None     # 这一轮第一次读到“完全亮了”的游戏时间
HEARTBEAT_SECONDS = 2.0


_NOTHING = object()


class Slot:
    """可以任意变来变去的值（装备格、复活倒计时）的去抖：新值要连续读到 need 次才换。"""

    def __init__(self):
        self.value = _NOTHING
        self._candidate = _NOTHING
        self._streak = 0

    @property
    def known(self) -> bool:
        return self.value is not _NOTHING

    def offer(self, value, need: int = 2) -> bool:
        """喂一次读数，返回这次是否换了值。"""
        if self.known and value == self.value:
            self._candidate, self._streak = _NOTHING, 0
            return False
        if self._candidate is not _NOTHING and value == self._candidate:
            self._streak += 1
        else:
            self._candidate, self._streak = value, 1
        if self._streak < need:
            return False
        self.value, self._candidate, self._streak = value, _NOTHING, 0
        return True

    def dismiss(self, value) -> None:
        """有证据表明不会是 value：正在攒的这个候选作废，从头数。"""
        if self._candidate is not _NOTHING and self._candidate == value:
            self._candidate, self._streak = _NOTHING, 0


class Tracker:
    def __init__(self, rules: dict | None = None):
        rules = rules or load_config("rules")
        self.baron_from = parse_clock(rules["baron"]["first_spawn"]) - SPAWN_SLACK
        self.first_dragon = parse_clock(rules["dragon"]["first_spawn"])
        self.soul_slot = Slot()                     # 第一条小龙刷新前牌子上的属性 = 这一局的龙魂属性
        self.elder_from = parse_clock(rules["elder"]["first_spawn"]) - SPAWN_SLACK
        self.dragons: dict[str, list[tuple[str, float]]] = {side: [] for side in SIDES}     # 已经报过的元素龙：(属性, 游戏秒)
        self.dragon_slots = {side: Slot() for side in SIDES}
        self.dragon_first: dict[str, tuple[tuple, float]] = {}      # 还没确认的新读数第一次出现的游戏时间
        self.pits: dict[str, Slot] = {}             # "upper" / "lower" → 牌子去抖后的状态
        self.pit_lost: dict[str, tuple[float, float]] = {}      # “存活”的牌子第一次读不到时的 (游戏时间, 时间戳)
        self.buff_seen: tuple[str, str, float] | None = None    # 最近看到的 buff 计时牌子：(野怪, 哪一方, 时间戳)
        self.marks: deque[tuple[float, dict]] = deque()         # 最近几秒时间轴最右端的读数：(时间戳, {"blue": n, "red": n})
        self.timing = load_config("cooldowns")
        self.lamps: dict[str, Lamp] = {}                         # "blue.0.spell.1"、"red.3.item.4" → 亮暗
        self.cooldowns: dict[str, dict] = {}                     # "blue.0" → {"spell.1": [开始, 结束或 None], …}（游戏秒）
        self.fields: dict[str, Debouncer] = {}
        self.slots: dict[str, Slot] = {}            # "blue.0.items.3"、"red.2.respawn" → 去抖后的值
        self.changed_at: dict[str, float] = {}      # 每个字段上次提交的时间
        self.kill_at: dict[str, float] = {}         # "red.1" → 上次涨人头的时间
        self.banner = None                          # 正在显示的多杀播报：{"side", "count", "streak", "seen", "sent"}
        self.multikills: list[dict] = []            # 已经认出、还在等着确定是谁的多杀：{"side", "count", "at", "t"}
        self.last_multikill: dict[str, tuple[int, float]] = {}      # 每一方上一次多杀：(几连破, 时间)
        self.panel = Debouncer(confirm=PANEL_CONFIRM)
        self.clock_seen: tuple[int, float] | None = None        # 最近一次读到的 (游戏秒, 第一次读到它的时间)
        self.clock_candidate: tuple[int, float] | None = None    # 一次还没确认的跳变：(游戏秒, 读到的时间)
        self.clock_read_at = float("-inf")
        self.anchor: tuple[float, float, bool] | None = None    # 上次对表：(游戏秒, 时间, 是否在走)
        self.heartbeat_at = float("-inf")
        self.rejected = 0           # 被校验挡掉的读数个数，给日志看

    # ---- 数值 ----

    def _field(self, path: str) -> Debouncer:
        if path not in self.fields:
            self.fields[path] = Debouncer(confirm=2, decrease_confirm=DECREASE_CONFIRM, monotonic=True)
        return self.fields[path]

    def _offer(self, path: str, value, now: float, out: dict) -> None:
        before = self._field(path).value
        committed = self._field(path).offer(value)
        if committed is not None:
            out[path] = committed
            self.changed_at[path] = now
            if path.endswith(".kills") and path.count(".") == 2 and before is not None and committed > before:
                self.kill_at[path[:-len(".kills")]] = now           # 谁、什么时候涨了人头（给多杀播报找主人）

    def _values(self, reading: dict, now: float) -> dict:
        values, out = reading["values"], {}
        for side in SIDES:
            for name in ("kills", "towers"):
                if f"{side}.{name}" in values:
                    self._offer(f"{side}.{name}", values[f"{side}.{name}"], now, out)
        if not reading["panel"]:
            return out
        for side in SIDES:
            golds = [values.get(f"{side}.{index}.gold") for index in PLAYERS]
            team = values.get(f"{side}.gold")
            trusted = not (team is not None and all(gold is not None for gold in golds)
                           and abs(sum(golds) - team) > GOLD_SUM_TOLERANCE)
            if not trusted:
                self.rejected += 1
            for index in PLAYERS:
                kda = values.get(f"{side}.{index}.kda")
                kda_changed = False
                if kda is not None:
                    for name, number in zip(("kills", "deaths", "assists"), kda):
                        path = f"{side}.{index}.{name}"
                        kda_changed |= self._field(path).value != number
                        self._offer(path, number, now, out)
                gold, path = golds[index], f"{side}.{index}.gold"
                if gold is None or not trusted:
                    continue
                elapsed = now - self.changed_at.get(path, now)
                if plausible_gold_jump(self._field(path).value, gold, elapsed, kda_changed):
                    self._offer(path, gold, now, out)
                else:
                    self.rejected += 1
        return out

    # ---- 装备和复活 ----

    def _slot(self, key: str) -> Slot:
        if key not in self.slots:
            self.slots[key] = Slot()
        return self.slots[key]

    def items_of(self, path: str) -> list | None:
        """一个选手已经确认的 6 格装备；还有格子没确认过就返回 None。"""
        slots = [self.slots.get(f"{path}.{index}") for index in range(6)]
        return [slot.value for slot in slots] if all(slot and slot.known for slot in slots) else None

    def _row(self, path: str, count: int) -> list:
        """一排格子（装备、技能）已经确认的内容；还没确认的格子是 None。"""
        slots = [self.slots.get(f"{path}.{index}") for index in range(count)]
        return [slot.value if slot and slot.known else None for slot in slots]

    def _duration(self, player: str, kind: str, name: str):
        """用掉之后冷却多少秒。None = 不知道（只显示暗着）；False = 这件东西不读条。"""
        timing = self.timing
        if kind == "spell":
            if name in timing.get("charged", []) or name not in timing["spells"]:
                return None
            haste = max((timing["spell_haste"].get(item, 0) for item in self._row(f"{player}.items", 6)), default=0)
            return timing["spells"][name] * (1 - haste)
        seconds = timing["items"].get(name)
        return seconds if seconds and seconds > timing.get("show_above", 0) else False

    def _same(self, one, other) -> bool:
        """是不是同一件东西（“传送”和“解封的传送”是同一个技能的两个样子）。"""
        return one == other or any(one in group and other in group for group in self.timing.get("shared", []))

    def _lamps(self, lights: dict, t: float, out: dict) -> None:
        """图标的亮暗 → 冷却读条。lights: {"blue.0.spell": [亮度, 亮度], "blue.0.item": [...6]}。"""
        changed = set()
        for path, values in lights.items():
            player, kind = path.rsplit(".", 1)
            names = self._row(f"{player}.{kind}s", len(values))
            running = self.cooldowns.setdefault(player, {})
            for index, (value, name) in enumerate(zip(values, names)):
                key, entry = f"{path}.{index}", f"{kind}.{index}"
                lamp = self.lamps.get(key)
                if lamp is None or not self._same(lamp.name, name):     # 格子里换了东西：之前的读条作废，重新认它的亮度
                    lamp = self.lamps[key] = Lamp(name)
                    if running.pop(entry, None):
                        changed.add(player)
                elif lamp.name != name:                                 # 同一个技能换了图标：读条接着走，亮度按新图标重新量
                    lamp.name, lamp.peak = name, value
                if name is None:
                    continue
                lamp.peak = max(lamp.peak, value)
                if lamp.peak < LIGHT_FLOOR:
                    continue
                ratio = value / lamp.peak
                if ratio >= READY:
                    lamp.ready_since = t if lamp.ready_since is None else lamp.ready_since
                else:
                    lamp.ready_since = None
                if entry in running and lamp.ready_since is not None and t - lamp.ready_since >= READY_SECONDS:
                    start, end = running[entry]
                    if end is None or end - t > EARLY_SHARE * (end - start):     # 原生早就转好了：提前结束
                        del running[entry]
                        changed.add(player)
                state = True if ratio >= LIGHT_ON else False if ratio <= LIGHT_OFF else None
                if state is not False:
                    lamp.dark_since = None if state else lamp.dark_since
                elif lamp.dark_since is None:
                    lamp.dark_since = t
                if state is None:
                    continue
                first = not lamp.lit.known
                if not lamp.lit.offer(state):
                    continue
                if state:               # 亮到一定程度了：只是为了下一次变暗时能认出“又用了一次”，读条不在这里结束
                    continue
                # 暗了。一开始看到它就是暗的，不知道什么时候用的，只报“暗着”；否则从变暗的那一刻起读条
                seconds = self._duration(player, kind, name)
                if seconds is False:
                    continue
                start = round(lamp.dark_since if lamp.dark_since is not None else t, 1)
                end = None if first or seconds is None else round(start + seconds, 1)
                opening = self.timing.get("opening", {}).get(name)      # 开局就在冷却的：到这一秒第一次转好
                if opening is not None and t < opening:
                    end = float(opening)
                running[entry] = [start, end]
                changed.add(player)
        for player, running in self.cooldowns.items():                  # 读完条的拿掉
            for entry in [entry for entry, (_, end) in running.items() if end is not None and end <= t]:
                del running[entry]
                changed.add(player)
        for player in changed:
            out[f"{player}.cooldowns"] = dict(self.cooldowns[player])

    def _extras(self, reading: dict, out: dict) -> None:
        weak = set(reading.get("weak") or ())
        for path, value in reading["values"].items():
            if path.endswith(".level"):
                slot = self._slot(path)
                # 十位被头像的亮斑糊住时只读得出个位：之前已经九级以上了，读到的又是 0–5，那就是十几级
                if slot.known and slot.value >= 9 and value <= 5 and value + 10 >= slot.value:
                    value += 10
                # 带小尖角的“1”在暗一些的头像上会被认成“4”（实测 11 级被读成 14）。
                # 读到 4 / 14 而“1 / 11”正好是现在的等级或下一级时，按 1 / 11 算
                if slot.known and value % 10 == 4 and value - 3 in (slot.value, slot.value + 1):
                    value -= 3
                # 备用读法（浅色头像上）读出的数只许往上走：它偶尔会把“8”读成“3”（左半边笔画被当成背景减掉了）
                if path in weak and slot.known and value < slot.value:
                    continue
                # 平时一次涨一两级，连续 1 秒读成同一个数就认；跳得多的要 3 秒；
                # 变小只可能是开局调对位时两行换了人，要稳稳读 5 秒才认
                step = value - slot.value if slot.known else 1
                need = LEVEL_CONFIRM * (1 if 0 < step <= LEVEL_MAX_JUMP else 3 if step > 0 else 5)
                if slot.offer(value, need):
                    out[path] = value
            elif path.endswith(".ult"):
                slot = self._slot(path)
                value = 1.0 if value >= ULT_READY else round(round(value / ULT_STEP) * ULT_STEP, 2)
                steady = slot.known and abs(value - slot.value) < ULT_STEP * 1.5 and value not in (0.0, 1.0)
                if not steady and slot.offer(value):
                    out[path] = value
            elif path.endswith(".spells"):
                changed = False
                for index, spell in enumerate(value):
                    if spell not in (UNKNOWN, None):                    # 冷却中被压暗时认不出，保持原样
                        changed |= self._slot(f"{path}.{index}").offer(spell)
                if changed:
                    out[path] = self._row(path, 2)
            elif path.endswith(".rune"):
                if value not in (UNKNOWN, None) and self._slot(path).offer(value):
                    out[path] = value
            elif path.endswith(".items"):
                changed = False
                for index, item in enumerate(value):
                    slot = self._slot(f"{path}.{index}")
                    if item == UNKNOWN:                         # 认不准：保持原样。但格子里确实有东西，不会是空的
                        slot.dismiss(None)
                        continue
                    gone = item is None and slot.known and slot.value is not None
                    changed |= slot.offer(item, ITEM_GONE_CONFIRM if gone else 2)
                if changed and self.items_of(path) is not None:
                    out[path] = self.items_of(path)
            elif path.endswith(".respawn"):
                slot = self._slot(path)
                early = value == 0 and slot.known and slot.value > REVIVE_NATURAL
                if slot.offer(value, REVIVE_CONFIRM if early else 2):
                    out[path] = value or None                   # 状态中心里“活着”是 None

    # ---- 多杀播报 ----

    def _announce(self, announce: dict | None, now: float, t: float | None, panel: bool = True) -> list[dict]:
        commands = []
        banner = self.banner
        if announce is None:
            if banner is not None and now - banner["seen"] > ANNOUNCE_GONE:
                self.banner = None
        else:
            side, count = announce["side"], announce["count"]
            if banner is None or banner["side"] != side:
                banner = self.banner = {"side": side, "count": None, "streak": 0, "seen": now, "sent": None, "blind": 0}
            banner["seen"] = now
            if count is None:
                banner["blind"] += 1
                # 认不出几连破（字的样式还没录到过，比如第一次见到的蓝方“四”）：同一方不久前刚报过 N 连破，这次就是 N+1。
                # 游戏是一级一级播的（二 → 三 → 四 → 五），中间不会跳；二、三连破之间隔不了多久，四到五可以久一点
                previous = self.last_multikill.get(side)
                if banner["blind"] >= ANNOUNCE_CONFIRM + 1 and banner["sent"] is None and previous \
                        and previous[0] in NEXT_WITHIN and now - previous[1] <= NEXT_WITHIN[previous[0]]:
                    count = previous[0] + 1
                    banner["count"], banner["streak"] = count, ANNOUNCE_CONFIRM
            if count is not None:
                if count == banner["count"]:
                    banner["streak"] += 1
                else:
                    banner["count"], banner["streak"] = count, 1
                if banner["streak"] >= ANNOUNCE_CONFIRM and banner["sent"] != count:
                    banner["sent"] = count
                    self.last_multikill[side] = (count, now)
                    self.multikills.append({"side": side, "count": count, "at": now, "t": t})
        # 给认出的多杀找主人：这一方在播报前后几秒内涨了人头、最近的那一个
        for pending in list(self.multikills):
            candidates = [(abs(self.kill_at[key] - pending["at"]), int(key.split(".")[1])) for key in self.kill_at
                          if key.startswith(pending["side"] + ".")
                          and pending["at"] - KILL_BEFORE <= self.kill_at[key] <= pending["at"] + KILL_AFTER]
            if not candidates and panel and now - pending["at"] < KILL_AFTER:
                continue                        # 人头可能比播报晚一点点才读到，再等等（面板收起时读不到人头，不等）
            self.multikills.remove(pending)
            command = {"type": "game.multikill", "source": "vision", "side": pending["side"], "count": pending["count"],
                       "index": min(candidates)[1] if candidates else None}
            if pending["t"] is not None:
                command["t"] = round(pending["t"], 1)
            commands.append(command)
        return commands

    # ---- 野怪 ----

    @staticmethod
    def _kill(what: str, t: float, side: str | None = None, element: str | None = None) -> dict:
        command = {"type": "game.objective", "source": "vision", "what": what, "t": round(t, 1)}
        if side:
            command["side"] = side
        if element:
            command["element"] = element
        return command

    def _who(self, lost_at: float, now: float) -> str | None:
        """牌子在 lost_at 消失的那次击杀是哪一方的：时间轴最右端哪一行突然多出了一个图标。说不准返回 None。

        不能简单比“之前”和“之后”：另一方十几秒前的图标还在最右端附近慢慢往左挪，读数会跟着缓缓变化
        （实跑时因此漏判过一次）。新图标是一两帧之内整个冒出来的，所以只认短时间内的陡增。
        """
        series = [marks for at, marks in self.marks if at >= lost_at - MARK_WINDOW]
        if len(series) <= MARK_SPAN:
            return None
        jump = {side: max(series[k][side] - min(marks[side] for marks in series[max(0, k - MARK_SPAN):k])
                          for k in range(1, len(series))) for side in SIDES}
        winners = [side for side in SIDES if jump[side] >= MARK_GAIN]
        others_quiet = all(jump[side] < MARK_GAIN / 2 for side in SIDES if side not in winners)
        return winners[0] if len(winners) == 1 and others_quiet else None

    def _dragons(self, seen: dict, t: float) -> list[dict]:
        """顶部条上的元素龙图标。图标只会一个个变多；变少只可能是新开了一局或者回放往回拖。"""
        commands = []
        for side, elements in seen.items():
            elements = tuple(elements)
            have = [element for element, _ in self.dragons[side]]
            if list(elements) == have:
                self.dragon_slots[side].offer(elements)
                self.dragon_first.pop(side, None)
                continue
            if self.dragon_first.get(side, (None,))[0] != elements:
                self.dragon_first[side] = (elements, t - DRAGON_ICON_LAG)
            need = DRAGON_CONFIRM if len(elements) > len(have) else DECREASE_CONFIRM
            if not self.dragon_slots[side].offer(elements, need):
                continue
            first = self.dragon_first.pop(side)[1]
            self.dragons[side] = [(element, dict(self.dragons[side]).get(element, first)) for element in elements]
            commands += [self._kill("dragon", first, side, element) for element in elements if element not in have]
        return commands

    def _soul_element(self, chips: dict, t: float) -> list[dict]:
        """第一条小龙刷新前，下面那块牌子的图标就是它的属性，也就是这一局的龙魂属性。连续 3 次一致才报。"""
        lower = chips.get("lower")
        if self.soul_slot.known or t >= self.first_dragon or not lower or lower[0] not in ELEMENTS:
            return []
        if not self.soul_slot.offer(lower[0], CHIP_CONFIRM):
            return []
        return [{"type": "game.soul_element", "source": "vision", "element": lower[0]}]

    def _pits(self, chips: dict, t: float, now: float) -> list[dict]:
        """两块刷新牌子。“存活”的牌子消失了 = 这个坑里的野怪被打掉了。"""
        commands = []
        if chips.get("buff"):
            self.buff_seen = (*chips["buff"], now)
        buff = self.buff_seen if self.buff_seen and now - self.buff_seen[2] <= BUFF_RECENT else None
        for name in ("upper", "lower"):
            state = chips.get(name)
            slot = self.pits.setdefault(name, Slot())
            was = slot.value if slot.known else None
            was_alive = bool(was and was[1])
            losing = was_alive and not (state and state[1])
            if losing:
                self.pit_lost.setdefault(name, (t, now))
            else:
                self.pit_lost.pop(name, None)
            if not slot.offer(state, CHIP_GONE_CONFIRM if losing else CHIP_CONFIRM) or not losing:
                continue
            at, lost_at = self.pit_lost.pop(name)
            if name == "upper":
                what = was[0] if was[0] in ("herald", "baron") else "baron" if at >= self.baron_from else "herald"
            elif was[0] in ELEMENTS or at < self.elder_from:
                continue                    # 元素龙归顶部条的图标管（那里有阵营和属性）；到时间自己消失的也不算击杀
            else:
                what = "elder"
            if buff and buff[0] in ("baron", "elder"):
                if buff[0] != what:
                    continue                # 原生界面在放另一个野怪的 buff，把这块牌子收起来了：不是击杀
                commands.append(self._kill(what, at, buff[1]))      # 阵营看 buff 牌子的底色
                continue
            commands.append(self._kill(what, at, self._who(lost_at, now)))
        return commands

    def _forget_pits(self) -> None:
        """时钟跳了（回放被拖动）：牌子会跟着突变，那不是击杀。"""
        self.pits.clear()
        self.pit_lost.clear()
        self.marks.clear()

    # ---- 时钟 ----

    def _expected(self, now: float) -> float | None:
        """按上次对表推算，状态中心现在显示的游戏时间。"""
        if self.anchor is None:
            return None
        seconds, at, running = self.anchor
        return seconds + (now - at if running else 0.0)

    def _sync(self, seconds: float, now: float, running: bool) -> dict:
        self.anchor = (seconds, now, running)
        return {"type": "game.clock", "action": "sync", "seconds": round(seconds, 2), "running": running, "source": "vision"}

    def _clock(self, observed: int | None, now: float) -> list[dict]:
        if observed is None:
            return []
        previous = self.clock_seen
        running = self.anchor is not None and self.anchor[2]
        last_read = self.clock_read_at
        gap, self.clock_read_at = now - last_read, now       # 距离上一次读出时钟过了多久
        if previous is not None and observed == previous[0]:
            self.clock_candidate = None
            if running and now - previous[1] > CLOCK_PAUSE_SECONDS:
                return [self._sync(observed, now, running=False)]      # 读数停住了：暂停
            return []
        # 读数变了。和状态中心推算的时间对得上（差不到一秒）就直接认；
        # 对不上的（拖动回放、暂停后恢复、读错）要紧接着再读到一次才认。
        expected = self._expected(now)
        if expected is None or abs(observed - expected) > 1.0:
            candidate = self.clock_candidate
            drift = 0 if candidate is None else observed - candidate[0]
            if candidate is None or not 0 <= drift <= 1 + int(now - candidate[1]):
                self.clock_candidate = (observed, now)
                self.clock_read_at = last_read      # 还没认的读数不算“读出来了”，跳秒校准不能拿它当上一次
                return []
            if expected is not None and abs(observed - expected) > BIG_JUMP and now - candidate[1] < JUMP_CONFIRM:
                self.clock_read_at = last_read
                return []           # 一大步跳变：多看一会儿（画面背景太亮时会把时钟读成 22:22 之类）
            # 确认了一次跳变。从第一次读到它起又过了一会儿，对表时把这段时间算上。
            waited = max(0.0, now - self.clock_candidate[1] - drift)
            self.clock_candidate = None
            self.clock_seen = (observed, now)
            self._forget_pits()
            if expected is not None and observed < expected - 30:      # 时钟倒回去了（新的一局、回放往回拖）：等级重新认
                for path in [path for path in self.slots if path.endswith(".level")]:
                    del self.slots[path]
            return [self._sync(observed + 0.1 + waited, now, running=True)]
        self.clock_candidate = None
        # 紧挨着的上一次读数还是前一秒，才能确定跳秒就发生在这两次之间；中间有读不出的帧就不能拿来校准
        ticked = previous is not None and observed == previous[0] + 1 and gap <= TICK_WINDOW
        self.clock_seen = (observed, now)
        if not running:
            return [self._sync(observed + 0.1, now, running=True)]         # 暂停后又走了
        if ticked and not -CLOCK_EARLY <= expected - observed <= CLOCK_LATE:
            return [self._sync(observed + 0.1, now, running=True)]         # 走偏了，拉回来
        return []

    # ---- 对账 ----

    def reconcile(self, snapshot: dict, now: float) -> list[dict]:
        """拿状态中心的快照和自己已经提交的值对一遍，把对不上的补发过去。

        平时只发变化的值。但状态中心那边可能被清空或重开一局（导播点了“进入局内”“清空局内数据”），
        这时没在变的数（人头、KDA、时钟）就得靠这里补回去。导播锁住的字段不碰。
        """
        game, locks = snapshot["game"], set(snapshot["game"]["locks"])
        values = {}
        for path, field in self.fields.items():
            if field.value is None or path in locks:
                continue
            parts = path.split(".")
            if len(parts) == 2 and parts[1] in ("kills", "towers"):
                current = game["teams"][parts[0]][parts[1]]
            elif len(parts) == 3:
                current = game["players"][parts[0]][int(parts[1])][parts[2]]
            else:
                continue                                # 团队经济在状态中心里是算出来的，不直接比
            if current != field.value:
                values[path] = field.value
        for side in SIDES:
            for index in PLAYERS:
                player = game["players"][side][index]
                path = f"{side}.{index}.items"
                items = self.items_of(path)
                if items is not None and path not in locks and player["items"] != items:
                    values[path] = items
                path = f"{side}.{index}.respawn"
                slot = self.slots.get(path)
                if slot and slot.known and path not in locks and player["respawn"] != (slot.value or None):
                    values[path] = slot.value or None
                for name in ("level", "ult"):
                    path, slot = f"{side}.{index}.{name}", self.slots.get(f"{side}.{index}.{name}")
                    if slot and slot.known and path not in locks and player[name] != slot.value:
                        values[path] = slot.value
                path, spells = f"{side}.{index}.spells", self._row(f"{side}.{index}.spells", 2)
                if any(spells) and path not in locks and player["spells"] != spells:
                    values[path] = spells
                path, slot = f"{side}.{index}.rune", self.slots.get(f"{side}.{index}.rune")
                if slot and slot.known and path not in locks and player["rune"] != slot.value:
                    values[path] = slot.value
                path, running = f"{side}.{index}.cooldowns", self.cooldowns.get(f"{side}.{index}")
                if running is not None and path not in locks and player["cooldowns"] != running:
                    values[path] = dict(running)
        commands = [{"type": "game.set", "source": "vision", "values": values}] if values else []
        if "objectives" not in locks:
            # 顶部条上挂着的元素龙是确凿的状态，状态中心里没有就补上。牌子报的击杀是一次性的，不补：
            # 导播把它撤掉多半是因为识别报错了，补回去只会和他抢。
            taken = game["objectives"]["dragon"]["taken"]
            recorded = {element for side in SIDES for element in taken[side]}
            commands += [self._kill("dragon", t, side, element) for side in SIDES
                         for element, t in self.dragons[side] if element not in recorded]
            if self.soul_slot.known and not game["objectives"]["dragon"].get("soulElement"):
                commands.append({"type": "game.soul_element", "source": "vision", "element": self.soul_slot.value})
        expected = self._expected(now)
        if expected is not None and "clock" not in locks:
            clock = game["clock"]
            shown = clock["baseSec"] + ((snapshot["now"] - clock["baseAt"]) / 1000 if clock["running"] else 0.0)
            if clock["running"] != self.anchor[2] or abs(shown - expected) > 1.0:
                commands.append(self._sync(expected, now, self.anchor[2]))
        return commands

    # ---- 入口 ----

    def update(self, reading: dict, now: float) -> list[dict]:
        commands = self._clock(reading["clock"], now)
        values = self._values(reading, now)
        self._extras(reading, values)
        was_hidden = self.panel.value is False
        panel_changed = self.panel.offer(bool(reading["panel"])) is not None
        if panel_changed and self.panel.value and was_hidden:
            # 面板重新展开：逐人经济恢复更新，顶部条那个团队经济不再需要，清掉，免得以后面板再收起时拿到一个很旧的数
            for side in SIDES:
                self.fields.pop(f"{side}.gold", None)
                values[f"{side}.gold"] = None
        if self.panel.value is False:
            # 面板收起期间，逐人经济是旧的；团队经济改用顶部条的读数
            for side in SIDES:
                if f"{side}.gold" in reading["values"]:
                    self._offer(f"{side}.gold", reading["values"][f"{side}.gold"], now, values)
        if values:
            command = {"type": "game.set", "source": "vision", "values": values}
            if self.anchor is not None and self.clock_candidate is None:
                command["t"] = round(self._expected(now), 1)    # 这些数是哪个游戏时刻读到的
            commands.append(command)
        if "announce" in reading or self.banner is not None or self.multikills:
            commands += self._announce(reading.get("announce"), now, self._expected(now) if self.clock_candidate is None else None,
                                       panel=bool(reading["panel"]))
        if reading["clock"] is not None and self.clock_candidate is None:
            # 时钟对不上的那几帧（回放正在被拖动、快进）先不看野怪：牌子和图标会跟着突变，那不是击杀
            game_time = self._expected(now)
            game_time = float(reading["clock"]) if game_time is None else game_time
            if reading.get("timeline"):
                self.marks.append((now, reading["timeline"]))
            while self.marks and self.marks[0][0] < now - MARK_HISTORY:
                self.marks.popleft()
            self._lamps(reading.get("lights") or {}, game_time, values)
            if any(key.endswith(".cooldowns") for key in values) and not any(c["type"] == "game.set" for c in commands):
                commands.append({"type": "game.set", "source": "vision", "values": values})
            commands += self._dragons(reading.get("dragons") or {}, game_time)
            commands += self._soul_element(reading.get("chips") or {}, game_time)
            commands += self._pits(reading.get("chips") or {}, game_time, now)
        if panel_changed or now - self.heartbeat_at >= HEARTBEAT_SECONDS:
            self.heartbeat_at = now
            commands.append({"type": "vision.status", "panel": bool(self.panel.value),
                             "clock": reading["clock"] is not None})
        return commands
