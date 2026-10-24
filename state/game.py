"""局内状态与规则引擎：比赛时钟、野怪刷新与 buff、经济差、Power play、经济曲线。

数值可以来自导播台（source="director"），以后也可以来自画面识别（source="vision"）。
导播锁住的字段，识别写不进去。

    game = {
      "status": "idle" | "live" | "ended",
      "clock": {"running", "baseSec", "baseAt"},     游戏时间 = baseSec + (now - baseAt) / 1000
      "teams": {"blue": {"kills", "towers", "gold"}, "red": {…}},       gold 是手填的团队经济
      "players": {"blue": [5 人，按上野中下辅], "red": […]},
      "events": [{"id", "t", "what", "side", "element",
                  "buffFull", "buffEnd", "holders", "goldStart", "goldEnd"}, …],    后五项只有带 buff 的野怪才有
      "locks": [字段路径…],                                             另有 "clock" 表示时钟由导播手动控制
      "vision": None | {"at": 毫秒, "panel": 底部面板是否展开, "clock": 时钟是否读得出},   识别服务的心跳
      "lead": {"team": 领先方, "lanes": [5 路领先方]},                  带滞回
      "curve": [[游戏秒, 蓝方经济 − 红方经济], …],
      "golds": [[游戏秒, 蓝方经济, 红方经济], …],                 最近两分钟，算 Power play 起点用
    }

字段路径："blue.kills"、"red.towers"、"blue.gold"（团队）、"blue.2.gold"（蓝方中路）、"red.0.alive" 等。
每个选手另有 "items"（6 格装备，由外到内，第 0 格是鞋子；空格为 None）和 "respawn"（复活倒计时秒数，
活着为 None）。写 respawn 会连带改 alive。
时间一律用“游戏秒”。前端拿到刷新时刻和 buff 结束时刻后自己倒数，服务端不用每秒广播。
"""
from __future__ import annotations

import re

from . import ROLES, SIDES, CommandError

OBJECTIVES = ("herald", "dragon", "baron", "elder")
TEAM_FIELDS = {"kills": int, "towers": int, "gold": "gold"}
PLAYER_FIELDS = {"gold": "gold", "kills": int, "deaths": int, "assists": int, "alive": bool, "name": str,
                 "champion": "slug", "respawn": "respawn", "items": "items",
                 "spells": "spells", "rune": "slug", "cooldowns": "cooldowns", "level": "level", "ult": "ult",
                 "bars": "bars"}
ITEM_SLOTS = 6


def parse_clock(text) -> int | None:
    """"6:00" / "1830" / 360 → 秒。"""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return int(text)
    text = str(text).strip()
    if ":" in text or "：" in text:
        minutes, _, seconds = text.replace("：", ":").partition(":")
        return int(minutes or 0) * 60 + int(seconds or 0)
    if len(text) >= 3 and text.isdigit():           # 1830 → 18:30
        return int(text[:-2]) * 60 + int(text[-2:])
    return int(text)


def format_clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def parse_gold(value) -> int | None:
    """12345 / "12345" / "12.3k" → 12345；空 → None。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kK]?)\s*", str(value).replace(",", ""))
    if not match:
        raise CommandError(f"看不懂这个经济数字：{value}")
    return int(round(float(match.group(1)) * (1000 if match.group(2) else 1)))


def new_game() -> dict:
    return {
        "status": "idle",
        "clock": {"running": False, "baseSec": 0.0, "baseAt": 0},
        "teams": {side: {"kills": 0, "towers": 0, "gold": None} for side in SIDES},
        "players": {
            side: [
                {"role": role, "name": "", "champion": None, "gold": None,
                 "kills": 0, "deaths": 0, "assists": 0, "alive": True, "respawn": None, "items": [None] * ITEM_SLOTS,
                 "spells": [None, None], "rune": None, "cooldowns": {}, "level": None, "ult": None, "bars": None}
                for role in ROLES
            ]
            for side in SIDES
        },
        "events": [],
        "seq": 0,
        "locks": [],
        "vision": None,
        "lead": {"team": None, "lanes": [None] * len(ROLES)},
        "curve": [],
        "golds": [],
    }


# ---- 时钟 ----

def game_time(game: dict, now: int) -> float:
    clock = game["clock"]
    return clock["baseSec"] + ((now - clock["baseAt"]) / 1000 if clock["running"] else 0)


def clock_set(game: dict, seconds: float, now: int, running: bool | None = None) -> None:
    """把游戏时间对到 seconds。识别到时钟读数、导播手动对表都走这里。"""
    clock = game["clock"]
    clock.update(baseSec=max(0.0, float(seconds)), baseAt=now,
                 running=clock["running"] if running is None else running)


def clock_pause(game: dict, now: int) -> None:
    clock_set(game, game_time(game, now), now, running=False)


def clock_resume(game: dict, now: int) -> None:
    clock_set(game, game_time(game, now), now, running=True)


# ---- 字段 ----

def _resolve(game: dict, path: str) -> tuple[dict, str, object]:
    parts = str(path).split(".")
    try:
        if len(parts) == 2 and parts[0] in SIDES and parts[1] in TEAM_FIELDS:
            return game["teams"][parts[0]], parts[1], TEAM_FIELDS[parts[1]]
        if len(parts) == 3 and parts[0] in SIDES and parts[2] in PLAYER_FIELDS:
            return game["players"][parts[0]][int(parts[1])], parts[2], PLAYER_FIELDS[parts[2]]
    except (ValueError, IndexError):
        pass
    raise CommandError(f"没有这个字段：{path}")


def _coerce(kind, value):
    if kind == "gold":
        value = parse_gold(value)
        return None if value is None else max(0, value)
    if kind is bool:
        return bool(value)
    if kind is int:
        return max(0, int(value or 0))
    if kind == "slug":
        return str(value) if value else None
    if kind == "respawn":                       # 0 和空都表示活着
        return max(0, int(value)) or None if value not in (None, "") else None
    if kind == "level":                         # 1–15；空 = 不知道
        return None if value in (None, "") else min(15, max(1, int(value)))
    if kind == "ult":                           # 大招转好了多少，0–1；空 = 不知道
        return None if value in (None, "") else min(1.0, max(0.0, round(float(value), 2)))
    if kind == "bars":                          # 卡片上的血条：血量、护盾、资源是比例（0–1），resource 是资源条颜色；空 = 不知道
        if not value:
            return None
        if not isinstance(value, dict):
            raise CommandError("血条要给 {hp, shield, mana, resource}")
        ratio = {key: min(1.0, max(0.0, round(float(value.get(key) or 0), 2))) for key in ("hp", "shield", "mana")}
        return {**ratio, "resource": str(value["resource"]) if value.get("resource") else None}
    if kind == "spells":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise CommandError("召唤师技能要给两个（没有的写 null）")
        return [str(spell) if spell else None for spell in value]
    if kind == "cooldowns":                     # {"spell.0": [开始, 结束], "item.3": [开始, None]}，游戏秒；None = 暗着但不知道多久
        return {str(key): [float(span[0]), None if span[1] is None else float(span[1])] for key, span in (value or {}).items()}
    if kind == "items":
        if not isinstance(value, (list, tuple)) or len(value) != ITEM_SLOTS:
            raise CommandError(f"装备要给 {ITEM_SLOTS} 格（由外到内，第一格是鞋子，空格写 null）")
        return [str(item) if item else None for item in value]
    return str(value or "").strip()


def get_field(game: dict, path: str):
    container, key, _ = _resolve(game, path)
    return container[key]


def set_field(game: dict, rules: dict, path: str, value, now: int,
              source: str = "director", lock: bool | None = None) -> bool:
    """写一个字段。返回是否真的改了（被导播锁住的字段，识别写不进去）。"""
    container, key, kind = _resolve(game, path)
    if source != "director" and path in game["locks"]:
        return False
    if lock is not None:
        set_lock(game, path, lock)
    value = _coerce(kind, value)
    if container[key] == value:
        return False
    container[key] = value
    died = key == "alive" and not value
    if key == "respawn":                        # 有复活倒计时 = 阵亡
        died = value is not None and container["alive"]
        container["alive"] = value is None
    elif key == "alive" and value:
        container["respawn"] = None
    if died:
        _player_died(game, rules, path.split(".")[0], int(path.split(".")[1]), now)
    if key == "gold":
        refresh_lead(game, rules)
    return True


def set_lock(game: dict, path: str, on: bool) -> None:
    if path not in ("clock", "objectives"):         # 这两个不是字段：整个时钟、全部野怪击杀
        _resolve(game, path)
    if on and path not in game["locks"]:
        game["locks"].append(path)
    elif not on and path in game["locks"]:
        game["locks"].remove(path)


# ---- 经济 ----

def team_gold(game: dict, side: str) -> int | None:
    """五个人的经济都有就求和（比顶部四舍五入的数准），否则用团队经济那一栏。

    例外：识别服务报告底部面板收起了。这时逐人经济停在收起前的值，团队经济那一栏（来自顶部条）更新。
    """
    golds = [player["gold"] for player in game["players"][side]]
    team = game["teams"][side]["gold"]
    if team is not None and (game.get("vision") or {}).get("panel") is False:
        return team
    return sum(golds) if all(gold is not None for gold in golds) else team


def gold_diff(game: dict) -> int | None:
    blue, red = team_gold(game, "blue"), team_gold(game, "red")
    return None if blue is None or red is None else blue - red


def lane_diffs(game: dict) -> list[int | None]:
    return [
        None if blue["gold"] is None or red["gold"] is None else blue["gold"] - red["gold"]
        for blue, red in zip(game["players"]["blue"], game["players"]["red"])
    ]


def next_lead(previous: str | None, diff: int | None, band: int) -> str | None:
    """带滞回的领先方：差值越过 ±band 才换边，在 0 附近抖动时颜色不闪。"""
    if diff is None:
        return None
    if diff > band:
        return "blue"
    if diff < -band:
        return "red"
    if previous is None:
        return "blue" if diff > 0 else "red" if diff < 0 else None
    return previous


def refresh_lead(game: dict, rules: dict) -> None:
    band = int(rules.get("lead_hysteresis", 0))
    lead = game["lead"]
    lead["team"] = next_lead(lead["team"], gold_diff(game), band)
    lead["lanes"] = [next_lead(prev, diff, band) for prev, diff in zip(lead["lanes"], lane_diffs(game))]


# ---- 野怪与 buff ----

def _seconds(rules: dict, what: str, key: str) -> int | None:
    return parse_clock(rules[what].get(key))


def _kills(game: dict, what: str) -> list[dict]:
    return [event for event in game["events"] if event["what"] == what]


GOLD_LOOKBACK = 2           # Power play 的起点取击杀前这么多秒的经济：击杀瞬间全队到手的那笔赏金也要算进收益
GOLD_HISTORY = 120          # 团队经济留多少秒的记录


def gold_before(game: dict, t: float) -> dict:
    """击杀发生之前双方的团队经济。没有那一刻的记录（刚接上识别、手填的局）就用现在的。"""
    past = [entry for entry in game.get("golds", []) if entry[0] <= t - GOLD_LOOKBACK]
    if past and t - past[-1][0] <= GOLD_LOOKBACK + 10:
        return {"blue": past[-1][1], "red": past[-1][2]}
    return {side: team_gold(game, side) for side in SIDES}


def _holders(game: dict, rules: dict, side: str) -> list[int]:
    """击杀远古巨龙时拿到 buff 的人：活着的（规则允许的话也算上已阵亡的）。"""
    to_dead = rules.get("unverified", {}).get("elder_buff_to_dead", False)
    return [index for index, player in enumerate(game["players"][side]) if player["alive"] or to_dead]


def add_objective(game: dict, rules: dict, what: str, side: str | None, now: int,
                  element: str | None = None, t: float | None = None) -> dict:
    """记一次野怪击杀。t 不给就用当前游戏时间。

    side 为 None = 只知道它被打掉了、还不知道是哪一方（识别服务从原生的刷新牌子上只看得出这么多）。
    这样的记录照常驱动刷新计时；之后再给出阵营，就补在这条记录上，击杀时刻不变。
    """
    if what not in OBJECTIVES or side not in (*SIDES, None):
        raise CommandError(f"没有这个野怪或阵营：{what} / {side}")
    if side is None and what == "dragon":
        raise CommandError("元素亚龙要说明是哪一方拿下的")
    pending = next((event for event in _kills(game, what) if event["side"] is None), None)
    if pending is not None:
        if side is not None:
            pending["side"] = side
            if "buffFull" in pending and rules[what].get("buff_lost_on_death"):
                pending["holders"] = _holders(game, rules, side)
        return pending
    if what == "herald" and _kills(game, "herald"):
        raise CommandError("峡谷先锋已经被击杀过了")
    if what == "dragon" and len(_kills(game, "dragon")) >= rules["dragon"]["max_spawns"]:
        raise CommandError(f"元素亚龙一局只有 {rules['dragon']['max_spawns']} 条，已经记满了")
    t = game_time(game, now) if t is None else float(t)
    game["seq"] += 1
    event = {"id": game["seq"], "t": round(t, 1), "what": what, "side": side, "element": element}
    buff = _seconds(rules, what, "buff") if what in ("baron", "elder") else None
    if buff:
        event["buffFull"] = event["buffEnd"] = event["t"] + buff      # buffEnd 会在持有者全部阵亡时提前
        event["goldStart"] = gold_before(game, event["t"])
        event["goldEnd"] = None
        if side is not None and rules[what].get("buff_lost_on_death"):
            event["holders"] = _holders(game, rules, side)
    game["events"].append(event)
    game["events"].sort(key=lambda item: item["t"])
    return event


SAME_KILL_SECONDS = 40      # 识别报的击杀和已有的记录相隔不到这么久，就当成同一次（导播可能已经手点过了）


def see_dragon(game: dict, rules: dict, side: str, element: str | None, now: int, t: float | None = None) -> bool:
    """识别服务在原生顶部条上看到某一方多了一条元素龙。返回是否改了状态。

    这条龙已经记过（哪怕记在另一方名下——那是导播的决定，不和他抢）就不动；
    导播只点了阵营、没选属性的那条，把属性补上；都不是才新记一条。
    """
    dragons = _kills(game, "dragon")
    if element and any(event["element"] == element for event in dragons):
        return False
    blank = next((event for event in dragons if event["side"] == side and not event["element"]), None)
    if blank is not None:
        blank["element"] = element
        return bool(element)
    if len(dragons) >= rules["dragon"]["max_spawns"]:
        return False
    add_objective(game, rules, "dragon", side, now, element=element, t=t)
    return True


def see_kill(game: dict, rules: dict, what: str, side: str | None, now: int, t: float | None = None) -> bool:
    """识别服务看到先锋 / 男爵 / 远古龙被打掉了；是哪一方它不一定看得出来（side 为 None）。

    这次击杀已经有记录就不再记；记录上还没有阵营而这次带了，就补上。导播定过的阵营不改。
    """
    seen_at = game_time(game, now) if t is None else float(t)
    kills = _kills(game, what)
    same = next((event for event in kills if event["side"] is None or abs(event["t"] - seen_at) < SAME_KILL_SECONDS),
                kills[0] if what == "herald" and kills else None)
    if same is not None and (same["side"] is not None or side is None):
        return False
    add_objective(game, rules, what, side, now, t=t)        # 有待定的那条就补在它上面，没有就新记一条
    return True


def remove_objective(game: dict, event_id: int | None = None) -> dict:
    """撤掉一次野怪击杀。不给 id 就撤最后记的那次。"""
    if not game["events"]:
        raise CommandError("还没有记过野怪击杀")
    event = max(game["events"], key=lambda item: item["id"]) if event_id is None else \
        next((item for item in game["events"] if item["id"] == event_id), None)
    if event is None:
        raise CommandError("找不到这条击杀记录")
    game["events"].remove(event)
    return event


def _buff_window(game: dict, what: str, t: float) -> dict | None:
    """t 落在最近一次击杀的完整 buff 时长内时返回那次击杀（哪怕 buff 已经因为全员阵亡提前结束）。"""
    kills = _kills(game, what)
    return kills[-1] if kills and kills[-1].get("buffFull") and t < kills[-1]["buffFull"] else None


def set_buff_holder(game: dict, what: str, index: int, on: bool, now: int) -> None:
    """手动改“会因阵亡丢失的 buff”还有谁拿着。全丢光时 buff 提前结束。"""
    t = game_time(game, now)
    event = _buff_window(game, what, t)
    if event is None or "holders" not in event:
        raise CommandError("现在没有需要逐人统计的 buff")
    holders = set(event["holders"])
    if on:
        holders.add(index)
    else:
        holders.discard(index)
    event["holders"] = sorted(holders)
    event["buffEnd"] = event["buffFull"] if holders else min(event["buffEnd"], round(t, 1))
    if holders:
        event["goldEnd"] = None


def _player_died(game: dict, rules: dict, side: str, index: int, now: int) -> None:
    for what in ("baron", "elder"):
        event = _buff_window(game, what, game_time(game, now))
        if event and event["side"] == side and index in event.get("holders", []):
            set_buff_holder(game, what, index, False, now)


def objectives_view(game: dict, rules: dict) -> dict:
    """每种野怪下一次刷新的游戏秒（at，None = 不会再刷），以及进行中的 buff。"""
    view = {}

    herald = _kills(game, "herald")
    # 先锋和男爵用同一个坑：男爵刷新时先锋肯定已经不在了，没人记过击杀也不能一直显示“存活”
    view["herald"] = {"at": None if herald else _seconds(rules, "herald", "first_spawn"),
                      "until": _seconds(rules, "baron", "first_spawn"),
                      "by": herald[0]["side"] if herald else None}

    dragons = _kills(game, "dragon")
    limit = rules["dragon"]["max_spawns"]
    if len(dragons) >= limit:
        dragon_at = None
    elif dragons:
        dragon_at = dragons[-1]["t"] + _seconds(rules, "dragon", "respawn")
    else:
        dragon_at = _seconds(rules, "dragon", "first_spawn")
    until = _seconds(rules, "dragon", "despawn")      # 到这个时间还没打掉就消失（正在挨打的话延后到 untilLate）
    if dragon_at is not None and until is not None and dragon_at >= until:
        dragon_at = None
    taken = {side: [event["element"] for event in dragons if event["side"] == side] for side in SIDES}
    need = rules["dragon"].get("soul_requires", limit)
    first = min(dragons, key=lambda event: event["t"]) if dragons else None
    view["dragon"] = {
        # 这一局的龙魂属性 = 第一条小龙的属性（识别在它刷新前就从原生牌子上看到了；没看到就等它被打掉）
        "soulElement": game.get("soulElement") or (first or {}).get("element"),
        "at": dragon_at, "until": until, "untilLate": _seconds(rules, "dragon", "despawn_in_combat"),
        "count": len(dragons), "max": limit, "taken": taken,
        "soul": next((side for side in SIDES if len(taken[side]) >= need), None),
    }

    for what in ("baron", "elder"):
        kills = _kills(game, what)
        last = kills[-1] if kills else None
        view[what] = {
            "at": last["t"] + _seconds(rules, what, "respawn") if last else _seconds(rules, what, "first_spawn"),
            "buff": {
                "side": last["side"], "from": last["t"], "endsAt": last["buffEnd"], "fullEndsAt": last["buffFull"],
                "holders": last.get("holders"),
                "goldStart": last["goldStart"], "goldEnd": last["goldEnd"],
            } if last else None,
        }
    return view


# ---- 定时推进 ----

def tick(game: dict, now: int) -> bool:
    """服务端每秒调几次：记经济曲线，buff 结束时定格 Power play。返回是否需要广播。"""
    if game["status"] != "live":
        return False
    t = game_time(game, now)
    changed = False
    for event in game["events"]:
        if event.get("buffEnd") is not None and event.get("goldEnd") is None and t >= event["buffEnd"]:
            event["goldEnd"] = {side: team_gold(game, side) for side in SIDES}
            changed = True
    diff = gold_diff(game)
    if game["clock"]["running"] and diff is not None:
        second = int(t)
        if not game["curve"] or second > game["curve"][-1][0]:
            game["curve"].append([second, diff])
            golds = game.setdefault("golds", [])
            golds.append([second, team_gold(game, "blue"), team_gold(game, "red")])
            del golds[:-GOLD_HISTORY]
    return changed


# ---- 给前端的视图 ----

def loadout(game: dict, values: dict[str, int]) -> dict:
    """装备价值（Loadout value）：双方身上全部装备（含鞋子、散件）的商店总价，和各占双方总和的百分比（加起来是 100）。
    价值表里没有的装备不算（装备库里的都有）。双方都还没有装备时没有百分比。"""
    total = {side: sum(values.get(item, 0) for player in game["players"][side] for item in player["items"] if item)
             for side in SIDES}
    both = total["blue"] + total["red"]
    blue = round(100 * total["blue"] / both) if both else None
    return {side: {"value": total[side], "share": None if blue is None else (blue if side == "blue" else 100 - blue)}
            for side in SIDES}


def view(game: dict, rules: dict) -> dict:
    objectives = objectives_view(game, rules)
    lanes = lane_diffs(game)
    return {
        "status": game["status"],
        "clock": game["clock"],
        "teams": {
            side: {
                "kills": game["teams"][side]["kills"],
                "towers": game["teams"][side]["towers"],
                "gold": team_gold(game, side),
                "dragons": objectives["dragon"]["taken"][side],
                "soul": objectives["dragon"]["soul"] == side,
            }
            for side in SIDES
        },
        "players": game["players"],
        "diff": gold_diff(game),
        "lead": game["lead"]["team"],
        "lanes": [{"diff": diff, "lead": lead} for diff, lead in zip(lanes, game["lead"]["lanes"])],
        "objectives": objectives,
        "events": game["events"],
        "locks": game["locks"],
        "vision": game.get("vision"),
        "fx": game.get("fx", []),                   # 最近的特效事件（state/effects.py）
    }

