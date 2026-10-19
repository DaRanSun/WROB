import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from server.app import build_app


def run(scenario):
    async def main():
        client = TestClient(TestServer(build_app(state_path=None)))
        await client.start_server()
        try:
            await scenario(client)
        finally:
            await client.close()
    asyncio.run(main())


def test_pages_and_assets():
    async def scenario(client):
        for path in ("/overlay/bp.html", "/overlay/game.html", "/overlay/icons.js", "/director/", "/common/client.js",
                     "/wrpedia/assets/champions/ahri.png", "/wrpedia/assets/champions/norra-portrait.jpg",
                     "/assets/items/%E7%8B%82%E6%88%98%E5%A3%AB%E8%83%AB%E7%94%B2.png"):          # 装备图标是中文文件名
            response = await client.get(path)
            assert response.status == 200, path
        page = await client.get("/overlay/bp.js")
        assert page.headers["Cache-Control"] == "no-store"
        # WRPedia 只开放素材目录
        for path in ("/wrpedia/admin/server.py", "/wrpedia/data/schedule.js", "/wrpedia/.netlify/state.json"):
            assert (await client.get(path)).status == 404, path
    run(scenario)


def test_catalog():
    async def scenario(client):
        catalog = await (await client.get("/api/catalog")).json()
        champions = {champion["slug"]: champion for champion in catalog["champions"]}
        assert len(champions) == 142
        assert champions["hwei"]["name"] == "彗" and champions["hwei"]["art"]["portrait_focus"]
        assert champions["norra"]["icon"].endswith("norra-portrait.jpg") and champions["norra"]["art"]["icon"] == "portrait"
        assert champions["lee-sin"]["roles"] == {"jungle": 1.0}
        assert champions["zilean"]["usage"] > champions["lee-sin"]["usage"] > 0      # 下拉列表按选禁次数排序
        assert min(champion["usage"] for champion in champions.values()) == 0        # 没上过场的英雄也在目录里
        assert {team["id"] for team in catalog["teams"]} >= {"TT", "FP", "RV", "KBG", "WBG", "SS1", "WHG", "ACE"}
        assert all(len(team["players"]) == 5 and all(team["players"].values()) for team in catalog["teams"])
        assert catalog["formats"]["tournament"] == "赛事征召"
        assert len(catalog["items"]) == 176 and "狂战士胫甲" in catalog["items"]
        assert catalog["rules"]["dragon"]["elements"] == ["infernal", "mountain", "ocean"]
        # assets/dragons/ 里放了哪几种元素龙的图标：属性 → 地址
        assert set(catalog["dragonIcons"]) <= set(catalog["rules"]["dragon"]["elements"])
        for url in catalog["dragonIcons"].values():
            assert (await client.get(url)).status == 200, url
    run(scenario)


def test_commands_over_http_and_websocket():
    async def scenario(client):
        socket = await client.ws_connect("/ws")
        first = json.loads(await socket.receive_str())
        assert first["type"] == "state" and first["state"]["draft"]["status"] == "idle"

        reply = await client.post("/api/command", json={"type": "draft.start"})
        assert reply.status == 200 and (await reply.json()) == {"ok": True}
        pushed = json.loads(await socket.receive_str())
        assert pushed["state"]["draft"]["status"] == "running"
        assert pushed["state"]["draft"]["active"] == [{"side": "blue", "type": "ban", "index": 0}]

        await socket.send_str(json.dumps({"type": "cmd", "id": 7, "cmd": {"type": "draft.lock", "champion": "ahri"}}))
        pushed = json.loads(await socket.receive_str())
        assert pushed["state"]["draft"]["slots"]["blue"]["ban"][0]["champion"] == "ahri"
        assert json.loads(await socket.receive_str()) == {"type": "ack", "id": 7, "ok": True}

        # 被拒绝的命令：HTTP 400 + 中文原因，不广播
        reply = await client.post("/api/command", json={"type": "draft.lock", "champion": "ahri"})
        assert reply.status == 400 and "本局已禁用" in (await reply.json())["error"]
        for bad in ({"type": "nope"}, {"type": "draft.clear"}, {"type": "series.update", "patch": {"teams": "x"}}, [1, 2]):
            reply = await client.post("/api/command", json=bad)
            assert reply.status == 400 and (await reply.json())["ok"] is False, bad
        state = await (await client.get("/api/state")).json()
        assert state["draft"]["turn"] == 1
        await socket.close()
    run(scenario)

