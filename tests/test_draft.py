import pytest

from state import ROLES, CommandError
from state import draft as draft_mod
from state.catalog import load_config

from conftest import BLUE_BANS, BLUE_PICKS, RED_BANS, RED_PICKS, play_draft


def slot(side, kind, index):
    return {"side": side, "type": kind, "index": index}


def test_tournament_order():
    turns, sizes = draft_mod.build_turns(load_config("draft")["formats"]["tournament"])
    order = "".join(("B" if t["side"] == "blue" else "R") + ("b" if t["type"] == "ban" else "p") for t in turns)
    assert order == "BbRbBbRbBbRb" "BpRpRpBpBpRp" "RbBbRbBb" "RpBpBpRp"
    assert sizes == {"ban": 5, "pick": 5}
    # 每一手对应的队内顺位：红方第二轮禁用是它的第 4、5 个禁用位
    assert [t["slots"] for t in turns if t["phase"] == "ban2"] == [[3], [3], [4], [4]]


def test_pair_notation_shares_a_turn():
    turns, _ = draft_mod.build_turns(load_config("draft")["formats"]["tournament-pairs"])
    pick1 = [t for t in turns if t["phase"] == "pick1"]
    assert [(t["side"], t["slots"]) for t in pick1] == [
        ("blue", [0]), ("red", [0, 1]), ("blue", [1, 2]), ("red", [2])]


def test_bad_format_rejected():
    with pytest.raises(ValueError):
        draft_mod.build_turns({"phases": [{"key": "p", "name": "p", "type": "pick", "order": "B R", "seconds": 30}]})


def test_full_draft_reaches_lineup(store):
    play_draft(store)
    draft = store.draft
    assert draft["status"] == "lineup"
    assert [s["champion"] for s in draft["slots"]["blue"]["ban"]] == BLUE_BANS
    assert [s["champion"] for s in draft["slots"]["red"]["ban"]] == RED_BANS
    assert [s["champion"] for s in draft["slots"]["blue"]["pick"]] == BLUE_PICKS
    assert [s["champion"] for s in draft["slots"]["red"]["pick"]] == RED_PICKS
    # 分路按 WRL 历史出场预填，这一局的实际分路正好能全部猜中
    assert [s["role"] for s in draft["slots"]["blue"]["pick"]] == ["bot", "top", "support", "mid", "jungle"]
    assert [s["role"] for s in draft["slots"]["red"]["pick"]] == ["support", "jungle", "mid", "bot", "top"]


def test_timer_restarts_each_turn(store):
    store.apply({"type": "draft.start"}, 1_000)
    assert store.draft["timer"] == {"running": True, "totalMs": 30_000, "endsAt": 31_000, "remainingMs": 30_000}
    store.apply({"type": "draft.lock", "champion": "ezreal"}, 9_000)
    assert store.draft["turn"] == 1
    assert store.draft["timer"]["endsAt"] == 39_000


def test_timer_pause_resume_set(store):
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.timer", "action": "pause"}, 12_000)
    assert draft_mod.timer_remaining(store.draft, 99_000) == 18_000
    store.apply({"type": "draft.timer", "action": "resume"}, 20_000)
    assert draft_mod.timer_remaining(store.draft, 21_000) == 17_000
    store.apply({"type": "draft.timer", "action": "set", "seconds": 5}, 21_000)
    assert draft_mod.timer_remaining(store.draft, 23_000) == 3_000
    assert draft_mod.timer_remaining(store.draft, 60_000) == 0


def test_hover_then_lock(store):
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.hover", "champion": "ahri"}, 0)
    first = store.draft["slots"]["blue"]["ban"][0]
    assert first == {"champion": "ahri", "locked": False, "role": None}
    assert store.draft["turn"] == 0
    store.apply({"type": "draft.lock"}, 0)             # 不带英雄 = 锁定当前预选
    assert first["locked"] and store.draft["turn"] == 1


def test_lock_needs_a_champion(store):
    store.apply({"type": "draft.start"}, 0)
    with pytest.raises(CommandError):
        store.apply({"type": "draft.lock"}, 0)
    with pytest.raises(CommandError):
        store.apply({"type": "draft.lock", "champion": "not-a-champion"}, 0)


def test_duplicate_blocked_unless_forced(store):
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.lock", "champion": "ahri"}, 0)
    with pytest.raises(CommandError, match="本局已禁用"):
        store.apply({"type": "draft.lock", "champion": "ahri"}, 0)
    assert store.draft["turn"] == 1                     # 被拒绝的操作不留痕迹
    store.apply({"type": "draft.lock", "champion": "ahri", "force": True}, 0)
    assert store.draft["turn"] == 2


def test_empty_ban(store):
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.skip"}, 0)
    assert store.draft["slots"]["blue"]["ban"][0] == {"champion": None, "locked": True, "role": None}
    assert store.draft["turn"] == 1
    for _ in range(5):
        store.apply({"type": "draft.skip"}, 0)
    with pytest.raises(CommandError, match="只有禁用位"):
        store.apply({"type": "draft.skip"}, 0)          # 轮到选用了


def test_out_of_order_fill_and_rewrite(store):
    """导播跳着填、回头改，当前手始终是第一个没填完的。"""
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.lock", "champion": "vi", "slot": slot("blue", "ban", 1)}, 0)
    assert store.draft["turn"] == 0
    store.apply({"type": "draft.lock", "champion": "ezreal"}, 0)        # 蓝 1
    store.apply({"type": "draft.lock", "champion": "zilean"}, 0)        # 红 1
    assert store.draft["turn"] == 3                                     # 蓝 2 已经填过，直接到红 2
    store.apply({"type": "draft.lock", "champion": "jinx", "slot": slot("blue", "ban", 1)}, 0)   # 改写
    assert store.draft["slots"]["blue"]["ban"][1]["champion"] == "jinx"
    assert store.draft["turn"] == 3
    store.apply({"type": "draft.lock", "champion": "vi"}, 0)            # vi 被改掉了，可以再用
    assert store.draft["slots"]["red"]["ban"][1]["champion"] == "vi"


def test_clear_steps_back(store):
    play_draft(store)
    assert store.draft["status"] == "lineup"
    store.apply({"type": "draft.clear", "slot": slot("red", "pick", 4)}, 0)
    assert store.draft["status"] == "running"
    assert draft_mod.active_refs(store.draft) == [slot("red", "pick", 4)]
    store.apply({"type": "draft.lock", "champion": "garen"}, 0)
    assert store.draft["status"] == "lineup"
    assert sorted(s["role"] for s in store.draft["slots"]["red"]["pick"]) == sorted(ROLES)


def test_simultaneous_turn(store):
    store.apply({"type": "draft.reset", "format": "tournament-pairs"}, 0)
    store.apply({"type": "draft.start"}, 0)
    for champion in ["ezreal", "zilean", "vi", "poppy", "sivir", "ashe", "senna"]:     # 6 禁 + 蓝 1 选
        store.apply({"type": "draft.lock", "champion": champion}, 0)
    assert draft_mod.active_refs(store.draft) == [slot("red", "pick", 0), slot("red", "pick", 1)]
    store.apply({"type": "draft.lock", "champion": "yone", "slot": slot("red", "pick", 1)}, 5_000)
    assert draft_mod.active_refs(store.draft) == [slot("red", "pick", 0)]
    assert store.draft["timer"]["endsAt"] == 30_000        # 同一手里先锁一个，不重新读秒
    store.apply({"type": "draft.lock", "champion": "ornn"}, 6_000)
    assert draft_mod.active_refs(store.draft) == [slot("blue", "pick", 1), slot("blue", "pick", 2)]
    assert store.draft["timer"]["endsAt"] == 36_000


def test_roles_swap_and_confirm(store):
    play_draft(store)
    store.apply({"type": "draft.role", "side": "blue", "index": 0, "role": "top"}, 0)
    assert [s["role"] for s in store.draft["slots"]["blue"]["pick"]] == ["top", "bot", "support", "mid", "jungle"]
    store.apply({"type": "draft.confirm"}, 0)
    assert store.draft["status"] == "done"
    assert not store.draft["timer"]["running"]
    assert draft_mod.lineup(store.draft, "blue") == {
        "top": "senna", "bot": "gwen", "support": "ornn", "mid": "camille", "jungle": "khazix"}


def test_roles_only_after_picks(store):
    store.apply({"type": "draft.start"}, 0)
    with pytest.raises(CommandError):
        store.apply({"type": "draft.role", "side": "blue", "index": 0, "role": "top"}, 0)
    with pytest.raises(CommandError):
        store.apply({"type": "draft.confirm"}, 0)


def test_guess_roles_without_history():
    assert draft_mod.guess_roles([None] * 5, {}) == list(ROLES)
    stats = {"x": {"support": 1.0}, "y": {"top": 0.6, "mid": 0.4}}
    roles = draft_mod.guess_roles(["x", "y", None, None, None], stats)
    assert roles[0] == "support" and roles[1] == "top" and sorted(roles) == sorted(ROLES)


def test_swap_slots(store):
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.lock", "champion": "ezreal"}, 0)
    store.apply({"type": "draft.swap", "a": slot("blue", "ban", 0), "b": slot("blue", "ban", 2)}, 0)
    assert [s["champion"] for s in store.draft["slots"]["blue"]["ban"][:3]] == [None, None, "ezreal"]
    assert store.draft["turn"] == 0
    with pytest.raises(CommandError):
        store.apply({"type": "draft.swap", "a": slot("blue", "ban", 0), "b": slot("red", "ban", 0)}, 0)


def test_undo(store):
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.lock", "champion": "ezreal"}, 0)
    store.apply({"type": "draft.hover", "champion": "ahri"}, 0)
    store.apply({"type": "draft.lock", "champion": "zilean"}, 0)
    store.apply({"type": "undo"}, 50_000)
    assert store.draft["turn"] == 1
    assert store.draft["slots"]["red"]["ban"][0]["locked"] is False
    assert store.draft["timer"]["endsAt"] == 80_000        # 撤销后这一手重新读秒
    store.apply({"type": "undo"}, 50_000)
    store.apply({"type": "undo"}, 50_000)
    assert store.draft["status"] == "idle"
    with pytest.raises(CommandError):
        store.apply({"type": "undo"}, 50_000)


# ---- 自选模式 ----

def free_store(store):
    store.apply({"type": "draft.reset", "format": "free"}, 0)
    return store


def test_free_format_picks_in_order_without_bans(store):
    draft = free_store(store).draft
    assert draft["free"] and draft["status"] == "idle"
    assert draft["slots"]["blue"]["ban"] == [] and len(draft["slots"]["red"]["pick"]) == 5
    assert all(slot["role"] is None for slot in draft["slots"]["blue"]["pick"])        # 不预设分路
    order = "".join("B" if turn["side"] == "blue" else "R" for turn in draft["turns"])
    assert order == "BRRBBRRBBR" and {turn["type"] for turn in draft["turns"]} == {"pick"}
    assert store.snapshot(0)["draft"]["active"] == []


def test_free_pick_then_fix_roles(store):
    """按选人顺序填，填完猜一套分路；导播对着游戏改分路，确认阵容、开局之后也能改，局内面板跟着换。"""
    free_store(store)
    store.apply({"type": "draft.start"}, 0)
    draft = store.draft
    assert draft["status"] == "running" and draft["timer"]["running"] is False        # 不读秒
    store.apply({"type": "draft.lock", "champion": BLUE_PICKS[0]}, 0)                  # 不给槽位就填轮到的：蓝 1
    assert store.snapshot(0)["draft"]["active"] == [slot("red", "pick", 0)]
    for index, champion in enumerate(RED_PICKS):
        store.apply({"type": "draft.lock", "champion": champion, "slot": slot("red", "pick", index)}, 0)
    for index, champion in enumerate(BLUE_PICKS[1:], start=1):
        store.apply({"type": "draft.lock", "champion": champion, "slot": slot("blue", "pick", index)}, 0)
    draft = store.draft
    assert draft["status"] == "lineup" and draft["timer"]["running"] is False
    assert sorted(slot["role"] for slot in draft["slots"]["blue"]["pick"]) == sorted(ROLES)
    store.apply({"type": "draft.role", "side": "blue", "index": 0, "role": "top"}, 0)
    store.apply({"type": "draft.role", "side": "blue", "index": 3, "role": "bot"}, 0)
    store.apply({"type": "draft.confirm"}, 0)
    store.apply({"type": "game.new"}, 0)
    players = store.game["players"]["blue"]
    assert players[0]["champion"] == BLUE_PICKS[0] and players[3]["champion"] == BLUE_PICKS[3]
    # 开局后发现蓝 1 其实在下路、蓝 4 在上路：改分路，局内面板的上路、下路跟着换
    store.apply({"type": "draft.role", "side": "blue", "index": 0, "role": "bot"}, 0)
    assert players[3]["champion"] == BLUE_PICKS[0] and players[0]["champion"] == BLUE_PICKS[3]


def test_free_pick_swap_and_clear(store):
    free_store(store)
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.lock", "champion": "yone", "slot": slot("blue", "pick", 0)}, 0)
    store.apply({"type": "draft.lock", "champion": "gwen", "slot": slot("blue", "pick", 2)}, 0)
    store.apply({"type": "draft.swap", "a": slot("blue", "pick", 0), "b": slot("blue", "pick", 2)}, 0)
    picks = store.draft["slots"]["blue"]["pick"]
    assert picks[0]["champion"] == "gwen" and picks[2]["champion"] == "yone"
    store.apply({"type": "draft.clear", "slot": slot("blue", "pick", 2)}, 0)
    assert picks[2] == {"champion": None, "locked": False, "role": None}
    with pytest.raises(CommandError, match="本局已选用"):         # 同一个英雄不能两边都有（按住 Shift 可以强行）
        store.apply({"type": "draft.lock", "champion": "gwen", "slot": slot("red", "pick", 0)}, 0)


def test_free_pick_not_confirmed_until_full(store):
    free_store(store)
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.lock", "champion": "gwen"}, 0)
    with pytest.raises(CommandError, match="十个英雄"):
        store.apply({"type": "draft.confirm"}, 0)


def test_free_pick_ignores_fearless(store):
    """无畏征召开着、上一局用过的英雄：赛事征召里不能选，自选模式里照样能填。"""
    store.apply({"type": "series.update", "values": {"fearless": "global"}}, 0)
    play_draft(store)
    store.apply({"type": "draft.confirm"}, 0)
    store.apply({"type": "series.finish_game", "winner": "a"}, 0)
    store.apply({"type": "draft.reset", "format": "free"}, 0)
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.lock", "champion": BLUE_PICKS[0], "slot": slot("blue", "pick", 0)}, 0)
    assert store.draft["slots"]["blue"]["pick"][0]["champion"] == BLUE_PICKS[0]
