import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state.catalog import Catalog, load_config  # noqa: E402
from state.store import Store  # noqa: E402


@pytest.fixture(scope="session")
def catalog():
    return Catalog(load_config("app"))


@pytest.fixture(scope="session")
def rules():
    return load_config("rules")


@pytest.fixture
def store(catalog, rules):
    """不落盘的状态中心，用真实的 config/ 和 WRPedia 资料。"""
    return Store(catalog, load_config("app"), load_config("draft"), rules, path=None)


# 一局真实的 BP（WRL 第 7 周 FP vs ACE 第 1 局），按队内顺位。
BLUE_BANS = ["ezreal", "vi", "ambessa", "sivir", "ashe"]
RED_BANS = ["zilean", "nunu-and-willump", "poppy", "lee-sin", "xin-zhao"]
BLUE_PICKS = ["senna", "gwen", "ornn", "camille", "khazix"]
RED_PICKS = ["nautilus", "chogath", "yone", "yunara", "renekton"]


def play_draft(store, picks=(BLUE_PICKS, RED_PICKS), bans=(BLUE_BANS, RED_BANS), now=0, start=True):
    """按流程顺序把一整局 BP 锁完。"""
    if start:
        store.apply({"type": "draft.start"}, now)
    queues = {("blue", "ban"): list(bans[0]), ("red", "ban"): list(bans[1]),
              ("blue", "pick"): list(picks[0]), ("red", "pick"): list(picks[1])}
    while store.draft["status"] == "running":
        turn = store.draft["turns"][store.draft["turn"]]
        champion = queues[(turn["side"], turn["type"])].pop(0)
        store.apply({"type": "draft.lock", "champion": champion}, now)
