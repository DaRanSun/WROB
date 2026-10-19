"""结束本局 · 胜利画面 · 赛后面板（state/postgame.py 和 Store 里的 game.finish / postgame.*）。"""
import pytest

from state import CommandError

from conftest import BLUE_BANS, play_draft

LEFT = [  # 结算页左半：输出 占比 承伤 治疗 占比 参团 经济 占比
    [27409, 29, 11009, 3394, 7, 52, 16151, 25], [17356, 18, 11811, 9180, 20, 38, 12704, 20],
    [33533, 35, 11397, 5760, 12, 57, 14174, 22], [12038, 13, 12208, 2632, 6, 52, 11668, 18],
    [5185, 5, 8280, 25435, 55, 71, 10003, 15]]
RIGHT = [
    [12810, 18, 30976, 3857, 22, 22, 7710, 15], [8420, 12, 13518, 5003, 29, 11, 12343, 23],
    [18194, 26, 13755, 0, 0, 44, 14027, 26], [25351, 36, 17241, 0, 0, 67, 9776, 18],
    [4982, 7, 13571, 8640, 49, 78, 9314, 18]]
KEYS = ("damage", "damageShare", "taken", "healing", "healingShare", "share", "gold", "goldShare")


def rows(table):
    return [dict(zip(KEYS, row)) for row in table]


@pytest.fixture
def live(store):
    """一局打到 18:20：蓝方（队伍 a）拿了两条龙、一个先锋，经济领先。"""
    play_draft(store)
    store.apply({"type": "draft.confirm"}, 0)
    store.apply({"type": "game.new"}, 0)
    store.apply({"type": "game.clock", "action": "start", "seconds": 0}, 0)
    golds = {"blue": [row[6] - 300 for row in LEFT], "red": [row[6] - 300 for row in RIGHT]}
    values = {f"{side}.{i}.gold": gold for side in golds for i, gold in enumerate(golds[side])}
    values.update({"blue.kills": 21, "red.kills": 9, "blue.towers": 9, "red.towers": 2, "blue.0.kills": 6, "red.3.deaths": 4})
    store.apply({"type": "game.set", "values": values}, 1000)
    store.apply({"type": "game.objective", "what": "dragon", "side": "blue", "element": "infernal", "t": 300}, 1000)
    store.apply({"type": "game.objective", "what": "herald", "side": "blue", "t": 480}, 1000)
    store.apply({"type": "game.objective", "what": "dragon", "side": "blue", "element": "ocean", "t": 560}, 1000)
    store.tick(2000)
    return store


def test_finish_freezes_the_game(live):
    store = live
    store.apply({"type": "game.finish", "winner": "a"}, 1100_000)            # 开局后 1100 秒 = 18:20
    assert store.game["status"] == "ended"
    post = store.postgame
    assert post["phase"] == "victory" and post["show"] and post["stats"] is None
    assert post["winner"] == "a" and post["winnerSide"] == "blue"
    assert post["length"] == 1100 and post["n"] == 1
    assert post["score"] == {"blue": 1, "red": 0}
    assert post["bans"]["blue"] == BLUE_BANS
    blue = post["totals"]["blue"]
    assert blue["kills"] == 21 and blue["towers"] == 9 and blue["heralds"] == 1 and blue["dragons"] == ["infernal", "ocean"]
    assert post["totals"]["red"]["deaths"] == 4
    assert post["curve"]                                                  # 经济曲线一起定格
    assert store.series["score"] == {"a": 1, "b": 0}
    assert store.series["games"][0]["length"] == "18:20"
    assert store.draft["status"] == "idle"                                 # 下一局的 BP
    assert store.snapshot(1100_000)["postgame"] is post


def test_vision_is_ignored_after_the_finish(live):
    store = live
    store.apply({"type": "game.finish", "winner": "a"}, 1100_000)
    rev = store.rev
    store.apply({"type": "game.set", "source": "vision", "values": {"blue.0.gold": 99999}}, 1101_000)
    store.apply({"type": "game.clock", "action": "sync", "seconds": 1105, "running": True, "source": "vision"}, 1101_000)
    assert store.game["players"]["blue"][0]["gold"] != 99999
    assert store.game["status"] == "ended" and store.rev == rev
    store.apply({"type": "vision.status", "panel": False, "clock": False, "screen": "stats"}, 1102_000)
    assert store.game["vision"]["screen"] == "stats"                     # 心跳照收：导播台要知道数据机停在哪一页


def test_stats_pop_the_panel(live):
    store = live
    with pytest.raises(CommandError, match="还没有结束本局"):
        store.apply({"type": "postgame.stats", "source": "vision", "left": rows(LEFT), "right": rows(RIGHT)}, 0)
    store.apply({"type": "game.finish", "winner": "a"}, 1100_000)
    store.apply({"type": "postgame.stats", "source": "vision", "left": rows(LEFT), "right": rows(RIGHT)}, 1130_000)
    post = store.postgame
    assert post["phase"] == "stats" and post["statsAt"] == 1130_000
    assert post["stats"]["blue"][0]["damage"] == 27409 and post["stats"]["red"][2]["healing"] == 0
    assert post["totals"]["blue"]["gold"] == sum(row[6] for row in LEFT)      # 用结算页的最终经济
    assert store.series["games"][0]["stats"] == post["stats"]
    # 重复报来的（哪怕不一样）不再改
    store.apply({"type": "postgame.stats", "source": "vision", "left": rows(RIGHT), "right": rows(LEFT)}, 1131_000)
    assert post["stats"]["blue"][0]["damage"] == 27409


def test_stats_sides_follow_the_gold(live):
    """结算页的左半不一定是蓝方：按局内经济对。"""
    store = live
    store.apply({"type": "game.finish", "winner": "b"}, 1100_000)
    store.apply({"type": "postgame.stats", "source": "vision", "left": rows(RIGHT), "right": rows(LEFT)}, 1130_000)
    assert store.postgame["stats"]["blue"][0]["damage"] == 27409
    assert store.postgame["winnerSide"] == "red"


def test_bad_stats_are_refused(live):
    store = live
    store.apply({"type": "game.finish", "winner": "a"}, 1100_000)
    with pytest.raises(CommandError):
        store.apply({"type": "postgame.stats", "left": rows(LEFT)[:4], "right": rows(RIGHT)}, 0)
    assert store.postgame["stats"] is None


def test_undo_a_wrong_finish(live):
    store = live
    store.apply({"type": "game.finish", "winner": "b"}, 1100_000)
    with pytest.raises(CommandError, match="已经结束"):
        store.apply({"type": "game.finish", "winner": "a"}, 1101_000)         # 连点两下不会记成两局
    store.apply({"type": "undo"}, 1102_000)
    assert store.postgame is None and store.series["score"] == {"a": 0, "b": 0}
    assert store.game["status"] == "live" and store.game["clock"]["running"]
    assert store.draft["status"] == "done"                                  # 阵容回来了
    store.apply({"type": "game.finish", "winner": "a"}, 1110_000)
    assert store.series["score"] == {"a": 1, "b": 0}


def test_director_controls_the_display(live):
    store = live
    with pytest.raises(CommandError, match="还没有结束本局"):
        store.apply({"type": "postgame.set", "show": False}, 0)
    store.apply({"type": "game.finish", "winner": "a"}, 1100_000)
    store.apply({"type": "postgame.set", "show": False, "metric": "taken"}, 0)
    assert store.postgame["show"] is False and store.postgame["metric"] == "taken"
    with pytest.raises(CommandError, match="没有这一项"):
        store.apply({"type": "postgame.set", "metric": "kda"}, 0)
    store.apply({"type": "game.new"}, 0)            # 下一局开局：赛后画面清掉
    assert store.postgame is None


def test_finish_without_a_game_uses_the_typed_length(store):
    play_draft(store)
    store.apply({"type": "series.finish_game", "winner": "b", "length": "1832"}, 0)
    assert store.postgame["length"] == 18 * 60 + 32 and store.series["games"][0]["length"] == "1832"
    assert store.postgame["winnerSide"] == "red"


def test_length_stops_where_vision_lost_the_clock(live):
    """基地爆了之后识别读不到时钟，状态中心的时钟还在走；导播晚点了半分钟，时长仍按识别最后看到时钟的时刻算。"""
    store = live
    store.apply({"type": "vision.status", "panel": True, "clock": True}, 1098_000)
    store.apply({"type": "vision.status", "panel": False, "clock": False}, 1100_000)
    store.apply({"type": "vision.status", "panel": False, "clock": False}, 1128_000)
    store.apply({"type": "game.finish", "winner": "a"}, 1130_000)
    assert store.postgame["length"] == 1099
    assert store.series["games"][0]["length"] == "18:19"
