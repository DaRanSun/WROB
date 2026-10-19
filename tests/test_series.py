import pytest

from state import CommandError
from state import series as series_mod

from conftest import BLUE_PICKS, RED_PICKS, play_draft

SECOND_BLUE = ["jinx", "lulu", "ahri", "lee-sin", "garen"]
SECOND_RED = ["kaisa", "braum", "orianna", "vi", "fiora"]
SECOND_BANS = (["ezreal", "sivir", "ashe", "zilean", "poppy"], ["ambessa", "xin-zhao", "nunu-and-willump", "yasuo", "zed"])


def finish_first_game(store, winner="a"):
    play_draft(store)
    store.apply({"type": "draft.confirm"}, 0)
    store.apply({"type": "series.finish_game", "winner": winner, "length": "13:09"}, 0)


def test_finish_game_updates_score_and_sides(store):
    finish_first_game(store, winner="a")
    series = store.series
    assert series["score"] == {"a": 1, "b": 0}
    assert series["blue"] == "b"                    # 下一局默认败方蓝方
    assert series["games"][0]["picks"] == {"blue": BLUE_PICKS, "red": RED_PICKS}
    assert series["games"][0]["length"] == "13:09"
    assert store.draft["status"] == "idle"
    snapshot = store.snapshot(0)
    assert snapshot["series"]["gameNumber"] == 2
    assert snapshot["series"]["sides"] == {"blue": "b", "red": "a"}
    assert snapshot["series"]["decided"] is None


def test_series_decided(store):
    finish_first_game(store, "a")
    play_draft(store, picks=(SECOND_BLUE, SECOND_RED), bans=SECOND_BANS)
    store.apply({"type": "series.finish_game", "winner": "a"}, 0)
    snapshot = store.snapshot(0)["series"]
    assert snapshot["decided"] == "a" and snapshot["gameNumber"] == 2      # 打完了，不再显示“第 3 局”


def test_global_fearless_blocks_both_teams(store):
    finish_first_game(store)
    store.apply({"type": "draft.start"}, 0)
    fearless = store.snapshot(0)["draft"]["fearless"]
    assert fearless["bans"] is True
    for side in ("blue", "red"):
        assert set(fearless["sides"][side]) == set(BLUE_PICKS + RED_PICKS)
    with pytest.raises(CommandError, match="第 1 局已选用"):
        store.apply({"type": "draft.lock", "champion": "senna"}, 0)       # 禁用位也不行
    store.apply({"type": "draft.lock", "champion": "ezreal"}, 0)          # 上一局的禁用不跨局


def test_team_fearless_only_blocks_own_picks(store):
    store.apply({"type": "series.update", "patch": {"fearless": "team"}}, 0)
    finish_first_game(store, winner="a")          # 第 1 局 a 在蓝方选了 BLUE_PICKS；第 2 局 a 换到红方
    fearless = series_mod.fearless_blocked(store.series)
    assert fearless["bans"] is False
    assert set(fearless["sides"]["red"]) == set(BLUE_PICKS)
    assert set(fearless["sides"]["blue"]) == set(RED_PICKS)
    store.apply({"type": "draft.start"}, 0)
    store.apply({"type": "draft.lock", "champion": "senna"}, 0)           # 禁用位不受限
    for champion in ["zilean", "vi", "poppy", "sivir", "ashe"]:
        store.apply({"type": "draft.lock", "champion": champion}, 0)
    with pytest.raises(CommandError, match="无畏征召"):
        store.apply({"type": "draft.lock", "champion": "nautilus"}, 0)    # 蓝方现在是 b，b 上一局用过
    store.apply({"type": "draft.lock", "champion": "gwen"}, 0)            # a 用过的，b 可以选


def test_fearless_off(store):
    store.apply({"type": "series.update", "patch": {"fearless": "off"}}, 0)
    finish_first_game(store)
    assert series_mod.fearless_blocked(store.series)["sides"] == {"blue": {}, "red": {}}


def test_undo_finish_game(store):
    finish_first_game(store)
    store.apply({"type": "undo"}, 0)
    assert store.series["score"] == {"a": 0, "b": 0}
    assert store.series["games"] == []
    assert store.draft["status"] == "done"


def test_update_validation(store):
    store.apply({"type": "series.update", "patch": {
        "title": " 测试杯 ", "bestOf": 5, "score": {"a": 2},
        "teams": {"a": {"name": "ThunderTalk", "short": "TT", "players": {"mid": "Z", "nope": "x"}}},
    }}, 0)
    assert store.series["title"] == "测试杯"
    assert store.series["bestOf"] == 5 and store.series["score"] == {"a": 2, "b": 0}
    assert store.series["teams"]["a"]["players"]["mid"] == "Z"
    assert "nope" not in store.series["teams"]["a"]["players"]
    with pytest.raises(CommandError):
        store.apply({"type": "series.update", "patch": {"bestOf": 4}}, 0)
    with pytest.raises(CommandError):
        store.apply({"type": "series.update", "patch": {"fearless": "maybe"}}, 0)


def test_reset_keeps_teams_when_asked(store):
    store.apply({"type": "series.update", "patch": {"teams": {"a": {"name": "TT"}}, "bestOf": 5}}, 0)
    finish_first_game(store)
    store.apply({"type": "series.reset", "keepTeams": True}, 0)
    assert store.series["teams"]["a"]["name"] == "TT" and store.series["bestOf"] == 5
    assert store.series["score"] == {"a": 0, "b": 0} and store.series["games"] == []
    store.apply({"type": "series.reset"}, 0)
    assert store.series["teams"]["a"]["name"] == "队伍 A"


def test_persistence_roundtrip(catalog, rules, tmp_path):
    from state.catalog import load_config
    from state.store import Store

    path = tmp_path / "state.json"
    first = Store(catalog, load_config("app"), load_config("draft"), rules, path=path)
    first.apply({"type": "draft.start"}, 0)
    first.apply({"type": "draft.lock", "champion": "ezreal"}, 0)
    first.save()
    second = Store(catalog, load_config("app"), load_config("draft"), rules, path=path)
    assert second.draft == first.draft and second.series == first.series

    path.write_text("{ not json", encoding="utf-8")
    third = Store(catalog, load_config("app"), load_config("draft"), rules, path=path)
    assert third.draft["status"] == "idle"
    assert (tmp_path / "state.bad.json").is_file()
