"""赛后：导播点“结束本局 · X 胜”时把这一局定格下来（胜利画面、赛后面板都用它），之后再补上结算页读到的数据。

    postgame = {
      "n": 第几局, "at": 结束的时刻（毫秒）, "statsAt": 读到结算数据的时刻 | None,
      "phase": "victory" | "stats",     读到结算页的数据之前只放胜利画面；读到了才弹赛后面板
      "show": True,                     导播可以把画面收起、再放出来
      "metric": "damage",               赛后面板右边那一栏比哪一项（见 METRICS）
      "winner": "a", "winnerSide": "blue",
      "teams": {"blue": {"key", "name", "short", "logo"}, "red": …},
      "score": {"blue": 1, "red": 0},   记完这一局之后的系列赛比分
      "title", "stage", "bestOf",
      "length": 秒 | None,
      "bans": {"blue": [英雄…], "red": […]},
      "players": {"blue": [{"role", "name", "champion", "kills", "deaths", "assists", "gold"}, …5], "red": […]},
      "totals": {"blue": {"kills", "deaths", "assists", "gold", "towers", "heralds", "dragons": [元素…], "elders", "barons"}, …},
      "curve": [[游戏秒, 蓝方经济 − 红方经济], …],
      "stats": None | {"blue": [行×5], "red": [行×5]},
    }

结算页的一行（上野中下辅的顺序）：{"damage", "damageShare", "taken", "healing", "healingShare", "share", "gold", "goldShare"}，
都是整数；Share 是占全队的百分比，share 是参团率。
"""
from __future__ import annotations

from . import SIDES, CommandError
from . import game as game_mod

STAT_KEYS = ("damage", "damageShare", "taken", "healing", "healingShare", "share", "gold", "goldShare")
METRICS = ("damage", "taken", "healing", "gold")        # 输出、承伤、治疗护盾、经济
CURVE_POINTS = 400      # 赛后面板的经济曲线最多留这么多个点（它跟着每次快照广播；完整的曲线在系列赛记录里）


def thin(curve: list) -> list:
    step = -(-len(curve) // CURVE_POINTS) or 1
    points = [list(point) for point in curve[::step]]
    if curve and points[-1] != list(curve[-1]):
        points.append(list(curve[-1]))
    return points


def build(game: dict, draft: dict, series: dict, rules: dict, winner: str, length: float | None, now: int) -> dict:
    """结束这一局的那一刻。series 要用记结果之前的（红蓝方还没为下一局换过来）。"""
    from .series import team_on       # series 也导入了 draft，放这里免得互相导入

    sides = {side: team_on(series, side) for side in SIDES}
    objectives = game_mod.objectives_view(game, rules)
    players, totals = {}, {}
    for side in SIDES:
        team = series["teams"][sides[side]]
        players[side] = [{key: player[key] for key in ("role", "name", "champion", "kills", "deaths", "assists", "gold")}
                         for player in game["players"][side]]
        for player in players[side]:
            player["name"] = player["name"] or team["players"].get(player["role"], "")
        kills = [event for event in game["events"] if event["side"] == side]
        totals[side] = {
            "kills": max(game["teams"][side]["kills"], sum(player["kills"] for player in players[side])),
            "deaths": sum(player["deaths"] for player in players[side]),
            "assists": sum(player["assists"] for player in players[side]),
            "gold": game_mod.team_gold(game, side),
            "towers": game["teams"][side]["towers"],
            "heralds": sum(event["what"] == "herald" for event in kills),
            "dragons": objectives["dragon"]["taken"][side],
            "elders": sum(event["what"] == "elder" for event in kills),
            "barons": sum(event["what"] == "baron" for event in kills),
        }
    winner_side = next(side for side in SIDES if sides[side] == winner)
    return {
        "n": len(series["games"]) + 1,
        "at": now, "statsAt": None,
        "phase": "victory", "show": True, "metric": "damage",
        "winner": winner, "winnerSide": winner_side,
        "teams": {side: {"key": sides[side], **{key: series["teams"][sides[side]][key] for key in ("name", "short", "logo")}}
                  for side in SIDES},
        "score": {side: series["score"][sides[side]] + (1 if sides[side] == winner else 0) for side in SIDES},
        "title": series.get("title", ""), "stage": series.get("stage", ""), "bestOf": series["bestOf"],
        "length": None if length is None else round(length),
        "bans": {side: [slot["champion"] for slot in draft["slots"][side]["ban"]] for side in SIDES},
        "players": players,
        "totals": totals,
        "curve": thin(game["curve"]),
        "stats": None,
    }


def clean_rows(rows) -> list[dict]:
    """识别发来的一方 5 行，检查成整数。"""
    if not isinstance(rows, list) or len(rows) != 5:
        raise CommandError("结算数据每一方要有 5 行")
    clean = []
    for row in rows:
        try:
            clean.append({key: max(0, int(row[key])) for key in STAT_KEYS})
        except (KeyError, TypeError, ValueError):
            raise CommandError(f"结算数据缺项或不是数字：{row}") from None
    return clean


def place(postgame: dict, left: list[dict], right: list[dict]) -> dict:
    """结算页左右两半各是哪一方。

    结算页把一方画在左边（蓝字）、另一方在右边（红字），但左边不一定是这一局的蓝方。拿每个人的总经济
    和局内最后读到的经济比：哪种对法差得少就是哪种。局内一个经济都没读到（纯手动的局）就当左边是蓝方。
    """
    def distance(rows, side):
        golds = [player["gold"] for player in postgame["players"][side]]
        return sum(abs(row["gold"] - gold) for row, gold in zip(rows, golds) if gold is not None), \
            sum(gold is not None for gold in golds)

    straight = [distance(left, "blue"), distance(right, "red")]
    crossed = [distance(left, "red"), distance(right, "blue")]
    if sum(count for _, count in straight) and sum(cost for cost, _ in crossed) < sum(cost for cost, _ in straight):
        return {"blue": right, "red": left}
    return {"blue": left, "red": right}


def add_stats(postgame: dict, left, right, now: int) -> bool:
    """记下结算页的数据，弹出赛后面板。已经有了就不再改（第一次确认读到的为准）。返回是否改了。"""
    left, right = clean_rows(left), clean_rows(right)
    if postgame["stats"] is not None:
        return False
    postgame["stats"] = place(postgame, left, right)
    postgame["statsAt"] = now
    postgame["phase"] = "stats"
    for side in SIDES:                      # 结算页的总经济是最终数，比局内最后一次读数准
        postgame["totals"][side]["gold"] = sum(row["gold"] for row in postgame["stats"][side])
    return True
