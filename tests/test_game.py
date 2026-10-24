import json

import pytest

from state import CommandError
from state import game as game_mod
from state.commit import Debouncer, cross_check, plausible_gold_jump

from conftest import play_draft


def at(clock):
    """游戏时间 "6:00" → 对应的墙上毫秒（测试里比赛在 0 毫秒开始计时）。"""
    return game_mod.parse_clock(clock) * 1000


@pytest.fixture
def live(store):
    store.apply({"type": "game.clock", "action": "start"}, 0)
    return store


def objectives(store):
    return game_mod.objectives_view(store.game, store.rules)


def test_parse_helpers():
    assert game_mod.parse_clock("6:00") == 360
    assert game_mod.parse_clock("1830") == 1110
    assert game_mod.parse_clock(45) == 45
    assert game_mod.parse_clock("") is None
    assert game_mod.format_clock(1110) == "18:30"
    assert game_mod.parse_gold("52.3k") == 52300
    assert game_mod.parse_gold("12,345") == 12345
    assert game_mod.parse_gold("") is None
    with pytest.raises(CommandError):
        game_mod.parse_gold("abc")


def test_clock(live):
    assert game_mod.game_time(live.game, 90_000) == 90
    live.apply({"type": "game.clock", "action": "pause"}, 90_000)
    assert game_mod.game_time(live.game, 500_000) == 90
    live.apply({"type": "game.clock", "action": "resume"}, 500_000)
    assert game_mod.game_time(live.game, 510_000) == 100
    live.apply({"type": "game.clock", "action": "set", "seconds": "12:30"}, 510_000)
    assert game_mod.game_time(live.game, 511_000) == 751
    live.apply({"type": "game.clock", "action": "nudge", "seconds": -2}, 511_000)
    assert game_mod.game_time(live.game, 511_000) == 749


def test_initial_spawns(live):
    view = objectives(live)
    assert view["herald"] == {"at": 360, "until": 660, "by": None}
    assert view["dragon"]["at"] == 360 and view["dragon"]["until"] == 1110 and view["dragon"]["untilLate"] == 1130
    assert view["baron"] == {"at": 660, "buff": None}
    assert view["elder"] == {"at": 1140, "buff": None}


def test_herald_never_respawns(live):
    live.apply({"type": "game.objective", "what": "herald", "side": "red"}, at("7:10"))
    assert objectives(live)["herald"] == {"at": None, "until": 660, "by": "red"}
    with pytest.raises(CommandError):
        live.apply({"type": "game.objective", "what": "herald", "side": "blue"}, at("8:00"))


def test_dragons_and_soul(live):
    live.apply({"type": "game.objective", "what": "dragon", "side": "blue", "element": "infernal"}, at("6:30"))
    view = objectives(live)["dragon"]
    assert view["at"] == 390 + 255 and view["count"] == 1 and view["soul"] is None          # 击杀后 4:15 刷新
    live.apply({"type": "game.objective", "what": "dragon", "side": "blue", "element": "ocean"}, at("11:40"))
    live.apply({"type": "game.objective", "what": "dragon", "side": "blue", "element": "cloud"}, at("17:00"))
    view = objectives(live)["dragon"]
    assert view["at"] is None and view["soul"] == "blue"
    assert view["taken"] == {"blue": ["infernal", "ocean", "cloud"], "red": []}
    assert live.snapshot(at("17:00"))["game"]["teams"]["blue"]["soul"] is True
    with pytest.raises(CommandError, match="只有 3 条"):
        live.apply({"type": "game.objective", "what": "dragon", "side": "red"}, at("18:00"))


def test_split_dragons_no_soul(live):
    for clock, side in [("6:30", "blue"), ("11:40", "red"), ("17:00", "blue")]:
        live.apply({"type": "game.objective", "what": "dragon", "side": side}, at(clock))
    assert objectives(live)["dragon"]["soul"] is None


def test_dragon_stops_spawning_at_despawn_time(live):
    live.apply({"type": "game.objective", "what": "dragon", "side": "red"}, at("9:00"))
    live.apply({"type": "game.objective", "what": "dragon", "side": "red"}, at("14:10"))
    assert objectives(live)["dragon"]["at"] == 850 + 255   # 18:25 刷新，赶在 18:30 之前
    live.apply({"type": "game.remove_objective"}, at("14:10"))
    live.apply({"type": "game.objective", "what": "dragon", "side": "red"}, at("14:30"))
    assert objectives(live)["dragon"]["at"] is None        # 14:30 + 4:15 = 18:45，已经过了 18:30，不再刷
    # 18:30 时正在挨打的那条可以拖到 18:50，所以这之间的击杀照常记
    live.apply({"type": "game.objective", "what": "dragon", "side": "blue", "element": "ocean"}, at("18:44"))
    assert objectives(live)["dragon"]["taken"]["blue"] == ["ocean"]


def test_baron_buff_and_respawn(live):
    live.apply({"type": "game.objective", "what": "baron", "side": "red"}, at("12:00"))
    baron = objectives(live)["baron"]
    assert baron["at"] == 720 + 216
    assert baron["buff"]["side"] == "red" and baron["buff"]["endsAt"] == baron["buff"]["fullEndsAt"] == 720 + 120
    assert baron["buff"]["holders"] is None                 # 大龙 buff 阵亡不丢
    live.apply({"type": "game.set", "path": "red.0.alive", "value": False}, at("12:30"))
    assert objectives(live)["baron"]["buff"]["endsAt"] == 840


def test_elder_buff_drops_with_deaths(live):
    live.apply({"type": "game.set", "path": "blue.4.alive", "value": False}, at("19:50"))
    live.apply({"type": "game.objective", "what": "elder", "side": "blue"}, at("20:00"))
    elder = objectives(live)["elder"]
    assert elder["at"] == 1200 + 270
    assert elder["buff"]["holders"] == [0, 1, 2, 3]         # 击杀时已阵亡的辅助没有 buff
    assert elder["buff"]["endsAt"] == 1320
    live.apply({"type": "game.set", "path": "blue.4.alive", "value": True}, at("20:10"))    # 复活不补 buff
    live.apply({"type": "game.set", "path": "blue.1.alive", "value": False}, at("20:20"))
    assert objectives(live)["elder"]["buff"]["holders"] == [0, 2, 3]
    live.apply({"type": "game.set", "path": "red.1.alive", "value": False}, at("20:25"))    # 对面阵亡无关
    assert objectives(live)["elder"]["buff"]["holders"] == [0, 2, 3]
    for index in (0, 2):
        live.apply({"type": "game.buff_holder", "index": index, "on": False}, at("20:30"))
    live.apply({"type": "game.set", "path": "blue.3.alive", "value": False}, at("20:40"))
    elder = objectives(live)["elder"]
    assert elder["buff"]["holders"] == [] and elder["buff"]["endsAt"] == 1240   # 全员丢失，提前结束
    live.apply({"type": "game.buff_holder", "index": 3, "on": True}, at("20:45"))           # 导播点错了，加回来
    assert objectives(live)["elder"]["buff"]["endsAt"] == 1320


def test_remove_objective(live):
    live.apply({"type": "game.objective", "what": "baron", "side": "red"}, at("12:00"))
    live.apply({"type": "game.objective", "what": "dragon", "side": "blue"}, at("12:30"))
    live.apply({"type": "game.remove_objective"}, at("12:31"))            # 撤最后记的
    assert objectives(live)["dragon"]["count"] == 0
    live.apply({"type": "game.remove_objective"}, at("12:32"))
    assert objectives(live)["baron"] == {"at": 660, "buff": None}
    with pytest.raises(CommandError):
        live.apply({"type": "game.remove_objective"}, at("12:33"))


def test_objective_with_explicit_time(live):
    live.apply({"type": "game.objective", "what": "baron", "side": "blue", "t": "11:20"}, at("11:45"))
    assert objectives(live)["baron"]["buff"]["from"] == 680


def test_gold_diff_and_lanes(live):
    assert live.snapshot(0)["game"]["diff"] is None
    live.apply({"type": "game.set", "values": {"blue.gold": "25.4k", "red.gold": 24100}}, 0)
    snapshot = live.snapshot(0)["game"]
    assert snapshot["diff"] == 1300 and snapshot["lead"] == "blue"
    # 五个人的经济填齐后，用求和代替手填的团队经济
    blue, red = [5200, 4800, 5100, 5600, 3300], [5000, 5100, 4700, 5900, 3200]
    values = {f"blue.{i}.gold": g for i, g in enumerate(blue)} | {f"red.{i}.gold": g for i, g in enumerate(red)}
    live.apply({"type": "game.set", "values": values}, 0)
    snapshot = live.snapshot(0)["game"]
    assert snapshot["teams"]["blue"]["gold"] == 24000 and snapshot["teams"]["red"]["gold"] == 23900
    assert snapshot["diff"] == 100
    assert [lane["diff"] for lane in snapshot["lanes"]] == [200, -300, 400, -300, 100]
    assert [lane["lead"] for lane in snapshot["lanes"]] == ["blue", "red", "blue", "red", "blue"]


def test_lead_hysteresis():
    assert game_mod.next_lead(None, 10, 50) == "blue"       # 第一次有数据：直接按正负
    assert game_mod.next_lead("blue", -30, 50) == "blue"    # 没越过滞回带，不换边
    assert game_mod.next_lead("blue", -51, 50) == "red"
    assert game_mod.next_lead("red", 0, 50) == "red"
    assert game_mod.next_lead("red", None, 50) is None
    assert game_mod.next_lead(None, 0, 50) is None


def test_counters_and_locks(live):
    live.apply({"type": "game.add", "path": "blue.kills"}, 0)
    live.apply({"type": "game.add", "path": "blue.kills"}, 0)
    live.apply({"type": "game.add", "path": "blue.kills", "delta": -5}, 0)
    assert live.game["teams"]["blue"]["kills"] == 0         # 不会减到负数
    live.apply({"type": "game.set", "path": "red.towers", "value": 3, "lock": True}, 0)
    live.apply({"type": "game.set", "path": "red.towers", "value": 9, "source": "vision"}, 0)
    assert live.game["teams"]["red"]["towers"] == 3         # 导播锁住的字段，识别改不动
    live.apply({"type": "game.lock", "path": "red.towers", "on": False}, 0)
    live.apply({"type": "game.set", "path": "red.towers", "value": 4, "source": "vision"}, 0)
    assert live.game["teams"]["red"]["towers"] == 4
    with pytest.raises(CommandError):
        live.apply({"type": "game.set", "path": "red.nothing", "value": 1}, 0)
    with pytest.raises(CommandError):
        live.apply({"type": "game.add", "path": "red.0.alive"}, 0)


def test_power_play_freezes_at_buff_end(live):
    live.apply({"type": "game.set", "values": {"blue.gold": 40000, "red.gold": 38000}}, at("12:00"))
    live.apply({"type": "game.objective", "what": "baron", "side": "blue"}, at("12:00"))
    assert objectives(live)["baron"]["buff"]["goldStart"] == {"blue": 40000, "red": 38000}
    live.apply({"type": "game.set", "values": {"blue.gold": 44500, "red.gold": 39000}}, at("13:30"))
    assert live.tick(at("13:59")) is False
    assert live.tick(at("14:00")) is True                   # buff 结束：定格
    live.apply({"type": "game.set", "values": {"blue.gold": 50000}}, at("14:30"))
    assert objectives(live)["baron"]["buff"]["goldEnd"] == {"blue": 44500, "red": 39000}
    assert live.tick(at("14:31")) is False


def test_curve_samples_once_per_game_second(live):
    live.apply({"type": "game.set", "values": {"blue.gold": 1000, "red.gold": 900}}, 0)
    for now in (1_000, 1_250, 1_500, 2_000, 2_250):
        live.tick(now)
    live.apply({"type": "game.clock", "action": "pause"}, 2_500)
    live.tick(9_000)                                        # 暂停时不记
    assert live.game["curve"] == [[1, 100], [2, 100]]


def test_game_new_fills_lineup(store):
    store.apply({"type": "series.update", "patch": {"teams": {
        "a": {"players": {"top": "Y1ze", "jungle": "Jiangzhi", "mid": "Soldier", "bot": "lin11", "support": "Awei"}}}}}, 0)
    play_draft(store)
    store.apply({"type": "draft.confirm"}, 0)
    store.apply({"type": "game.new"}, 0)
    blue = store.game["players"]["blue"]
    assert [(p["role"], p["name"], p["champion"]) for p in blue] == [
        ("top", "Y1ze", "gwen"), ("jungle", "Jiangzhi", "khazix"), ("mid", "Soldier", "camille"),
        ("bot", "lin11", "senna"), ("support", "Awei", "ornn")]
    assert store.game["status"] == "live" and not store.game["clock"]["running"]


# ---- 去抖提交 ----

def test_debouncer_needs_two_equal_reads():
    gold = Debouncer(monotonic=True)
    assert gold.offer(500) is None
    assert gold.offer(500) == 500
    assert gold.offer(500) is None                          # 没变化不重复提交
    assert gold.offer(560) is None
    assert gold.offer(5600) is None                         # 中途读成别的：重新计数
    assert gold.offer(560) is None
    assert gold.offer(560) == 560


def test_debouncer_decrease_needs_three():
    gold = Debouncer(monotonic=True)
    gold.offer(3000), gold.offer(3000)
    assert gold.offer(2400) is None
    assert gold.offer(2400) is None
    assert gold.offer(2400) == 2400                         # 卖装备：连续 3 次才认
    kda = Debouncer()
    kda.offer("2/1/3"), kda.offer("2/1/3")
    kda.offer("2/1/2")
    assert kda.offer("2/1/2") == "2/1/2"                    # 非单调量照常 2 次


def test_debouncer_unreadable_resets():
    value = Debouncer()
    value.offer(7)
    value.offer(None)
    assert value.offer(7) is None
    assert value.offer(7) == 7


def test_gold_jump_and_cross_check():
    assert plausible_gold_jump(5000, 5300, 0.2, kda_changed=False)
    assert not plausible_gold_jump(5000, 15300, 0.2, kda_changed=False)     # 多读了一位
    assert plausible_gold_jump(5000, 6500, 0.2, kda_changed=True)           # 刚拿了人头
    assert plausible_gold_jump(None, 500, 0.2, kda_changed=False)
    assert cross_check([5000] * 5, 25000, [1, 0, 2, 3, 0], 6) == []
    assert cross_check([5000] * 5, 26000, [1, 0, 2, 3, 0], 7) == ["gold", "kills"]
    assert cross_check([5000, None, 5000, 5000, 5000], 99999, [1, None, 0, 0, 0], 9) == []   # 数据不全时不判


def test_respawn_and_alive_go_together(live):
    set_ = lambda path, value, t: live.apply({"type": "game.set", "path": path, "value": value}, at(t))
    player = live.game["players"]["red"][3]
    set_("red.3.respawn", 22, "10:55")
    assert player["alive"] is False and player["respawn"] == 22
    set_("red.3.respawn", 21, "10:56")
    assert player["alive"] is False and player["respawn"] == 21
    set_("red.3.respawn", 0, "11:17")                   # 0 和空都表示活着
    assert player["alive"] is True and player["respawn"] is None
    # 导播只点“阵亡 / 复活”、不填秒数也行
    set_("red.3.alive", False, "12:00")
    assert player["alive"] is False and player["respawn"] is None
    set_("red.3.respawn", 30, "12:01")
    set_("red.3.alive", True, "12:05")
    assert player["alive"] is True and player["respawn"] is None


def test_respawn_drops_the_elder_buff(live):
    live.apply({"type": "game.objective", "what": "elder", "side": "blue"}, at("19:30"))
    assert len(objectives(live)["elder"]["buff"]["holders"]) == 5
    live.apply({"type": "game.set", "source": "vision", "values": {"blue.2.respawn": 35}}, at("19:40"))
    assert objectives(live)["elder"]["buff"]["holders"] == [0, 1, 3, 4]
    live.apply({"type": "game.set", "source": "vision", "values": {"blue.2.respawn": 34}}, at("19:41"))
    assert objectives(live)["elder"]["buff"]["holders"] == [0, 1, 3, 4]          # 倒计时往下走不算再死一次


def test_items(live, catalog):
    assert len(catalog.items) == 177 and "潜行药剂" not in catalog.items and "放血者的诅咒" in catalog.items      # 176 件来自掌盟截图，1 件后来补的（extra）
    assert live.game["players"]["blue"][0]["items"] == [None] * 6
    build = ["铁板靴", None, None, "长剑", "破舰者", "黄昏与黎明"]
    live.apply({"type": "game.set", "path": "blue.0.items", "value": build}, 0)
    assert live.game["players"]["blue"][0]["items"] == build
    with pytest.raises(CommandError, match="装备库里没有"):
        live.apply({"type": "game.set", "path": "blue.0.items", "value": ["无尽之刃大宝剑"] + build[1:]}, 0)
    with pytest.raises(CommandError, match="6 格"):
        live.apply({"type": "game.set", "path": "blue.0.items", "value": build[:4]}, 0)
    # 导播锁住之后，识别写不进去
    live.apply({"type": "game.set", "path": "blue.0.items", "value": build, "lock": True}, 0)
    live.apply({"type": "game.set", "source": "vision", "values": {"blue.0.items": [None] * 6}}, 0)
    assert live.game["players"]["blue"][0]["items"] == build
    # 每一件装备都有图标
    from state.catalog import ROOT
    assert not [name for name in catalog.items if not (ROOT / "assets" / "items" / f"{name}.png").is_file()]


# ---- 识别服务报上来的野怪击杀 ----

def seen(store, clock, **fields):
    store.apply({"type": "game.objective", "source": "vision", "t": game_mod.parse_clock(clock), **fields}, at(clock))


def test_vision_dragons_merge_with_the_director(live):
    seen(live, "7:39", what="dragon", side="blue", element="ocean")
    seen(live, "7:39", what="dragon", side="blue", element="ocean")                 # 重复报不重复记
    assert objectives(live)["dragon"]["taken"] == {"blue": ["ocean"], "red": []}
    assert objectives(live)["dragon"]["at"] == 459 + 255
    # 导播先手点了红方、没选属性：识别把属性补上，不再多记一条
    live.apply({"type": "game.objective", "what": "dragon", "side": "red"}, at("12:15"))
    seen(live, "12:16", what="dragon", side="red", element="mountain")
    assert objectives(live)["dragon"]["taken"] == {"blue": ["ocean"], "red": ["mountain"]}
    # 导播把火龙记给了蓝方：识别看到的是红方，也不跟导播抢
    live.apply({"type": "game.objective", "what": "dragon", "side": "blue", "element": "infernal"}, at("16:50"))
    seen(live, "16:51", what="dragon", side="red", element="infernal")
    assert objectives(live)["dragon"]["count"] == 3 and objectives(live)["dragon"]["taken"]["blue"] == ["ocean", "infernal"]


def test_kill_with_side_pending(live):
    """识别只看得出男爵在 12:00 被打掉了：刷新计时照走，buff 等导播点了是哪一方才显示，起点仍是 12:00。"""
    seen(live, "12:00", what="baron")
    baron = objectives(live)["baron"]
    assert baron["at"] == 720 + 216 and baron["buff"]["side"] is None
    seen(live, "12:01", what="baron")                                               # 同一次，不重复记
    assert len([event for event in live.game["events"] if event["what"] == "baron"]) == 1
    live.apply({"type": "game.objective", "what": "baron", "side": "red"}, at("12:07"))
    baron = objectives(live)["baron"]
    assert baron["buff"]["side"] == "red" and baron["buff"]["from"] == 720 and baron["buff"]["endsAt"] == 720 + 120
    assert len(live.game["events"]) == 1
    # 导播已经手点过的击杀，识别晚几秒报上来不会再记一条
    live.apply({"type": "game.objective", "what": "baron", "side": "blue"}, at("16:00"))
    seen(live, "16:02", what="baron")
    assert [event["side"] for event in live.game["events"]] == ["red", "blue"]


def test_vision_kill_with_a_side(live):
    """实时 OB 里识别能从时间轴看出是哪一方：直接记全；之前只记了“被打掉”的，把阵营补上；导播定过的不改。"""
    seen(live, "7:09", what="herald", side="red")
    assert objectives(live)["herald"]["by"] == "red"
    seen(live, "7:10", what="herald", side="blue")                                  # 先锋只有一只
    assert objectives(live)["herald"]["by"] == "red"
    seen(live, "11:29", what="baron")
    seen(live, "11:30", what="baron", side="blue")
    baron = objectives(live)["baron"]["buff"]
    assert baron["side"] == "blue" and baron["from"] == 689
    live.apply({"type": "game.objective", "what": "baron", "side": "red"}, at("15:57"))
    seen(live, "15:58", what="baron", side="blue")                                  # 导播已经记给红方了
    assert [event["side"] for event in live.game["events"] if event["what"] == "baron"] == ["blue", "red"]


def test_pending_elder_gets_holders_when_assigned(live):
    seen(live, "19:30", what="elder")
    assert objectives(live)["elder"]["buff"]["holders"] is None
    live.apply({"type": "game.set", "path": "blue.4.alive", "value": False}, at("19:31"))
    live.apply({"type": "game.objective", "what": "elder", "side": "blue"}, at("19:33"))
    assert objectives(live)["elder"]["buff"]["holders"] == [0, 1, 2, 3]


def test_objectives_lock_keeps_vision_out(live):
    live.apply({"type": "game.lock", "path": "objectives", "on": True}, 0)
    seen(live, "7:39", what="dragon", side="blue", element="ocean")
    seen(live, "12:00", what="baron")
    assert live.game["events"] == []
    with pytest.raises(CommandError, match="哪一方"):
        live.apply({"type": "game.objective", "what": "baron"}, at("12:00"))


def test_spells_rune_and_cooldowns(live):
    player = live.game["players"]["red"][2]
    assert player["spells"] == [None, None] and player["rune"] is None and player["cooldowns"] == {}
    live.apply({"type": "game.set", "source": "vision", "values": {
        "red.2.spells": ["闪现", "引燃"], "red.2.rune": "相位猛冲", "red.2.cooldowns": {"spell.0": [530, 680], "item.4": [600.5, None]}}}, 0)
    assert player["spells"] == ["闪现", "引燃"] and player["rune"] == "相位猛冲"
    assert player["cooldowns"] == {"spell.0": [530.0, 680.0], "item.4": [600.5, None]}
    with pytest.raises(CommandError, match="两个"):
        live.apply({"type": "game.set", "path": "red.2.spells", "value": ["闪现"]}, 0)


def test_power_play_counts_the_kill_bounty(live):
    """击杀男爵的瞬间全队每人到手一笔赏金。识别报上来时这笔钱已经到账了，所以起点要取击杀之前的经济。"""
    set_gold = lambda blue, red, clock: [live.apply({"type": "game.set", "values": {
        **{f"blue.{i}.gold": blue for i in range(5)}, **{f"red.{i}.gold": red for i in range(5)}}}, at(clock)),
        game_mod.tick(live.game, at(clock))]
    set_gold(8000, 9000, "13:20")
    for second in range(21, 34):                                    # 13:21–13:33 每秒一个点，双方每秒各涨 3
        set_gold(8000 + (second - 20) * 3, 9000 + (second - 20) * 3, f"13:{second}")
    set_gold(8042, 9042 + 120, "13:34")                             # 红方打掉男爵：每人 +120
    live.apply({"type": "game.objective", "source": "vision", "what": "baron", "side": "red", "t": 813.5}, at("13:35"))
    start = objectives(live)["baron"]["buff"]["goldStart"]
    assert start == {"blue": 5 * 8033, "red": 5 * 9033}             # 13:31 的经济：赏金到账之前
    assert 5 * 9162 - start["red"] - (5 * 8042 - start["blue"]) == 600      # 这 600 现在算在红方的打龙收益里
    assert len(live.game["golds"]) == 15


def test_level_and_ult(live):
    player = live.game["players"]["blue"][3]
    assert player["level"] is None and player["ult"] is None
    live.apply({"type": "game.set", "source": "vision", "values": {"blue.3.level": 6, "blue.3.ult": 0.35}}, 0)
    assert player["level"] == 6 and player["ult"] == 0.35
    live.apply({"type": "game.set", "values": {"blue.3.level": 99, "blue.3.ult": 3}}, 0)
    assert player["level"] == 15 and player["ult"] == 1.0


def test_display_settings(store):
    assert store.ui == {"mode": "single", "buffs": True, "matchups": True, "chart": False, "teamfight": False,
                        "loadout": False, "effects": True, "autoBp": True}
    store.apply({"type": "ui.set", "values": {"mode": "dual", "buffs": 0, "chart": True}}, 0)
    assert store.snapshot(0)["ui"] == {"mode": "dual", "buffs": False, "matchups": True, "chart": True, "teamfight": False,
                                       "loadout": False, "effects": True, "autoBp": True}
    with pytest.raises(CommandError, match="single"):
        store.apply({"type": "ui.set", "values": {"mode": "triple"}}, 0)
    with pytest.raises(CommandError, match="没有这个显示设置"):
        store.apply({"type": "ui.set", "values": {"objectives": True}}, 0)      # 刷新计时牌已经取消了


def test_teamfight_ui(store):
    """团战 UI 只在双设备下有；和经济曲线占同一块地方，打开一个就收起另一个。"""
    with pytest.raises(CommandError, match="双设备"):
        store.apply({"type": "ui.set", "values": {"teamfight": True}}, 0)
    assert store.ui["teamfight"] is False
    store.apply({"type": "ui.set", "values": {"mode": "dual", "chart": True}}, 0)
    store.apply({"type": "ui.set", "values": {"teamfight": True}}, 0)               # T：经济曲线让位
    assert (store.ui["teamfight"], store.ui["chart"]) == (True, False)
    store.apply({"type": "ui.set", "values": {"chart": True}}, 0)                   # E：团战 UI 让位
    assert (store.ui["teamfight"], store.ui["chart"]) == (False, True)
    store.apply({"type": "ui.set", "values": {"teamfight": True}}, 0)
    store.apply({"type": "ui.set", "values": {"teamfight": False}}, 0)              # 再按一次 T：回到对位面板
    assert (store.ui["teamfight"], store.ui["chart"]) == (False, False)
    with pytest.raises(CommandError, match="不能同时"):
        store.apply({"type": "ui.set", "values": {"teamfight": True, "chart": True}}, 0)
    assert (store.ui["teamfight"], store.ui["chart"]) == (False, False)             # 被拒的命令一项都不改
    store.apply({"type": "ui.set", "values": {"teamfight": True}}, 0)
    store.apply({"type": "ui.set", "values": {"buffs": False}}, 0)                  # 别的开关不碰它
    assert store.ui["teamfight"] is True
    store.apply({"type": "ui.set", "values": {"mode": "single"}}, 0)                # 换回单设备就收起
    assert store.ui["teamfight"] is False


def test_teamfight_ui_saved(catalog, rules, tmp_path):
    from state.catalog import load_config
    from state.store import Store

    path = tmp_path / "state.json"
    first = Store(catalog, load_config("app"), load_config("draft"), rules, path=path)
    first.apply({"type": "ui.set", "values": {"mode": "dual", "teamfight": True}}, 0)
    first.save()
    assert Store(catalog, load_config("app"), load_config("draft"), rules, path=path).ui["teamfight"] is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["ui"]["mode"] = "single"                     # 手改过的存档：单设备却开着团战 UI
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert Store(catalog, load_config("app"), load_config("draft"), rules, path=path).ui["teamfight"] is False


def test_key_items(catalog):
    """团战 UI 的关键装备：会读冷却条的（冷却超过 6 秒），去掉卢登和两双三级鞋。"""
    keys = set(catalog.public()["keyItems"])
    assert {"中娅沙漏", "守护天使", "水银弯刀", "芬布尔之冬"} <= keys
    assert not {"卢登的回声", "带链碾碎者", "装甲战靴"} & keys      # 用户点名去掉
    assert "炽天使之拥" not in keys                     # 手游原生界面不显示它的状态，本来就不读条
    assert not {"星蚀", "耀光", "三相之力"} & keys       # 冷却 6 秒以内的不读条，也就不算
    assert keys <= set(catalog.items)


def test_item_values(catalog):
    """装备价值表（config/loadout.yaml）：装备库里每一件都有价值；进化装备和进化前的一样。"""
    values = catalog.item_values
    assert set(values) == set(catalog.items)
    assert (values["天启戒指"], values["速度之靴"], values["中娅沙漏"], values["三相之力"], values["守望者铠甲"]) == (300, 400, 3300, 3333, 1050)
    for evolved, base in [("歌之权冠", "耳语头环"), ("炽天使之拥", "大天使之杖"), ("芬布尔之冬", "冬之降临"),
                          ("魔切", "魔宗"), ("山脉壁垒", "圣物之盾"), ("黑雾巨镰", "幽魂镰刀")]:
        assert values[evolved] == values[base]


def test_loadout(store):
    """双方身上全部装备（含鞋子、散件）的价值总和，和各占的百分比（加起来 100）。"""
    view = store.snapshot(0)["game"]["loadout"]
    assert view == {"blue": {"value": 0, "share": None}, "red": {"value": 0, "share": None}}      # 都还没出装：没有百分比
    store.apply({"type": "game.set", "values": {
        "blue.0.items": ["速度之靴", "长剑", None, None, None, None],                        # 400 + 500
        "blue.3.items": ["狂战士胫甲", "无尽之刃", "歌之权冠", None, None, None],             # 1200 + 3400 + 2400（进化前是耳语头环）
        "red.2.items": ["明朗之靴", "中娅沙漏", "卢登的回声", "增幅典籍", None, None],       # 1000 + 3300 + 2800 + 500
    }}, 0)
    view = store.snapshot(0)["game"]["loadout"]
    assert view["blue"]["value"] == 900 + 7000 and view["red"]["value"] == 7600
    assert (view["blue"]["share"], view["red"]["share"]) == (51, 49)                   # 7900 / 15500 = 50.97%
    assert view["blue"]["share"] + view["red"]["share"] == 100


def test_loadout_ui(store):
    """装备价值只在双设备下有；和团战 UI、经济曲线互不影响；换回单设备时收起。"""
    with pytest.raises(CommandError, match="装备价值只用于双设备"):
        store.apply({"type": "ui.set", "values": {"loadout": True}}, 0)
    store.apply({"type": "ui.set", "values": {"mode": "dual", "loadout": True}}, 0)
    store.apply({"type": "ui.set", "values": {"teamfight": True}}, 0)
    store.apply({"type": "ui.set", "values": {"chart": True}}, 0)
    assert (store.ui["loadout"], store.ui["teamfight"], store.ui["chart"]) == (True, False, True)
    store.apply({"type": "ui.set", "values": {"mode": "single"}}, 0)
    assert store.ui["loadout"] is False
