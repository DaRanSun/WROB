#!/usr/bin/env python3
"""下载每个英雄的大招图标到 assets/ults/<英雄 slug>.png（面板上显示大招冷却用）。

    python tools/fetch_ults.py            # 只补缺的
    python tools/fetch_ults.py --all      # 全部重新下载

图标来自 Riot 的 Data Dragon（端游的公开素材库）：手游和端游的大招图标绝大多数是同一张。
端游里没有的英雄下载不到，面板上会用一个通用的“R”图标；自己找到图后按同样的文件名放进去即可（--all 也不会覆盖它们）。
诺拉的 assets/ults/norra.png 就是这样来的：用户 10-20 给的手游截图 norra_R.png，缩成 64×64。
"""
import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from state.catalog import Catalog, load_config  # noqa: E402

CDN = "https://ddragon.leagueoflegends.com"
FOLDER = ROOT / "assets" / "ults"


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read()


def plain(text: str) -> str:
    """名字只留字母数字，用来把 "Kha'Zix"、"khazix"、"Nunu & Willump"、"nunu-and-willump" 对上。"""
    return re.sub(r"[^a-z0-9]", "", text.lower().replace("&", "and"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="已经有的也重新下载")
    args = parser.parse_args()

    version = json.loads(get(f"{CDN}/api/versions.json"))[0]
    listing = json.loads(get(f"{CDN}/cdn/{version}/data/en_US/champion.json"))["data"]
    by_name = {plain(entry["name"]): key for key, entry in listing.items()}
    by_name.update({plain(key): key for key in listing})
    FOLDER.mkdir(parents=True, exist_ok=True)

    def fetch(champion: dict) -> tuple[str, str]:
        target = FOLDER / f"{champion['slug']}.png"
        if target.is_file() and not args.all:
            return champion["slug"], "已有"
        key = by_name.get(plain(champion.get("en") or "")) or by_name.get(plain(champion["slug"]))
        if key is None:
            return champion["slug"], "端游里没有这个英雄"
        try:
            detail = json.loads(get(f"{CDN}/cdn/{version}/data/en_US/champion/{key}.json"))["data"][key]
            target.write_bytes(get(f"{CDN}/cdn/{version}/img/spell/{detail['spells'][3]['image']['full']}"))
        except Exception as error:                                   # 网络问题：留给下次补
            return champion["slug"], f"下载失败（{error}）"
        return champion["slug"], "下载了"

    champions = list(Catalog(load_config("app")).champions.values())
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(fetch, champions))
    counts = {}
    for _, outcome in results:
        counts[outcome.split("（")[0]] = counts.get(outcome.split("（")[0], 0) + 1
    print(f"Data Dragon {version}；" + "，".join(f"{name} {count} 个" for name, count in counts.items()))
    for slug, outcome in results:
        if outcome not in ("已有", "下载了"):
            print(f"  {slug}: {outcome}")


if __name__ == "__main__":
    main()
