"""状态中心：持有全部状态，执行导播台（以后还有识别服务）发来的命令，落盘，生成给前端的快照。

命令是 {"type": "draft.lock", …} 这样的 dict，全部列在 Store.COMMANDS 里。
被拒绝的操作抛 CommandError，消息会原样显示在导播台上。
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

from . import SIDES, CommandError
from . import draft as draft_mod
from . import effects as fx_mod
from . import game as game_mod
from . import postgame as postgame_mod
from . import series as series_mod

UNDO_DEPTH = 100
# 局内面板的显示设置。
#   mode     画面模式。single = 单设备：面板直接盖在这台手机自己的画面上。原生的选手卡片看得见，所以底部面板不重复
#            显示等级、召唤师技能和大招；原生的底部面板收起时（打团时为了视野），我们的底部面板跟着收起。
#            dual = 双设备：观战机推纯净画面，面板显示全部内容。
#   buffs    男爵 / 远古龙 buff 的剩余时间和 Power play。（野怪的刷新倒计时原生界面都有，面板不再显示。）
#   matchups 底部的对位面板。
#   chart    经济曲线：导播插入时顶替底部面板，收起后面板回来。
#   teamfight 团战 UI（只用于双设备）：顶替底部面板，十个人一人一栏，只留召唤师技能、大招、关键装备、等级、血条。
#            和经济曲线互相顶替（打开一个就收起另一个），两个都收起就是对位面板。
#   loadout  装备价值（只用于双设备）：底部两侧各一块，双方身上装备的价值总和和各占的百分比。团战 UI 开着时不管它都显示。
#   effects  连杀、出装提示、野怪 / 龙魂播报这些自动特效（导播手动放的不受它管）。
#   autoBp   画面识别自动填 BP（数据机停在选人界面时）。关掉后识别发来的 BP 一概不收，全部手填。
DEFAULT_UI = {"mode": "single", "buffs": True, "matchups": True, "chart": False, "teamfight": False, "loadout": False,
              "effects": True, "autoBp": True}
UI_MODES = ("single", "dual")
# 一局结束后识别发来的命令里，只收这些：结算页的数据，和下一局的 BP（下一局的选人界面出现时，上一局还挂着赛后画面）
VISION_AFTER_END = ("postgame.stats", "draft.start", "draft.hover", "draft.lock", "draft.skip")
VISION_ALIVE_MS = 6000      # 识别每 2 秒报一次到；超过这么久没消息就当它没在跑
HEARTBEAT_LAG = 1.0         # 报到隔 2 秒一次，“时钟读不到了”平均晚报 1 秒


def ui_value(key: str, value):
    if key not in DEFAULT_UI:
        raise CommandError(f"没有这个显示设置：{key}")
    if key == "mode":
        if value not in UI_MODES:
            raise CommandError("画面模式只有 single（单设备）和 dual（双设备）")
        return value
    return bool(value)


DUAL_ONLY = {"teamfight": "团战 UI 只用于双设备模式", "loadout": "装备价值只用于双设备模式"}      # 单设备没有放它们的地方


def settle_ui(ui: dict) -> dict:
    """底部那块地方同一时间只放一样：团战 UI 和经济曲线不会同时开着。团战 UI、装备价值只在双设备下有。"""
    if ui["chart"]:
        ui["teamfight"] = False
    if ui["mode"] != "dual":
        ui.update({key: False for key in DUAL_ONLY})
    return ui


def now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    def __init__(self, catalog, app_config: dict, draft_config: dict, rules: dict, path: Path | None = None):
        self.catalog = catalog
        self.defaults = app_config.get("defaults") or {}
        self.formats = draft_config["formats"]
        self.default_format = draft_config.get("default") or next(iter(self.formats))
        for key, fmt in self.formats.items():
            draft_mod.build_turns(fmt)          # 配置写错就在启动时报出来
        self.rules = rules
        from .catalog import load_config            # 这里才用到，避免和 catalog 互相导入
        self.effects = load_config("effects")
        self.path = path
        self.rev = 0
        self.dirty = False
        self.history: list[dict] = []           # BP / 系列赛的撤销栈
        self.series = series_mod.new_series(self.defaults)
        self.draft = self._new_draft()
        self.game = game_mod.new_game()
        self.postgame = None                    # 导播结束一局之后的胜利画面 / 赛后面板（state/postgame.py）
        self.ui = dict(DEFAULT_UI)              # 面板上各个部件的显示开关
        self._load()

    # ---- 落盘 ----

    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.series, self.draft, self.game = saved["series"], saved["draft"], saved["game"]
            self.series.setdefault("brand", str(self.defaults.get("brand", "") or ""))     # 旧存档里没有角标
            self.series.setdefault("brandLogo", "")
            self.postgame = saved.get("postgame")
            for players in self.game["players"].values():           # 旧存档里选手没有装备和复活这两项
                for player in players:
                    player.setdefault("items", [None] * game_mod.ITEM_SLOTS)
                    player.setdefault("respawn", None)
                    player.setdefault("spells", [None, None])
                    player.setdefault("rune", None)
                    player.setdefault("cooldowns", {})
                    player.setdefault("level", None)
                    player.setdefault("ult", None)
                    player.setdefault("bars", None)
            self.ui = dict(DEFAULT_UI)
            for key, value in (saved.get("ui") or {}).items():          # 旧存档里已经不用的开关、不认识的模式都丢掉
                if key in DEFAULT_UI and (key != "mode" or value in UI_MODES):
                    self.ui[key] = ui_value(key, value)
            self.ui = settle_ui(self.ui)
        except (ValueError, KeyError) as error:
            # 存档坏了不能挡住开播：留个副本，从空白状态开始。
            self.path.replace(self.path.with_suffix(".bad.json"))
            print(f"[store] 存档读不出来（{error}），已另存为 {self.path.with_suffix('.bad.json').name}，从空白开始")

    def save(self) -> None:
        if not self.path or not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        payload = {"savedAt": now_ms(), "series": self.series, "draft": self.draft, "game": self.game,
                   "postgame": self.postgame, "ui": self.ui}
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)
        self.dirty = False

    # ---- 快照 ----

    def snapshot(self, now: int | None = None) -> dict:
        now = now_ms() if now is None else now
        series = self.series
        decided = series_mod.decided(series)
        return {
            "rev": self.rev,
            "now": now,
            "series": {
                **series,
                # 系列赛打完后没有“下一局”，停在最后一局的局数上
                "gameNumber": len(series["games"]) + (0 if decided and series["games"] else 1),
                "winsNeeded": series_mod.wins_needed(series),
                "decided": decided,
                "sides": {side: series_mod.team_on(series, side) for side in SIDES},
                "history": series_mod.history_view(series),
            },
            "draft": {
                **self.draft,
                "active": draft_mod.active_refs(self.draft),
                "used": draft_mod.used_this_game(self.draft),
                "fearless": series_mod.fearless_blocked(series),
            },
            "game": {**game_mod.view(self.game, self.rules), "loadout": game_mod.loadout(self.game, self.catalog.item_values)},
            "postgame": self.postgame,
            "ui": self.ui,
            "canUndo": bool(self.history),
        }

    # ---- 命令 ----

    def apply(self, command: dict, now: int | None = None) -> None:
        now = now_ms() if now is None else now
        handler = self.COMMANDS.get(command.get("type"))
        if handler is None:
            raise CommandError(f"不认识的命令：{command.get('type')}")
        name, undoable = handler
        if command.get("source") == "vision" and self.game["status"] == "ended" and command["type"] not in VISION_AFTER_END:
            return          # 导播已经结束了这一局：识别再读到的局内数据一概不收（游戏里可能还在放结算动画）
        before = self._bp_state() if undoable else None
        try:
            getattr(self, name)(command, now)
        except Exception as error:              # 直播中不能因为一条坏命令崩掉，所以这里兜住所有异常
            if before is not None:
                self._restore(before)           # 执行到一半出错：恢复原状
            if isinstance(error, CommandError):
                raise
            raise CommandError(f"命令参数不对：{error!r}") from error
        if before is not None and before != self._bp_state():
            self.history.append(before)
            del self.history[:-UNDO_DEPTH]
        self.rev += 1
        self.dirty = True

    def tick(self, now: int | None = None) -> bool:
        """定时推进。返回是否需要广播。"""
        now = now_ms() if now is None else now
        curve_length = len(self.game["curve"])
        changed = game_mod.tick(self.game, now)
        if changed:
            self.rev += 1
        if changed or len(self.game["curve"]) != curve_length:
            self.dirty = True
        return changed

    def _bp_state(self) -> dict:
        return copy.deepcopy({"series": self.series, "draft": self.draft, "postgame": self.postgame})

    def _restore(self, saved: dict) -> None:
        self.series, self.draft = saved["series"], saved["draft"]
        self.postgame = saved.get("postgame")

    def _new_draft(self, key: str | None = None) -> dict:
        key = key or self.default_format
        if key not in self.formats:
            raise CommandError(f"config/draft.yaml 里没有这个 BP 流程：{key}")
        return draft_mod.new_draft(key, self.formats[key])

    def _champion(self, slug) -> str | None:
        if slug in (None, ""):
            return None
        if slug not in self.catalog.champions:
            raise CommandError(f"没有这个英雄：{slug}")
        return slug

    @property
    def _role_stats(self) -> dict:
        return self.catalog.role_stats

    # 系列赛

    def _series_update(self, cmd, now):
        series_mod.update(self.series, cmd.get("patch") or {})

    def _series_swap_sides(self, cmd, now):
        self.series["blue"] = series_mod.OTHER_TEAM[self.series["blue"]]

    def _game_finish(self, cmd, now):
        """结束本局 · 某队胜：记比分和 BP，定格这一局的数据，局内面板放胜利画面，停收识别的局内数据。

        时长不填就用比赛时钟。导播台“BP”页的结果按钮也走这里。点错了可以撤销（Ctrl+Z）：比分、BP、赛后画面都回去，
        局内也接着收识别的数据。
        """
        winner = cmd.get("winner")
        if winner not in series_mod.TEAM_KEYS:
            raise CommandError("胜者只能是队伍 a 或 b")
        if (self.postgame and self.game["status"] == "ended" and self.draft["status"] == "idle"
                and self.postgame["n"] == len(self.series["games"])):
            raise CommandError(f"第 {self.postgame['n']} 局已经结束、记过结果了；点错了胜方请先撤销")
        game = self.game
        played = game["status"] in ("live", "ended") and (game["clock"]["running"] or game["clock"]["baseSec"] > 0)
        typed = str(cmd.get("length") or "").strip()
        try:
            length = game_mod.parse_clock(typed) if typed else (game_mod.game_time(game, now) if played else None)
        except ValueError:
            raise CommandError(f"看不懂这个时长：{typed}（写 18:32 或 1832）") from None
        vision = game.get("vision") or {}
        if not typed and length is not None and game.get("clockLostAt") is not None \
                and now - vision.get("at", 0) < VISION_ALIVE_MS and not vision.get("clock"):
            # 基地爆了之后游戏界面收起，识别读不到时钟，状态中心的时钟却还在走：时长取识别最后一次看到时钟的时刻
            length = min(length, game["clockLostAt"])
        if game["status"] == "live":
            self._game_end(cmd, now)
        self.postgame = postgame_mod.build(game, self.draft, self.series, self.rules, winner, length, now)
        record = series_mod.finish_game(self.series, self.draft, winner,
                                        typed or ("" if length is None else game_mod.format_clock(length)))
        if game["curve"]:
            record["curve"] = list(game["curve"])               # 这一局的经济曲线留档（下一局开始时局内数据会清空）
        self.draft = self._new_draft(self.draft["format"])

    def _postgame_stats(self, cmd, now):
        """识别在结算页“统计 · 数据对比”上读到了双方十个人的数据。left / right 是画面上的左右两半。"""
        if self.postgame is None:
            raise CommandError("还没有结束本局，结算数据先不收")
        if postgame_mod.add_stats(self.postgame, cmd.get("left"), cmd.get("right"), now):
            record = next((game for game in self.series["games"] if game["n"] == self.postgame["n"]), None)
            if record is not None:
                record["stats"] = self.postgame["stats"]

    def _postgame_set(self, cmd, now):
        """导播收起 / 再放出胜利画面和赛后面板，换赛后面板右边比的那一项。"""
        if self.postgame is None:
            raise CommandError("还没有结束本局")
        if "show" in cmd:
            self.postgame["show"] = bool(cmd["show"])
        if "metric" in cmd:
            if cmd["metric"] not in postgame_mod.METRICS:
                raise CommandError(f"赛后面板没有这一项：{cmd['metric']}")
            self.postgame["metric"] = cmd["metric"]

    def _series_reset(self, cmd, now):
        teams, blue = self.series["teams"], self.series["blue"]
        kept = {key: self.series.get(key, "") for key in ("title", "stage", "patch", "bestOf", "fearless", "brand", "brandLogo")}
        self.series = series_mod.new_series(self.defaults)
        self.series.update(kept)
        if cmd.get("keepTeams"):
            self.series["teams"], self.series["blue"] = teams, blue
        self.draft = self._new_draft(self.draft["format"])

    # BP

    def _vision_bp(self, cmd, now) -> bool:
        """识别（vision/bp.py）发来的 BP 命令要不要执行。导播关了自动 BP、槽位不存在、槽位已经锁定
        （导播填过、或者识别自己填过）都不执行，也不报错——识别只填空着的或者只是预选的槽位，导播改过的不会被改回去。"""
        if cmd.get("source") != "vision":
            return True
        if not self.ui.get("autoBp", True) or self.draft.get("custom"):     # 自定义模式游戏里没有，不接识别
            return False
        ref = cmd.get("slot")
        if ref is not None:
            try:
                if draft_mod.get_slot(self.draft, ref)["locked"]:
                    return False
            except CommandError:
                return False
        if self.draft["status"] == "idle":     # 导播还没点开始：识别看到了选人界面，就开始
            draft_mod.start(self.draft, now)
        return True

    def _draft_start(self, cmd, now):
        if cmd.get("source") == "vision":
            if self.ui.get("autoBp", True) and self.draft["status"] == "idle" and not self.draft.get("custom"):
                draft_mod.start(self.draft, now)
            return
        if cmd.get("format") and self.draft["status"] == "idle":
            self.draft = self._new_draft(cmd["format"])
        draft_mod.start(self.draft, now)

    def _draft_hover(self, cmd, now):
        if not self._vision_bp(cmd, now):
            return
        draft_mod.hover(self.draft, self._champion(cmd.get("champion")), cmd.get("slot"))

    def _draft_lock(self, cmd, now):
        if not self._vision_bp(cmd, now):
            return
        draft_mod.lock(
            self.draft, series_mod.fearless_blocked(self.series), self._role_stats, now,
            champion=self._champion(cmd.get("champion")), ref=cmd.get("slot"), force=bool(cmd.get("force")),
        )

    def _draft_skip(self, cmd, now):
        if not self._vision_bp(cmd, now):
            return
        draft_mod.skip(self.draft, self._role_stats, now, cmd.get("slot"))

    def _draft_clear(self, cmd, now):
        draft_mod.clear(self.draft, self._role_stats, now, cmd["slot"])

    def _draft_swap(self, cmd, now):
        draft_mod.swap(self.draft, cmd["a"], cmd["b"])
        if self.draft.get("custom"):        # 自定义模式分路留在格子上：交换就是改了这两路的英雄
            self._lineup_to_game()

    def _draft_role(self, cmd, now):
        draft_mod.set_role(self.draft, cmd["side"], int(cmd["index"]), cmd["role"])
        self._lineup_to_game()

    def _lineup_to_game(self):
        """开局之后才发现分路不对（自选模式最常见）：局内面板每一路的英雄跟着换。识别按游戏里的分路逐行读，不用动。"""
        if self.draft["status"] == "done" and self.game["status"] == "live":
            for side in SIDES:
                champions = draft_mod.lineup(self.draft, side)
                for player in self.game["players"][side]:
                    player["champion"] = champions.get(player["role"], player["champion"])

    def _draft_confirm(self, cmd, now):
        draft_mod.confirm_lineup(self.draft)

    def _draft_timer(self, cmd, now):
        if cmd.get("source") == "vision" and (not self.ui.get("autoBp", True) or self.draft["status"] != "running"
                                              or self.draft.get("custom")):
            return          # 识别按游戏里的读秒对表；导播关了自动 BP、已经在调分路（不读秒）、自定义模式时不动
        action = cmd.get("action")
        if action == "pause":
            draft_mod.timer_pause(self.draft, now)
        elif action == "resume":
            draft_mod.timer_resume(self.draft, now)
        elif action == "reset":
            draft_mod.timer_reset(self.draft, now)
        elif action == "set":
            draft_mod.timer_set(self.draft, float(cmd["seconds"]), now)
        else:
            raise CommandError(f"不认识的计时操作：{action}")

    def _draft_reset(self, cmd, now):
        self.draft = self._new_draft(cmd.get("format") or self.draft["format"])

    def _undo(self, cmd, now):
        if not self.history:
            raise CommandError("没有可以撤销的操作")
        finished = self.postgame
        self._restore(self.history.pop())
        draft_mod.timer_reset(self.draft, now)      # 撤销后这一手重新读秒
        if finished is not None and self.postgame is None and self.game["status"] == "ended":
            # 撤销的是“结束本局”（点错了 / 点早了）：比赛接着打，识别的数据接着收，时钟等识别来对表
            self.game["status"] = "live"
            game_mod.clock_resume(self.game, now)

    # 局内

    def _game_new(self, cmd, now):
        """按确认后的阵容开一局：把选手名和英雄填进 5 路。"""
        self.game = game_mod.new_game()
        self.game["status"] = "live"
        self.postgame = None
        fx_mod.hush(self.game, now)
        for side in SIDES:
            team = self.series["teams"][series_mod.team_on(self.series, side)]
            champions = draft_mod.lineup(self.draft, side)
            for player in self.game["players"][side]:
                player["name"] = team["players"].get(player["role"], "")
                player["champion"] = champions.get(player["role"])

    def _game_clock(self, cmd, now):
        action = cmd.get("action")
        if action == "start":
            self.game["status"] = "live"
            game_mod.clock_set(self.game, game_mod.parse_clock(cmd.get("seconds")) or 0, now, running=True)
        elif action == "pause":
            game_mod.clock_pause(self.game, now)
        elif action == "resume":
            game_mod.clock_resume(self.game, now)
        elif action == "set":
            seconds = game_mod.parse_clock(cmd.get("seconds"))
            if seconds is None:
                raise CommandError("请填要对到的游戏时间，例如 12:30")
            game_mod.clock_set(self.game, seconds, now)
        elif action == "nudge":
            game_mod.clock_set(self.game, game_mod.game_time(self.game, now) + float(cmd["seconds"]), now)
        elif action == "sync":
            # 识别服务对表：直接给出当前游戏时间和走 / 停。导播把时钟锁住之后，识别改不动。
            if cmd.get("source") == "vision" and "clock" in self.game["locks"]:
                return
            running = bool(cmd.get("running", True))
            game_mod.clock_set(self.game, float(cmd["seconds"]), now, running=running)
            if running and self.game["status"] == "idle":
                self.game["status"] = "live"
        else:
            raise CommandError(f"不认识的时钟操作：{action}")

    def _game_set(self, cmd, now):
        source = "vision" if cmd.get("source") == "vision" else "director"
        values = cmd.get("values") or {cmd["path"]: cmd.get("value")}
        watched = {}                    # 识别报上来的装备：改之前的样子，改完比一比放出装提示
        if source == "vision":
            for path in values:
                parts = str(path).split(".")
                if len(parts) == 3 and parts[2] == "items" and parts[0] in SIDES:
                    watched[(parts[0], int(parts[1]))] = list(self.game["players"][parts[0]][int(parts[1])]["items"])
            if watched and (self.game.get("vision") or {}).get("panel") is False:
                fx_mod.hush(self.game, now, fx_mod.PANEL_QUIET_MS)  # 面板收起期间不会有逐人数据：这是刚展开在补数
        for path, value in values.items():
            if path.endswith(".champion"):
                value = self._champion(value)
            elif path.endswith(".items") and isinstance(value, (list, tuple)):
                unknown = [item for item in value if item and item not in self.catalog.items]
                if unknown:
                    raise CommandError(f"装备库里没有：{'、'.join(map(str, unknown))}")
            game_mod.set_field(self.game, self.rules, path, value, now, source=source, lock=cmd.get("lock"))
        if watched:
            self._player_fx(watched, now, cmd.get("t"))

    def _player_fx(self, watched: dict, now: int, t: float | None = None) -> None:
        # 识别会带上读到这些数时的游戏时间；没带就按状态中心自己的时钟算
        t = float(t) if isinstance(t, (int, float)) else game_mod.game_time(self.game, now)
        legendary = self.catalog.legendary
        for (side, index), items in watched.items():
            player = self.game["players"][side][index]
            if player["items"] != items:
                fx_mod.items_changed(self.game, legendary, side, index, items, player["items"], t, now)

    def _game_multikill(self, cmd, now):
        """识别读到了游戏自己的多杀播报（“红方三连破”）。index 是识别猜的主人，可能是 None。"""
        side, count, index = cmd.get("side"), int(cmd.get("count") or 0), cmd.get("index")
        if side not in SIDES or not 2 <= count <= 5:
            raise CommandError("多杀要给出哪一方和几连破（2–5）")
        if index is not None and not 0 <= int(index) < len(self.game["players"][side]):
            raise CommandError(f"没有这一路：{index}")
        if fx_mod.quiet(self.game, now):
            return
        fx_mod.push(self.game, "multikill", now, side=side, index=None if index is None else int(index), count=count)

    def _objective_state(self) -> dict:
        view = game_mod.objectives_view(self.game, self.rules)
        return {"sides": {event["id"]: event["side"] for event in self.game["events"]},
                "soul": view["dragon"]["soul"], "element": view["dragon"]["soulElement"]}

    def _objective_fx(self, before: dict, now: int) -> None:
        """野怪击杀记上了阵营（新记的，或者给“归属待定”的那条补上了阵营）、拿到龙魂、知道了龙魂属性：播报。"""
        after = self._objective_state()
        if fx_mod.quiet(self.game, now):
            return
        for event in self.game["events"]:
            if event["side"] and before["sides"].get(event["id"]) is None:
                fx_mod.push(self.game, "objective", now, what=event["what"], side=event["side"], element=event.get("element"))
        if after["element"] and after["element"] != before["element"]:
            fx_mod.push(self.game, "soulElement", now, element=after["element"])
        if after["soul"] and after["soul"] != before["soul"]:
            fx_mod.push(self.game, "soul", now, side=after["soul"], element=after["element"])

    def _game_soul_element(self, cmd, now):
        """这一局的龙魂属性（= 第一条小龙的属性）。识别在第一条小龙刷新前从原生牌子上看到；导播也可以手填。"""
        element = cmd.get("element") or None
        if element and element not in self.rules["dragon"]["elements"]:
            raise CommandError(f"没有这种元素龙：{element}")
        if cmd.get("source") == "vision" and self.game.get("soulElement"):
            return
        before = self._objective_state()
        self.game["soulElement"] = element
        self._objective_fx(before, now)

    def _fx_play(self, cmd, now):
        """导播手动放一个特效（预览美术，或者识别漏了的时候补上）。"""
        kind = cmd.get("kind")
        data = {key: cmd[key] for key in ("side", "index", "count", "items", "what", "element") if cmd.get(key) is not None}
        if kind == "multikill" and not 2 <= int(data.get("count", 0)) <= 5:
            raise CommandError("连杀数是 2–5")
        if kind not in ("multikill", "item", "objective", "soul", "soulElement"):
            raise CommandError(f"没有这种特效：{kind}")
        fx_mod.push(self.game, kind, now, manual=True, **data)

    def _game_add(self, cmd, now):
        path = cmd["path"]
        current = game_mod.get_field(self.game, path)
        if not isinstance(current, int) or isinstance(current, bool):
            raise CommandError(f"这个字段不能加减：{path}")
        game_mod.set_field(self.game, self.rules, path, current + int(cmd.get("delta", 1)), now)

    def _game_lock(self, cmd, now):
        game_mod.set_lock(self.game, cmd["path"], bool(cmd.get("on")))

    def _game_objective(self, cmd, now):
        before = self._objective_state()
        self._record_objective(cmd, now)
        self._objective_fx(before, now)

    def _record_objective(self, cmd, now):
        t = cmd.get("t") if isinstance(cmd.get("t"), (int, float)) else game_mod.parse_clock(cmd.get("t"))
        what, side, element = cmd.get("what"), cmd.get("side") or None, cmd.get("element") or None
        if cmd.get("source") == "vision":
            # 识别报上来的击杀：导播说了“野怪由我手动控制”就不收；已经记过的不重复记
            if "objectives" in self.game["locks"]:
                return
            if what == "dragon":
                game_mod.see_dragon(self.game, self.rules, side, element, now, t=t)
            else:
                game_mod.see_kill(self.game, self.rules, what, side, now, t=t)
            return
        if side is None:
            raise CommandError("要说明是哪一方拿下的")
        game_mod.add_objective(self.game, self.rules, what, side, now, element=element, t=t)

    def _game_remove_objective(self, cmd, now):
        game_mod.remove_objective(self.game, cmd.get("id"))

    def _game_buff_holder(self, cmd, now):
        game_mod.set_buff_holder(self.game, cmd.get("what", "elder"), int(cmd["index"]), bool(cmd.get("on")), now)

    def _game_end(self, cmd, now):
        game_mod.clock_pause(self.game, now)
        self.game["status"] = "ended"

    def _game_reset(self, cmd, now):
        self.game = game_mod.new_game()
        self.postgame = None
        fx_mod.hush(self.game, now)

    def _vision_status(self, cmd, now):
        before = self.game.get("vision") or {}
        if before.get("panel") is False and cmd.get("panel"):
            fx_mod.hush(self.game, now, fx_mod.PANEL_QUIET_MS)      # 原生面板刚重新展开：攒下的人头、装备会一下子补上来
        if self.game["status"] == "live":                       # 时钟从读得到变成读不到的那一刻（结束本局时算时长用）
            if cmd.get("clock"):
                self.game.pop("clockLostAt", None)
            elif before.get("clock") and self.game.get("clockLostAt") is None:
                self.game["clockLostAt"] = round(max(0.0, game_mod.game_time(self.game, now) - HEARTBEAT_LAG), 1)
        self.game["vision"] = {"at": now, "panel": bool(cmd.get("panel")), "clock": bool(cmd.get("clock")),
                               "screen": cmd.get("screen")}           # "stats" = 结算页的数据对比；"bp" = 选人界面
        game_mod.refresh_lead(self.game, self.rules)        # 面板收起 / 展开会换团队经济的来源

    def _ui_set(self, cmd, now):
        values = {key: ui_value(key, value) for key, value in (cmd.get("values") or {}).items()}
        ui = dict(self.ui, **values)
        for key, refusal in DUAL_ONLY.items():
            if values.get(key) and ui["mode"] != "dual":
                raise CommandError(refusal)
        if values.get("teamfight"):
            if values.get("chart"):
                raise CommandError("经济曲线和团战 UI 占同一块地方，不能同时打开")
            ui["chart"] = False             # 打开团战 UI 时收起经济曲线；反过来由 settle_ui 收起团战 UI
        self.ui.update(settle_ui(ui))

    # 命令名 → (方法, 是否进撤销栈)。撤销只管 BP 和系列赛；局内数值一直在变，用各自的“撤掉上一条”。
    COMMANDS = {
        "series.update": ("_series_update", True),
        "series.swap_sides": ("_series_swap_sides", True),
        "series.finish_game": ("_game_finish", True),          # 旧名字，和 game.finish 一样
        "series.reset": ("_series_reset", True),
        "draft.start": ("_draft_start", True),
        "draft.hover": ("_draft_hover", False),
        "draft.lock": ("_draft_lock", True),
        "draft.skip": ("_draft_skip", True),
        "draft.clear": ("_draft_clear", True),
        "draft.swap": ("_draft_swap", True),
        "draft.role": ("_draft_role", True),
        "draft.confirm": ("_draft_confirm", True),
        "draft.timer": ("_draft_timer", False),
        "draft.reset": ("_draft_reset", True),
        "undo": ("_undo", False),
        "game.new": ("_game_new", False),
        "game.clock": ("_game_clock", False),
        "game.set": ("_game_set", False),
        "game.add": ("_game_add", False),
        "game.lock": ("_game_lock", False),
        "game.objective": ("_game_objective", False),
        "game.remove_objective": ("_game_remove_objective", False),
        "game.buff_holder": ("_game_buff_holder", False),
        "game.end": ("_game_end", False),
        "game.reset": ("_game_reset", False),
        "ui.set": ("_ui_set", False),
        "vision.status": ("_vision_status", False),
        "game.soul_element": ("_game_soul_element", False),
        "fx.play": ("_fx_play", False),
        "game.multikill": ("_game_multikill", False),
        "game.finish": ("_game_finish", True),
        "postgame.stats": ("_postgame_stats", False),
        "postgame.set": ("_postgame_set", False),
    }

