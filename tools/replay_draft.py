#!/usr/bin/env python3
"""把 WRPedia 里录过的一个大场按 BP 顺序重放进正在运行的服务，用来看面板效果、排练导播流程。

    python3 tools/replay_draft.py                 # 最近一个按实际顺位录入的大场，逐手间隔 1.5 秒
    python3 tools/replay_draft.py --match W7M1 --delay 0.3
    python3 tools/replay_draft.py --games 1       # 只放第一局，并且停在阵容确认，不结束这一局

会重置当前的系列赛状态，正式比赛时不要对着直播用的服务跑。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from state.catalog import Catalog, read_js, load_config  # noqa: E402


def post(base, command):
    request = urllib.request.Request(
        base + "/api/command", data=json.dumps(command).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"命令被拒绝：{command} → {json.load(error)['error']}")
    except urllib.error.URLError as error:
        sys.exit(f"连不上服务（{base}）：{error.reason}。先运行 python3 -m server")


def find_match(league, match_id):
    recorded = [
        match for round_ in league["rounds"] for match in round_["matches"]
        if match.get("games") and all(game.get("draftOrder") == "recorded" for game in match["games"])
    ]
    if match_id:
        match = next((m for m in recorded if m["id"] == match_id), None)
        if match is None:
            sys.exit(f"没有按实际顺位录入的大场 {match_id}。可选：{', '.join(m['id'] for m in recorded)}")
        return match
    return recorded[-1]


def main():
    config = load_config("app")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=f"http://127.0.0.1:{config.get('port', 8780)}")
    parser.add_argument("--match", help="WRPedia 的比赛 id，例如 W7M1")
    parser.add_argument("--games", type=int, help="只放前几局；最后一局停在阵容确认")
    parser.add_argument("--delay", type=float, default=1.5, help="每一手之间隔几秒")
    args = parser.parse_args()

    catalog = Catalog(config)
    league = read_js(catalog.wrpedia / "data" / "schedule.js", "window.WR_LEAGUE = ")
    match = find_match(league, args.match)
    teams = {team["id"]: team for team in catalog.teams}
    ids = {"a": match["opponent1"], "b": match["opponent2"]}
    games = match["games"][: args.games] if args.games else match["games"]
    print(f"{match['id']}  {ids['a']} vs {ids['b']}  BO{match['bestOf']}  共 {len(games)} 局")

    def send(command):
        return post(args.base, command)

    send({"type": "series.reset"})
    send({"type": "game.reset"})
    send({"type": "series.update", "patch": {
        "title": league["meta"]["name"], "stage": match["id"], "bestOf": match["bestOf"], "fearless": "global",
        "teams": {key: {k: teams[ids[key]][k] for k in ("id", "name", "short", "logo", "players")} for key in ids},
    }})
    for number, game in enumerate(games, 1):
        sides = {game["team1"]["side"]: "team1", game["team2"]["side"]: "team2"}
        send({"type": "series.update", "patch": {"blue": "a" if sides["blue"] == "team1" else "b"}})
        send({"type": "draft.start"})
        queues = {(side, kind): list(game[sides[side]][kind + "s"]) for side in sides for kind in ("ban", "pick")}
        state = json.load(urllib.request.urlopen(args.base + "/api/state"))
        for turn in state["draft"]["turns"]:
            for _ in turn["slots"]:
                champion = queues[(turn["side"], turn["type"])].pop(0)
                time.sleep(args.delay / 2)
                if champion:
                    send({"type": "draft.hover", "champion": champion})
                    time.sleep(args.delay / 2)
                    send({"type": "draft.lock"})
                else:
                    time.sleep(args.delay / 2)
                    send({"type": "draft.skip"})
        for side, key in sides.items():        # 用 WRPedia 记录的实际分路覆盖预填
            for index, role in enumerate(game[key].get("pickRoles") or []):
                if isinstance(role, str):
                    send({"type": "draft.role", "side": side, "index": index, "role": role})
        time.sleep(args.delay)
        send({"type": "draft.confirm"})
        print(f"  第 {number} 局 BP 完成")
        if args.games and number == len(games):
            break
        time.sleep(args.delay * 2)
        send({"type": "series.finish_game", "winner": "a" if game["winner"] == 1 else "b", "length": game["length"]})


if __name__ == "__main__":
    main()
