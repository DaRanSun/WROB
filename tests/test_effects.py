"""面板特效的触发：连杀、出装提示、野怪 / 龙魂播报。"""
import pytest

from state import CommandError
from state import game as game_mod


def at(clock):
    return game_mod.parse_clock(clock) * 1000


@pytest.fixture
def live(store):
    store.apply({"type": "game.clock", "action": "start"}, 0)          # 不经过 game.new，没有静默期
    return store


def vision(store, clock, **values):
    store.apply({"type": "game.set", "source": "vision", "values": {key.replace("_", "."): value for key, value in values.items()}}, at(clock))


def fx(store, kind=None):
    return [event for event in store.game.get("fx", []) if kind is None or event["kind"] == kind]


def multikill(store, clock, **fields):
    store.apply({"type": "game.multikill", "source": "vision", **fields}, at(clock))


def test_multikills_follow_the_game_announcements(live):
    """以游戏自己的播报为准：二连破、三连破各放一次；五连破“吞掉”四连破时直接放五杀。"""
    multikill(live, "16:27", side="red", index=1, count=2)
    multikill(live, "16:31", side="red", index=1, count=3)
    multikill(live, "16:40", side="red", index=1, count=5)
    assert [(e["side"], e["index"], e["count"]) for e in fx(live, "multikill")] == [("red", 1, 2), ("red", 1, 3), ("red", 1, 5)]
    multikill(live, "17:00", side="blue", index=None, count=2)          # 识别没找到是谁：照放，放在这一方的半边
    assert fx(live, "multikill")[-1]["index"] is None
    with pytest.raises(CommandError, match="2–5"):
        multikill(live, "17:01", side="blue", index=0, count=6)


def test_director_edits_and_catch_up_do_not_fire(live):
    live.apply({"type": "game.set", "values": {"blue.1.kills": 3}}, at("6:00"))          # 人头数变了不再触发多杀
    vision(live, "6:01", blue_1_kills=5)
    assert fx(live) == []
    live.apply({"type": "game.new"}, at("6:10"))                                            # 开一局：识别把数字补回来
    vision(live, "6:11", red_4_items=["铁板靴", None, None, None, "破败王者之刃", "三相之力"])
    multikill(live, "6:12", side="red", index=4, count=2)                                    # 补数的那几秒里看到的播报也不放
    assert fx(live) == []


def test_legendary_item_flash(live):
    build = ["铁板靴", None, None, None, "长剑", "考尔菲德的战锤"]
    vision(live, "9:00", red_0_items=build)
    assert fx(live) == []                                               # 小件不算
    vision(live, "9:30", red_0_items=["铁板靴", None, None, "长剑", "三相之力", "破败王者之刃"])
    assert fx(live, "item")[-1]["items"] == ["三相之力", "破败王者之刃"]          # 一次出了两件：轮流展示
    vision(live, "10:00", red_0_items=["铁板靴", None, None, "三相之力", "破败王者之刃", "长剑"])
    assert len(fx(live, "item")) == 1                                   # 挪了格子不算新买的


def test_items_appearing_late_are_catch_up(live):
    vision(live, "14:00", blue_3_items=["铁板靴", None, None, "无尽之刃", "岚切", "收集者"])    # 原来整排是空的
    assert fx(live) == []


def test_objective_announcements_and_soul(live):
    live.apply({"type": "game.soul_element", "source": "vision", "element": "infernal"}, at("4:40"))
    assert [(e["kind"], e["element"]) for e in fx(live)] == [("soulElement", "infernal")]
    live.apply({"type": "game.soul_element", "source": "vision", "element": "ocean"}, at("4:41"))       # 已经知道了，识别改不动
    assert live.snapshot(at("4:41"))["game"]["objectives"]["dragon"]["soulElement"] == "infernal"
    for clock, element in (("6:10", "infernal"), ("10:30", "mountain"), ("15:00", "ocean")):
        live.apply({"type": "game.objective", "source": "vision", "what": "dragon", "side": "red", "element": element, "t": game_mod.parse_clock(clock)}, at(clock))
    kinds = [(e["kind"], e.get("side"), e.get("element")) for e in fx(live)][1:]
    assert kinds == [("objective", "red", "infernal"), ("objective", "red", "mountain"), ("objective", "red", "ocean"),
                     ("soul", "red", "infernal")]                       # 龙魂的属性是第一条小龙的
    # 男爵：识别只看出被打掉了，没有播报；导播点了阵营再播
    live.apply({"type": "game.objective", "source": "vision", "what": "baron", "t": 760}, at("12:40"))
    assert fx(live)[-1]["kind"] == "soul"
    live.apply({"type": "game.objective", "what": "baron", "side": "blue"}, at("12:45"))
    assert (fx(live)[-1]["what"], fx(live)[-1]["side"]) == ("baron", "blue")


def test_soul_element_falls_back_to_first_dragon(live):
    live.apply({"type": "game.objective", "what": "dragon", "side": "blue", "element": "ocean"}, at("6:30"))
    assert [e["kind"] for e in fx(live)] == ["objective", "soulElement"]
    assert live.snapshot(at("6:30"))["game"]["objectives"]["dragon"]["soulElement"] == "ocean"


def test_manual_play_and_snapshot(live):
    live.apply({"type": "game.new"}, 0)                                 # 静默期里导播手动放也照样放
    live.apply({"type": "fx.play", "kind": "multikill", "side": "red", "index": 1, "count": 5}, 100)
    event = live.snapshot(100)["game"]["fx"][-1]
    assert (event["kind"], event["count"], event["manual"]) == ("multikill", 5, True)
    with pytest.raises(CommandError):
        live.apply({"type": "fx.play", "kind": "multikill", "side": "red", "index": 1, "count": 7}, 100)
    for i in range(40):
        live.apply({"type": "fx.play", "kind": "soulElement", "element": "ocean"}, 200 + i)
    assert len(live.game["fx"]) == 30                                   # 只留最近的


def test_finished_game_keeps_its_gold_curve(live):
    from conftest import play_draft
    play_draft(live)
    live.apply({"type": "draft.confirm"}, 0)
    live.game["curve"] = [[0, 0], [60, 350], [120, -200]]
    live.apply({"type": "series.finish_game", "winner": "a"}, 0)
    assert live.series["games"][-1]["curve"] == [[0, 0], [60, 350], [120, -200]]

