"""BP 状态机。

状态是普通 dict（能直接 JSON 化），函数原地修改它：

    draft = {
      "format": "tournament", "formatName": "赛事征召",
      "status": "idle" | "running" | "lineup" | "done",
      "turns": [{"side", "type", "phase", "phaseName", "slots": [槽位下标…], "seconds"}, …],
      "turn": 当前手的下标（全部完成时等于 len(turns)）,
      "slots": {"blue": {"ban": [slot…], "pick": [slot…]}, "red": {…}},
      "timer": {"running", "totalMs", "endsAt", "remainingMs"},
      "lineupSeconds": 0,                    选完之后的阵容调整阶段读几秒；0 = 不读秒（导播手动确认）
      "free": False,
      "blindBans": False,
    }
    slot = {"champion": slug | None, "locked": bool, "role": None | 分路}

槽位按“队内顺位”存（蓝方第 1 个禁用、红方第 3 个选用……），和 WRPedia 的 picks / bans 一致。
未锁定而 champion 有值 = 预选（亮着但还没确定）；锁定而 champion 为空 = 空 Ban。

“当前手”永远是第一个还有空槽位的手，所以导播跳着填、回头改、清掉重来都不会把流程弄乱。
槽位用 ref = {"side", "type", "index"} 指定。

自选模式（format 里写 free: true）：没有禁用，OB 排位、巅峰对决这类不走赛事征召的对局用。
无畏征召照样按系列赛的设置（series.fearless）来，不要的话在赛事设置里选“不启用”。
双方按选人顺序（默认 B RR BB RR BB R，连选的两手同一轮、共用读秒；format 里可以用 order 改）填英雄，
和赛事征召一样不预设分路：
十个都填完进入阵容阶段，按历史出场猜一套分路，导播对着游戏里的实际分路调好再确认。
不在选人时写死分路，是因为排位里谁走哪一路要进了游戏才看得准；确认之后（甚至开局之后）分路照样能改，
局内面板的英雄会跟着换到对应的那一路。

自选模式可以带暗禁（format 里写 bans: 3）：选人之前双方同时各禁 bans 个，互相看不见，所以两边可以禁同一个英雄
（同一方不能禁两次）。双方的禁用位同时开放（这两手标着 both），导播先填哪边都行，共用一次读秒；禁掉的英雄两边都不能选。

读秒（以游戏里的为准，识别会对表）：每一手的 seconds；自选模式是 format 的 ban_seconds（默认 30）、pick_seconds（默认 25）。
3 Ban 录屏里实测：禁用 30 秒（29→0），选人每轮 25 秒（24→0），连选两手共用（顺序里写成 RR，算一手）；
赛事征召禁用、选人都是一手 30 秒。
暗禁的双方同时禁，也共用一个 30 秒。选完之后的阵容调整阶段默认不读秒（lineup_seconds: 0），由导播手动确认。
"""
from __future__ import annotations

from itertools import permutations

from . import ROLES, SIDES, CommandError

KINDS = ("ban", "pick")
PICKS_PER_SIDE = 5


FREE_ORDER = "B RR BB RR BB R"          # 自选模式（含 3 Ban）默认的选人顺序：蛇形，连选的两手是同一轮、共用一个 30 秒


def build_turns(fmt: dict) -> tuple[list[dict], dict]:
    """把 config/draft.yaml 里的一个 format 展开成逐手列表，并返回每方各类槽位的数量。"""
    if fmt.get("free"):                     # 自选模式：（可选的一轮暗禁，）一轮选用
        bans = int(fmt.get("bans") or 0)
        blind = [{"key": "ban", "name": "禁用", "type": "ban", "order": f"{'B' * bans} {'R' * bans}",
                  "seconds": int(fmt.get("ban_seconds", 30))}]
        fmt = {"phases": (blind if bans else []) + [
            {"key": "pick", "name": "选用", "type": "pick", "order": fmt.get("order", FREE_ORDER),
             "seconds": int(fmt.get("pick_seconds", 25))}]}
    turns = []
    counts = {(side, kind): 0 for side in SIDES for kind in KINDS}
    for phase in fmt["phases"]:
        kind = phase["type"]
        if kind not in KINDS:
            raise ValueError(f"BP 流程里的 type 只能是 ban / pick：{kind}")
        for token in str(phase["order"]).split():
            if token[0] not in "BR" or set(token) != {token[0]}:
                raise ValueError(f"BP 流程里的记号只能由 B 或 R 组成：{token}")
            side = "blue" if token[0] == "B" else "red"
            start = counts[(side, kind)]
            counts[(side, kind)] += len(token)
            turns.append({
                "side": side, "type": kind,
                "phase": phase["key"], "phaseName": phase["name"],
                "slots": list(range(start, start + len(token))),
                "seconds": int(phase["seconds"]),
            })
    for side in SIDES:
        if counts[(side, "pick")] != PICKS_PER_SIDE:
            raise ValueError(f"BP 流程里{side}方的选用数必须是 {PICKS_PER_SIDE}")
    if counts[("blue", "ban")] != counts[("red", "ban")]:
        raise ValueError("BP 流程里双方禁用数不一致")
    return turns, {"ban": counts[("blue", "ban")], "pick": PICKS_PER_SIDE}


def _empty_slot() -> dict:
    return {"champion": None, "locked": False, "role": None}


def new_draft(key: str, fmt: dict) -> dict:
    turns, sizes = build_turns(fmt)
    draft = {
        "format": key,
        "formatName": fmt.get("name", key),
        "status": "idle",
        "turns": turns,
        "turn": 0,
        "slots": {side: {kind: [_empty_slot() for _ in range(sizes[kind])] for kind in KINDS} for side in SIDES},
        "timer": {"running": False, "totalMs": 0, "endsAt": None, "remainingMs": 0},
        "lineupSeconds": int(fmt.get("lineup_seconds", 0)),
        "free": bool(fmt.get("free")),
        "blindBans": bool(fmt.get("free") and fmt.get("bans")),
    }
    if draft["blindBans"]:                  # 暗禁：双方的禁用同时进行
        for turn in draft["turns"]:
            if turn["type"] == "ban":
                turn["both"] = True
    return draft


# ---- 槽位 ----

def get_slot(draft: dict, ref: dict) -> dict:
    try:
        index = int(ref["index"])
        if index < 0:
            raise IndexError
        return draft["slots"][ref["side"]][ref["type"]][index]
    except (KeyError, IndexError, TypeError, ValueError):
        raise CommandError(f"没有这个槽位：{ref}") from None


def _ref(side: str, kind: str, index: int) -> dict:
    return {"side": side, "type": kind, "index": index}


def _first_open_turn(draft: dict) -> int:
    for number, turn in enumerate(draft["turns"]):
        slots = draft["slots"][turn["side"]][turn["type"]]
        if any(not slots[i]["locked"] for i in turn["slots"]):
            return number
    return len(draft["turns"])


def active_refs(draft: dict) -> list[dict]:
    """当前手里还没锁定的槽位。BP 没在进行时为空。"""
    if draft["status"] != "running" or draft["turn"] >= len(draft["turns"]):
        return []
    turn = draft["turns"][draft["turn"]]
    together = [other for other in draft["turns"] if other["phase"] == turn["phase"]] if turn.get("both") else [turn]
    return [_ref(other["side"], other["type"], i) for other in together for i in other["slots"]
            if not draft["slots"][other["side"]][other["type"]][i]["locked"]]


def _target(draft: dict, ref: dict | None) -> dict:
    if ref:
        get_slot(draft, ref)
        return ref
    active = active_refs(draft)
    if not active:
        raise CommandError("现在没有轮到的槽位，请先点一个槽位再操作")
    return active[0]


def used_this_game(draft: dict, ignore: dict | None = None) -> dict[str, str]:
    """本局已经锁定的英雄 → 原因。ignore 是正在被改写的槽位，不算它自己。"""
    used = {}
    for side in SIDES:
        for kind in KINDS:
            for index, slot in enumerate(draft["slots"][side][kind]):
                if ignore and (ignore["side"], ignore["type"], int(ignore["index"])) == (side, kind, index):
                    continue
                if slot["locked"] and slot["champion"]:
                    used[slot["champion"]] = "本局已禁用" if kind == "ban" else "本局已选用"
    return used


def blocked_reason(draft: dict, fearless: dict, ref: dict, champion: str) -> str | None:
    """这个槽位能不能放这个英雄。fearless 见 series.fearless_blocked。"""
    if ref["type"] == "ban" and draft.get("blindBans"):
        # 暗禁：看不见对面禁了什么，两边禁同一个是常事；只有本方已经禁过的不行
        own = {slot["champion"] for index, slot in enumerate(draft["slots"][ref["side"]]["ban"])
               if slot["locked"] and index != int(ref["index"])}
        if champion in own:
            return "本方已禁用"
    else:
        reason = used_this_game(draft, ignore=ref).get(champion)
        if reason:
            return reason
    if ref["type"] == "pick" or fearless.get("bans"):
        return fearless["sides"][ref["side"]].get(champion)
    return None


# ---- 计时 ----

def _timer_start(draft: dict, seconds: float, now: int) -> None:
    total = int(seconds * 1000)
    if total <= 0:                          # 这一段不读秒（阵容调整）
        _timer_stop(draft)
        return
    draft["timer"] = {"running": True, "totalMs": total, "endsAt": now + total, "remainingMs": total}


def timer_remaining(draft: dict, now: int) -> int:
    timer = draft["timer"]
    return max(0, timer["endsAt"] - now) if timer["running"] else timer["remainingMs"]


def timer_pause(draft: dict, now: int) -> None:
    timer = draft["timer"]
    if timer["running"]:
        timer.update(running=False, remainingMs=timer_remaining(draft, now), endsAt=None)


def timer_resume(draft: dict, now: int) -> None:
    timer = draft["timer"]
    if not timer["running"] and timer["remainingMs"] > 0:
        timer.update(running=True, endsAt=now + timer["remainingMs"])


def timer_set(draft: dict, seconds: float, now: int) -> None:
    """把剩余时间改成 seconds，运行 / 暂停状态不变。用来和游戏里的倒计时对表。"""
    timer = draft["timer"]
    remaining = max(0, int(seconds * 1000))
    timer["remainingMs"] = remaining
    timer["totalMs"] = max(timer["totalMs"], remaining)
    timer["endsAt"] = now + remaining if timer["running"] else None


def timer_reset(draft: dict, now: int) -> None:
    """按当前阶段的配置重新倒计时。"""
    if draft["status"] == "running" and draft["turn"] < len(draft["turns"]):
        _timer_start(draft, draft["turns"][draft["turn"]]["seconds"], now)
    elif draft["status"] == "lineup":
        _timer_start(draft, draft["lineupSeconds"], now)


def _timer_stop(draft: dict) -> None:
    draft["timer"] = {"running": False, "totalMs": 0, "endsAt": None, "remainingMs": 0}


# ---- 流程 ----

def _sync(draft: dict, now: int, role_stats: dict) -> None:
    """槽位变化之后重新推导当前手和阶段。"""
    if draft["status"] == "idle":
        return
    previous = draft["turn"]
    draft["turn"] = _first_open_turn(draft)
    complete = draft["turn"] >= len(draft["turns"])
    if draft["status"] == "running":
        if complete:
            draft["status"] = "lineup"
            _prefill_roles(draft, role_stats)
            _timer_start(draft, draft["lineupSeconds"], now)
        elif draft["turn"] != previous and not _same_window(draft, previous, draft["turn"]):
            _timer_start(draft, draft["turns"][draft["turn"]]["seconds"], now)
    elif not complete:
        # 阵容阶段回头清了槽位：退回 BP。
        draft["status"] = "running"
        _timer_start(draft, draft["turns"][draft["turn"]]["seconds"], now)
    elif draft["status"] == "lineup":
        _prefill_roles(draft, role_stats)


def _same_window(draft: dict, a: int, b: int) -> bool:
    """两手同时进行（暗禁的双方禁用）：从一手换到另一手不重新读秒。"""
    turns = draft["turns"]
    return a < len(turns) and b < len(turns) and bool(turns[a].get("both")) and turns[a]["phase"] == turns[b]["phase"]


def start(draft: dict, now: int) -> None:
    if draft["status"] != "idle":
        raise CommandError("BP 已经开始了；要重来请先点“重置本局 BP”")
    draft["status"] = "running"
    draft["turn"] = _first_open_turn(draft)
    _timer_start(draft, draft["turns"][draft["turn"]]["seconds"], now)


def hover(draft: dict, champion: str | None, ref: dict | None = None) -> None:
    """预选：槽位上亮出英雄但不锁定。champion 为 None 时取消预选。"""
    slot = get_slot(draft, _target(draft, ref))
    if slot["locked"]:
        raise CommandError("这个槽位已经锁定；要改请直接重新锁定或先清空")
    slot["champion"] = champion


def lock(draft: dict, fearless: dict, role_stats: dict, now: int,
         champion: str | None = None, ref: dict | None = None, force: bool = False) -> dict:
    """锁定一个槽位。不给 champion 就锁它当前的预选；对已锁定的槽位调用等于改写。返回实际锁定的 ref。"""
    if draft["status"] == "idle":
        raise CommandError("BP 还没开始")
    ref = _target(draft, ref)
    slot = get_slot(draft, ref)
    champion = champion or slot["champion"]
    if not champion:
        raise CommandError("还没选英雄" + ("；要空 Ban 请点“空 Ban”" if ref["type"] == "ban" else ""))
    reason = blocked_reason(draft, fearless, ref, champion)
    if reason and not force:
        raise CommandError(f"{reason}，不能再选。确实要这样填请按住 Shift 再锁定")
    slot.update(champion=champion, locked=True)
    _sync(draft, now, role_stats)
    return ref


def skip(draft: dict, role_stats: dict, now: int, ref: dict | None = None) -> dict:
    """空 Ban：这一手不禁任何英雄。"""
    if draft["status"] == "idle":
        raise CommandError("BP 还没开始")
    ref = _target(draft, ref)
    if ref["type"] != "ban":
        raise CommandError("只有禁用位可以留空")
    get_slot(draft, ref).update(champion=None, locked=True)
    _sync(draft, now, role_stats)
    return ref


def clear(draft: dict, role_stats: dict, now: int, ref: dict) -> None:
    """清空一个槽位。流程会退回到这一手。"""
    get_slot(draft, ref).update(champion=None, locked=False, role=None)
    _sync(draft, now, role_stats)


def swap(draft: dict, a: dict, b: dict) -> None:
    """交换两个同类槽位的内容（填错顺序时用）。分路跟着英雄走。"""
    if (a["side"], a["type"]) != (b["side"], b["type"]):
        raise CommandError("只能交换同一方、同一类的两个槽位")
    slots = draft["slots"][a["side"]][a["type"]]
    get_slot(draft, a), get_slot(draft, b)
    i, j = int(a["index"]), int(b["index"])
    slots[i], slots[j] = slots[j], slots[i]
    draft["turn"] = _first_open_turn(draft) if draft["status"] != "idle" else draft["turn"]


# ---- 阵容与分路 ----

def guess_roles(champions: list[str | None], role_stats: dict) -> list[str]:
    """按历史出场给 5 个英雄分配上野中下辅，取总占比最高的一种排法。没有记录的英雄填剩下的位置。"""
    best, best_score = list(ROLES), -1.0
    for order in permutations(ROLES):
        score = sum(role_stats.get(champion or "", {}).get(role, 0.0) for champion, role in zip(champions, order))
        if score > best_score:
            best, best_score = list(order), score
    return best


def _prefill_roles(draft: dict, role_stats: dict) -> None:
    for side in SIDES:
        picks = draft["slots"][side]["pick"]
        if sorted(slot["role"] or "" for slot in picks) == sorted(ROLES):
            continue        # 已经是完整的一套分路（可能是导播调过的），不动
        for slot, role in zip(picks, guess_roles([slot["champion"] for slot in picks], role_stats)):
            slot["role"] = role


def set_role(draft: dict, side: str, index: int, role: str) -> None:
    """指定某个选用位的分路；原来占着这个分路的选用位换成它原先的分路。确认阵容之后也能改。"""
    if draft["status"] not in ("lineup", "done"):
        raise CommandError("双方选完之后才能调整分路")
    if role not in ROLES or side not in SIDES:
        raise CommandError(f"没有这个分路：{role}")
    picks = draft["slots"][side]["pick"]
    slot = get_slot(draft, _ref(side, "pick", index))
    for other in picks:
        if other is not slot and other["role"] == role:
            other["role"] = slot["role"]
    slot["role"] = role


def confirm_lineup(draft: dict) -> None:
    if draft["status"] != "lineup":
        raise CommandError("双方十个英雄都填好之后才能确认阵容" if draft.get("free") else "双方选完之后才能确认阵容")
    draft["status"] = "done"
    _timer_stop(draft)


def lineup(draft: dict, side: str) -> dict[str, str | None]:
    """分路 → 英雄。阵容没确认过分路时返回空。"""
    return {slot["role"]: slot["champion"] for slot in draft["slots"][side]["pick"] if slot["role"]}


def summary(draft: dict) -> dict:
    """结束一局时存进系列赛记录的精简版。"""
    return {
        kind + "s": {side: [slot["champion"] for slot in draft["slots"][side][kind]] for side in SIDES}
        for kind in KINDS
    } | {"roles": {side: [slot["role"] for slot in draft["slots"][side]["pick"]] for side in SIDES}}
