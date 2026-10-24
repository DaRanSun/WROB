"""只读资料：英雄、战队、选手、历史分路。来自 WRPedia 和 config/，启动时读一次。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import yaml

from . import ROLES

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"

# WRPedia 里对外提供的只有这两个目录，其余（后台、发布凭据）不经本服务暴露。
WRPEDIA_PUBLIC = ("assets/champions", "assets/teams")


def load_config(name: str) -> dict:
    with open(CONFIG / f"{name}.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def read_js(path: Path, marker: str):
    """读 WRPedia 的 "window.X = <json>;" 数据文件。文件头注释里也可能出现 marker，所以只认行首。"""
    text = path.read_text(encoding="utf-8")
    start = 0 if text.startswith(marker) else text.find("\n" + marker) + 1
    if start == 0 and not text.startswith(marker):
        raise ValueError(f"{path.name} 里找不到 {marker.strip()}")
    body = text[start + len(marker):].rstrip()
    return json.loads(body[:-1] if body.endswith(";") else body)


class Catalog:
    def __init__(self, app_config: dict):
        self.wrpedia = (ROOT / app_config.get("wrpedia", "../WRPedia")).resolve()
        if not (self.wrpedia / "data" / "champions.json").is_file():
            raise FileNotFoundError(
                f"找不到 WRPedia：{self.wrpedia}。请在 config/app.yaml 里改 wrpedia 路径。"
            )
        self.art = load_config("art")
        league = read_js(self.wrpedia / "data" / "schedule.js", "window.WR_LEAGUE = ")
        self.role_stats = self._role_stats(league)
        self.usage = self._usage(league)
        self.champions = self._champions()
        self.items = self._items()
        self.legendary = set(load_config("items").get("legendary") or []) & set(self.items)
        self.key_items = self._key_items(self.items)
        self.item_values = self._item_values(self.items)
        self.teams = self._local_teams() + self._wrpedia_teams(league)

    # ---- 英雄 ----

    def _champions(self) -> dict[str, dict]:
        base = json.loads((self.wrpedia / "data" / "champions.json").read_text(encoding="utf-8"))
        locales = read_js(self.wrpedia / "data" / "champion-locales.js", "window.WR_CHAMPION_LOCALES = ")
        folder = self.wrpedia / "assets" / "champions"
        champions = {}
        for entry in base:
            slug = entry["slug"]
            locale = locales.get(slug, {})
            art = dict(self.art.get(slug) or {})
            portrait = f"{slug}-portrait.jpg"
            icon = f"{slug}.png"
            if art.get("icon") == "portrait" or not (folder / icon).is_file():
                icon, art["icon"] = portrait, "portrait"
            champions[slug] = {
                "slug": slug,
                "name": locale.get("name") or entry.get("name") or slug,   # 国服中文名
                "en": entry.get("name") or slug,
                "title": locale.get("title", ""),
                "aliases": locale.get("aliases", []),
                "icon": f"/wrpedia/assets/champions/{icon}",
                "portrait": f"/wrpedia/assets/champions/{portrait}" if (folder / portrait).is_file() else None,
                "art": art,
                "roles": self.role_stats.get(slug, {}),
                "usage": self.usage.get(slug, 0),
            }
        return champions

    @staticmethod
    def _role_stats(league: dict) -> dict[str, dict[str, float]]:
        """每个英雄在 WRL 已录入小局里各分路的出场占比，用来预填阵容分路。"""
        counts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for round_ in league.get("rounds", []):
            for match in round_.get("matches", []):
                for game in match.get("games", []):
                    for key in ("team1", "team2"):
                        side = game.get(key) or {}
                        pick_roles = side.get("pickRoles")
                        for index, slug in enumerate(side.get("picks") or []):
                            # 旧记录没有 pickRoles，picks 本身就是上野中下辅顺序。
                            role = pick_roles[index] if isinstance(pick_roles, list) else ROLES[index]
                            for item in role if isinstance(role, list) else [role]:
                                if slug and item in ROLES:
                                    counts[slug][item] += 1 / (len(role) if isinstance(role, list) else 1)
        stats = {}
        for slug, by_role in counts.items():
            total = sum(by_role.values())
            stats[slug] = {role: round(value / total, 3) for role, value in by_role.items()}
        return stats

    @staticmethod
    def _usage(league: dict) -> dict[str, int]:
        """每个英雄在 WRL 已录入小局里被选用、被禁用的总次数，用来给英雄下拉列表排序。"""
        counts: dict[str, int] = defaultdict(int)
        for round_ in league.get("rounds", []):
            for match in round_.get("matches", []):
                for game in match.get("games", []):
                    for key in ("team1", "team2"):
                        side = game.get(key) or {}
                        for slug in (side.get("picks") or []) + (side.get("bans") or []):
                            if slug:
                                counts[slug] += 1
        return dict(counts)

    @staticmethod
    def _items() -> list[str]:
        """装备的名字（同时是编号）。图标在 assets/items/<名字>.png，由 tools/build_items.py 生成。"""
        config = load_config("items")
        excluded = set(config.get("exclude") or [])
        names = list(config.get("items") or []) + list(config.get("extra") or {})        # extra：后来单独补的
        return [name for name in names if name not in excluded]

    @staticmethod
    def _key_items(items: list[str]) -> list[str]:
        """团战 UI 显示的关键装备：会读冷却条的那些（冷却超过 show_above 秒），去掉 teamfight_exclude 里的。"""
        timing = load_config("cooldowns")
        above = timing.get("show_above", 0)
        skip = set(timing.get("teamfight_exclude") or [])
        cooling = {name for name, seconds in (timing.get("items") or {}).items() if seconds > above and name not in skip}
        return [name for name in items if name in cooling]

    @staticmethod
    def _item_values(items: list[str]) -> dict[str, int]:
        """装备价值（config/loadout.yaml）：名字 → 商店总价。进化装备按进化前那件算。名字写错了启动时就报出来。"""
        config = load_config("loadout")
        values = {name: int(value) for name, value in (config.get("values") or {}).items()}
        for evolved, base in (config.get("evolved") or {}).items():
            if base not in values:
                raise ValueError(f"config/loadout.yaml：{evolved} 由 {base} 进化，但 values 里没有 {base}")
            values[evolved] = values[base]
        unknown = sorted(set(values) - set(items))
        if unknown:
            raise ValueError(f"config/loadout.yaml 里这些名字装备库（items.yaml）里没有：{'、'.join(unknown)}")
        return values

    # ---- 战队 ----

    def _local_teams(self) -> list[dict]:
        teams = []
        for team in load_config("teams").get("teams") or []:
            logo = team.get("logo") or ""
            teams.append({
                "id": str(team["id"]),
                "name": team.get("name") or str(team["id"]),
                "short": team.get("short") or str(team["id"]),
                "logo": f"/assets/teams/{logo}" if logo and "/" not in logo else logo,
                "players": {role: (team.get("players") or {}).get(role, "") for role in ROLES},
                "roster": [name for name in (team.get("players") or {}).values() if name],
                "source": "local",
            })
        return teams

    def _wrpedia_teams(self, league: dict) -> list[dict]:
        players = read_js(self.wrpedia / "data" / "players.js", "window.WR_PLAYERS = ")
        latest: dict[str, list[str]] = {}       # 每队最近一场的首发，顺序是上野中下辅
        for round_ in league.get("rounds", []):
            for match in round_.get("matches", []):
                for team_id, names in (match.get("startingLineups") or {}).items():
                    if len(names) == len(ROLES):
                        latest[team_id] = names
        teams = []
        for team in league.get("teams", []):
            roster = [p for p in players if p["teamId"] == team["id"]]
            lineup = dict(zip(ROLES, latest.get(team["id"], [])))
            for role in ROLES:
                if not lineup.get(role):
                    lineup[role] = next((p["name"] for p in roster if p["role"] == role), "")
            teams.append({
                "id": team["id"],
                "name": team.get("name") or team["id"],
                "short": team.get("short") or team["id"],
                "logo": f"/wrpedia/{team['logo']}" if team.get("logo") else "",
                "players": lineup,
                "roster": [p["name"] for p in roster],
                "source": "wrpedia",
            })
        return teams

    def public(self) -> dict:
        return {"champions": list(self.champions.values()), "teams": self.teams, "items": self.items,
                "legendary": [name for name in self.items if name in self.legendary], "keyItems": self.key_items}
