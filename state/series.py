"""系列赛：两支队伍、比分、已打完的小局、无畏征召。

队伍用 "a" / "b" 标识，和红蓝方无关；series["blue"] 记当前这一局谁在蓝方。

    series = {
      "title", "stage", "patch", "bestOf", "fearless": "off" | "team" | "global",
      "brand", "brandLogo",                 局内面板角上的赛事标识：文字、图片地址（都可以空）
      "teams": {"a": team, "b": team},      team = {"id", "name", "short", "logo", "players": {分路: 名字}}
      "score": {"a": 0, "b": 0},
      "blue": "a",
      "games": [{"n", "blue", "winner", "length", "bans", "picks", "roles"}, …],
    }
"""
from __future__ import annotations

from . import ROLES, SIDES, CommandError
from . import draft as draft_mod

TEAM_KEYS = ("a", "b")
OTHER_TEAM = {"a": "b", "b": "a"}
FEARLESS_MODES = ("off", "team", "global")
BEST_OF = (1, 3, 5, 7)


def blank_team(name: str) -> dict:
    return {"id": "", "name": name, "short": name, "logo": "", "players": {role: "" for role in ROLES}}


def new_series(defaults: dict) -> dict:
    return {
        "title": defaults.get("title", ""),
        "stage": defaults.get("stage", ""),
        "brand": str(defaults.get("brand", "") or ""),
        "brandLogo": "",
        "patch": str(defaults.get("patch", "") or ""),
        "bestOf": defaults.get("best_of", 3),
        "fearless": defaults.get("fearless", "global"),
        "teams": {"a": blank_team("队伍 A"), "b": blank_team("队伍 B")},
        "score": {"a": 0, "b": 0},
        "blue": "a",
        "games": [],
    }


def team_on(series: dict, side: str) -> str:
    return series["blue"] if side == "blue" else OTHER_TEAM[series["blue"]]


def side_of(series: dict, team: str) -> str:
    return "blue" if series["blue"] == team else "red"


def wins_needed(series: dict) -> int:
    return series["bestOf"] // 2 + 1


def decided(series: dict) -> str | None:
    """已经拿下系列赛的队伍，没有则为 None。"""
    return next((team for team in TEAM_KEYS if series["score"][team] >= wins_needed(series)), None)


def fearless_blocked(series: dict) -> dict:
    """按当前红蓝方整理出无畏征召锁住的英雄。

    返回 {"mode", "bans": 是否连禁用位也不能放, "sides": {"blue": {slug: 原因}, "red": {…}}}。
    global（双边无畏）：任何一方选过的，双方都不能再选，也没必要再禁。
    team：只锁本队自己选过的，对面还能选，所以禁用位不受限。
    """
    mode = series["fearless"]
    sides = {side: {} for side in SIDES}
    if mode != "off":
        for game in series["games"]:
            reason = f"无畏征召：第 {game['n']} 局已选用"
            for then_side in SIDES:
                picker = game["blue"] if then_side == "blue" else OTHER_TEAM[game["blue"]]
                for champion in game["picks"][then_side]:
                    for side in SIDES:
                        if champion and (mode == "global" or team_on(series, side) == picker):
                            sides[side].setdefault(champion, reason)
    return {"mode": mode, "bans": mode == "global", "sides": sides}


def update(series: dict, patch: dict) -> None:
    """导播台改赛事信息。只接受认识的字段，其余忽略。"""
    for key in ("title", "stage", "patch", "brand", "brandLogo"):
        if key in patch:
            series[key] = str(patch[key] or "").strip()
    if "bestOf" in patch:
        if patch["bestOf"] not in BEST_OF:
            raise CommandError("赛制只支持 BO1 / BO3 / BO5 / BO7")
        series["bestOf"] = patch["bestOf"]
    if "fearless" in patch:
        if patch["fearless"] not in FEARLESS_MODES:
            raise CommandError("无畏征召只能是 off / team / global")
        series["fearless"] = patch["fearless"]
    if "blue" in patch:
        if patch["blue"] not in TEAM_KEYS:
            raise CommandError("蓝方只能是队伍 a 或 b")
        series["blue"] = patch["blue"]
    for team, score in (patch.get("score") or {}).items():
        if team in TEAM_KEYS:
            series["score"][team] = max(0, int(score))
    for team, fields in (patch.get("teams") or {}).items():
        if team not in TEAM_KEYS:
            continue
        target = series["teams"][team]
        for key in ("id", "name", "short", "logo"):
            if key in fields:
                target[key] = str(fields[key] or "").strip()
        for role, name in (fields.get("players") or {}).items():
            if role in ROLES:
                target["players"][role] = str(name or "").strip()


def finish_game(series: dict, draft: dict, winner: str, length: str = "") -> dict:
    """记下这一局的结果和 BP，比分加一。下一局默认败方在蓝方，导播可以再改。"""
    if winner not in TEAM_KEYS:
        raise CommandError("胜者只能是队伍 a 或 b")
    record = {
        "n": len(series["games"]) + 1,
        "blue": series["blue"],
        "winner": winner,
        "length": str(length or "").strip(),
        **draft_mod.summary(draft),
    }
    series["games"].append(record)
    series["score"][winner] += 1
    series["blue"] = OTHER_TEAM[winner]
    return record


def history_view(series: dict) -> list[dict]:
    """给前端画“已用英雄”：每局按队伍（而不是红蓝方）列出选用。"""
    rows = []
    for game in series["games"]:
        by_team = {}
        for side in SIDES:
            team = game["blue"] if side == "blue" else OTHER_TEAM[game["blue"]]
            by_team[team] = {"side": side, "picks": game["picks"][side], "bans": game["bans"][side]}
        rows.append({"n": game["n"], "winner": game["winner"], "length": game["length"], "teams": by_team})
    return rows
