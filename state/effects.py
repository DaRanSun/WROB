"""面板特效的触发：连杀、出装提示、野怪 / 龙魂播报。

特效是一条条“事件”，放在 game["fx"] 里（只留最近的 30 条），面板按 id 播放没播过的、而且是刚发生的：

    {"id": 12, "kind": "multikill", "at": 墙上毫秒, "side": "blue", "index": 2, "count": 3}     index 可能是 None（不知道是谁）
    {"id": 13, "kind": "item", "at": …, "side": "red", "index": 3, "items": ["破败王者之刃"]}
    {"id": 14, "kind": "objective", "at": …, "what": "baron", "side": "blue"}             元素龙另带 "element"
    {"id": 15, "kind": "soul", "at": …, "side": "red", "element": "ocean"}               拿到龙魂
    {"id": 16, "kind": "soulElement", "at": …, "element": "infernal"}                    这一局的龙魂属性

多杀以游戏自己的播报为准（识别服务读到“红方三连破”就报 game.multikill）；出装只认识别服务报上来的装备变化
（导播手改装备是在纠错，不该放特效）。导播想手动放，用 fx.play 命令。
开一局、清空之后的几秒、单设备模式下原生面板刚重新展开的几秒里，数字会一下子补上来，这时不放特效。
"""
from __future__ import annotations

from collections import Counter

KEEP = 30
QUIET_MS = 6000             # 开一局 / 清空之后这么久不放特效（识别在这段时间里把数字补回来）
PANEL_QUIET_MS = 3000       # 原生底部面板刚重新展开：收起期间攒下的人头、装备会一下子出来
CATCH_UP_SECONDS = 120      # 装备栏原来整排是空的、游戏已经过了这么久：是在补数，不是刚买的


def push(game: dict, kind: str, now: int, **data) -> dict:
    game["fxSeq"] = game.get("fxSeq", 0) + 1
    event = {"id": game["fxSeq"], "kind": kind, "at": now, **data}
    fx = game.setdefault("fx", [])
    fx.append(event)
    del fx[:-KEEP]
    return event


def hush(game: dict, now: int, ms: int = QUIET_MS) -> None:
    game["fxQuietUntil"] = max(game.get("fxQuietUntil", 0), now + ms)


def quiet(game: dict, now: int) -> bool:
    return now < game.get("fxQuietUntil", 0)


def items_changed(game: dict, legendary: set, side: str, index: int, before: list, after: list, t: float, now: int) -> None:
    """装备栏变了：多出来的传说装（按件数比，不按格子——卖掉一件后其他装备挪位置不算新买的）。"""
    if quiet(game, now) or (not any(before) and t > CATCH_UP_SECONDS):
        return
    added = Counter(item for item in after if item in legendary) - Counter(item for item in before if item in legendary)
    if added:
        push(game, "item", now, side=side, index=index, items=sorted(added.elements(), key=after.index))
