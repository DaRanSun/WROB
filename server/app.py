"""aiohttp 应用。

    /director/            导播台
    /overlay/bp.html      BP 面板（OBS 浏览器源，1920×1080）
    /overlay/game.html    局内面板（OBS 浏览器源；单设备 2800×1264，和手机画面一样大；双设备 1920×1080）
    /ws                   WebSocket：连上先收到完整状态，之后每次变化再收一份；也可以从这里发命令
    /api/state            当前状态
    /api/catalog          英雄、战队、BP 流程、规则
    /api/command          POST 一条命令（和 WebSocket 里的 cmd 等价，给脚本和以后的识别服务用）
    /api/curve            本局经济曲线
    /wrpedia/assets/…     WRPedia 的英雄图和队标（只读，只开放这两个目录）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import webbrowser
from pathlib import Path
from urllib.parse import quote

from aiohttp import WSCloseCode, WSMsgType, web

from state import CommandError
from state.catalog import WRPEDIA_PUBLIC, Catalog, load_config
from state.store import Store, now_ms

ROOT = Path(__file__).resolve().parent.parent
PAGES = ("overlay", "director", "common", "assets")
TICK_SECONDS = 0.25
SAVE_SECONDS = 1.0
PING_SECONDS = 4.0          # 应用层心跳；前端超过 3 个周期没收到任何消息就判定断线并重连

STORE = web.AppKey("store", Store)
CATALOG = web.AppKey("catalog", dict)
CLIENTS = web.AppKey("clients", set)



def _dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=_dump(data), status=status, content_type="application/json")


async def broadcast(app: web.Application, message: str | None = None) -> None:
    message = message or _dump({"type": "state", "state": app[STORE].snapshot()})
    for client in list(app[CLIENTS]):
        try:
            await client.send_str(message)
        except (ConnectionError, RuntimeError):
            app[CLIENTS].discard(client)


async def run_command(app: web.Application, command) -> dict:
    if not isinstance(command, dict):
        return {"ok": False, "error": "命令必须是一个对象"}
    try:
        app[STORE].apply(command)
    except CommandError as error:
        return {"ok": False, "error": str(error)}
    await broadcast(app)
    return {"ok": True}


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    socket = web.WebSocketResponse(heartbeat=20)
    await socket.prepare(request)
    app = request.app
    app[CLIENTS].add(socket)
    try:
        await socket.send_str(_dump({"type": "state", "state": app[STORE].snapshot()}))
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(message.data)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("type") == "cmd":
                reply = await run_command(app, data.get("cmd"))
                await socket.send_str(_dump({"type": "ack", "id": data.get("id"), **reply}))
    finally:
        app[CLIENTS].discard(socket)
    return socket


async def handle_command(request: web.Request) -> web.Response:
    try:
        command = await request.json()
    except ValueError:
        return _json({"ok": False, "error": "请求体不是 JSON"}, 400)
    reply = await run_command(request.app, command)
    return _json(reply, 200 if reply["ok"] else 400)


async def handle_state(request: web.Request) -> web.Response:
    return _json(request.app[STORE].snapshot())


async def handle_catalog(request: web.Request) -> web.Response:
    return _json({**request.app[CATALOG], "dragonIcons": dragon_icons(request.app[CATALOG]["rules"])})


def dragon_icons(rules: dict) -> dict[str, str]:
    """元素龙的图标：assets/dragons/ 里文件名以属性的英文名开头的图片（Infernal_Dragon_buff.png、ocean.png 都行）。

    图片可以随时往里放，所以每次现看。没有图片的属性，面板用自己画的示意图标。
    """
    files = sorted(path for path in (ROOT / "assets" / "dragons").glob("*")
                   if path.suffix.lower() in (".png", ".webp", ".svg", ".jpg"))
    icons = {}
    for element in rules["dragon"]["elements"]:
        match = next((path for path in files if path.name.lower().startswith(element)), None)
        if match:
            icons[element] = f"/assets/dragons/{quote(match.name)}"
    return icons


async def handle_curve(request: web.Request) -> web.Response:
    return _json(request.app[STORE].game["curve"])


@web.middleware
async def cache_headers(request: web.Request, handler):
    response = await handler(request)
    # OBS 的浏览器源缓存很顽固；页面和脚本一律不缓存，改完刷新就生效。图片可以缓存。
    response.headers.setdefault(
        "Cache-Control", "public, max-age=3600" if request.path.startswith("/wrpedia/") else "no-store")
    return response


async def _ticker(app: web.Application) -> None:
    store = app[STORE]
    since_save = since_ping = 0.0
    while True:
        await asyncio.sleep(TICK_SECONDS)
        if store.tick():
            await broadcast(app)
        since_save += TICK_SECONDS
        since_ping += TICK_SECONDS
        if since_save >= SAVE_SECONDS:
            since_save = 0.0
            store.save()
        if since_ping >= PING_SECONDS:
            since_ping = 0.0
            await broadcast(app, _dump({"type": "ping", "now": now_ms()}))


async def _close_clients(app: web.Application) -> None:
    # 不主动断开的话，aiohttp 退出时会等这些长连接最多 60 秒，面板也就迟迟不去重连新起的服务。
    for client in list(app[CLIENTS]):
        await client.close(code=WSCloseCode.GOING_AWAY, message=b"server restarting")


async def _background(app: web.Application):
    task = asyncio.create_task(_ticker(app))
    yield
    task.cancel()
    app[STORE].save()


def build_app(state_path: Path | None = ROOT / "data" / "state.json") -> web.Application:
    app_config = load_config("app")
    draft_config = load_config("draft")
    rules = load_config("rules")
    catalog = Catalog(app_config)
    store = Store(catalog, app_config, draft_config, rules, path=state_path)

    app = web.Application(middlewares=[cache_headers])
    app[STORE] = store
    app[CLIENTS] = set()
    app[CATALOG] = {
        **catalog.public(),
        "formats": {key: fmt.get("name", key) for key, fmt in draft_config["formats"].items()},
        "defaultFormat": store.default_format,
        "rules": rules,
        "effects": load_config("effects"),
    }

    async def index(request):
        raise web.HTTPFound("/director/")

    async def director(request):
        return web.FileResponse(ROOT / "director" / "index.html")

    async def no_icon(request):
        return web.Response(status=204)

    app.router.add_get("/", index)
    app.router.add_get("/director", index)
    app.router.add_get("/director/", director)
    app.router.add_get("/favicon.ico", no_icon)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/catalog", handle_catalog)
    app.router.add_get("/api/curve", handle_curve)
    app.router.add_post("/api/command", handle_command)
    for name in PAGES:
        app.router.add_static(f"/{name}/", ROOT / name)
    for folder in WRPEDIA_PUBLIC:
        app.router.add_static(f"/wrpedia/{folder}/", catalog.wrpedia / folder)
    app.on_shutdown.append(_close_clients)
    app.cleanup_ctx.append(_background)
    return app


def main() -> None:
    config = load_config("app")
    parser = argparse.ArgumentParser(description="WROB 赛事直播面板服务")
    parser.add_argument("--host", default=config.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=config.get("port", 8780))
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开导播台")
    parser.add_argument("--state", type=Path, default=ROOT / "data" / "state.json",
                        help="状态存档的位置（默认 data/state.json；试东西时可以指到别处，不动正式存档）")
    args = parser.parse_args()

    app = build_app(args.state)
    base = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"

    async def announce(_app):
        print(f"导播台      {base}/director/")
        print(f"BP 面板     {base}/overlay/bp.html      （OBS 浏览器源，1920×1080）")
        print(f"局内面板    {base}/overlay/game.html    （OBS 浏览器源，2800×1264，和手机画面一样大）")
        if not args.no_browser:
            webbrowser.open(f"{base}/director/")

    app.on_startup.append(announce)
    web.run_app(app, host=args.host, port=args.port, print=None)
