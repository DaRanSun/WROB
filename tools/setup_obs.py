#!/usr/bin/env python3
"""把 WROB 的场景和画布设置写进 OBS（macOS）。OBS 开着的时候不能写，先退出 OBS（⌘Q）。

    python tools/setup_obs.py           单设备：场景集合和配置文件都叫“WROB”
    python tools/setup_obs.py dual      双设备：另一套，叫“WROB 双设备”（不动单设备那套）

单设备：
  · 场景“局内（单设备）”：手机画面（窗口采集 scrcpy 的 LOLM 窗口，铺满画布）+ 局内面板（浏览器源，2800×1264）
  · 场景“BP”：BP 面板（浏览器源，1920×1080，等比放进画布居中）
  · 画布 2800×1264（和手机画面一样大），输出 1920×866（同比例）
双设备：
  · 场景“局内（双设备）”：观战机画面（窗口采集 MuMu 模拟器的窗口，等比放进中间 1920×750 那一块，即 y 90–840）
    + 局内面板（浏览器源，1920×1080，盖在上面；导播台“局内”页的画面模式要选“双设备”）
  · 场景“BP”：BP 面板铺满
  · 画布、输出都是 1920×1080
两套都是 60 帧，录像存到“影片”文件夹；跑完之后 OBS 下次打开就用刚写的那一套（菜单“场景集合”“配置文件”里可以换回另一套）。

已经有同名的场景集合 / 配置文件时会先备份（加上时间后缀）。面板的地址按 config/app.yaml 的端口写。
画面那个来源第一次要在 OBS 里点开、选一下窗口（窗口编号每次启动都会变，没法提前写死）。
"""
import configparser
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from state.catalog import load_config  # noqa: E402

OBS = Path.home() / "Library" / "Application Support" / "obs-studio"
# name 是 OBS 菜单里显示的名字，file 是 basic/scenes/ 下的文件名和 basic/profiles/ 下的目录名
MODES = {
    "single": {"name": "WROB", "file": "WROB", "canvas": (2800, 1264), "output": (1920, 866)},    # 同比例、偶数边长（编码器要求）
    "dual": {"name": "WROB 双设备", "file": "WROB_dual", "canvas": (1920, 1080), "output": (1920, 1080)},
}
PICTURE = {"pos": (0, 90), "size": (1920, 750)}     # 双设备时观战机画面的位置：顶部 90 高的面板和底部 240 高的面板之间
# MuMu 的窗口顶上有一条 38 点的标题栏 + 工具栏（“我的安卓”），底下两个角是圆的。采集到的是 Retina 的 2 倍像素：
# 顶上裁 76，底下裁 52（26 点，去掉圆角），再“铺满”1920×750（窗口比例差一点时，多出来的那一点出画布）。MuMu 的窗口大小随便拖，这几个数不变。
MUMU_CROP = {"crop_top": 76, "crop_bottom": 52}


def ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str        # OBS 的键区分大小写
    if path.is_file():
        parser.read(path, encoding="utf-8-sig")
    return parser


def save_ini(parser: configparser.ConfigParser, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        parser.write(fh, space_around_delimiters=False)


def backup(path: Path) -> None:
    if path.exists():
        target = path.with_name(path.name + time.strftime(".bak-%Y%m%d-%H%M%S"))
        shutil.move(str(path), str(target))
        print(f"  原来的 {path.name} 备份成了 {target.name}")


def source(name: str, kind: str, settings: dict) -> dict:
    return {"name": name, "uuid": str(uuid.uuid4()), "id": kind, "versioned_id": kind, "settings": settings,
            "mixers": 0, "sync": 0, "flags": 0, "volume": 1.0, "balance": 0.5, "enabled": True, "muted": False,
            "push-to-mute": False, "push-to-mute-delay": 0, "push-to-talk": False, "push-to-talk-delay": 0,
            "hotkeys": {}, "deinterlace_mode": 0, "deinterlace_field_order": 0, "monitoring_type": 0,
            "private_settings": {}}


def item(src: dict, number: int, bounds: tuple[int, int] | None = None, pos: tuple[int, int] = (0, 0),
         cover: bool = False, crop: dict | None = None) -> dict:
    """场景里的一项。bounds 给了就等比放进这么大的框里、居中（cover 时是等比铺满，多出来的出框）；没给就按原尺寸放。
    pos 是左上角，crop 是先裁掉的像素。"""
    entry = {"name": src["name"], "source_uuid": src["uuid"], "visible": True, "locked": False, "rot": 0.0,
             "pos": {"x": float(pos[0]), "y": float(pos[1])}, "scale": {"x": 1.0, "y": 1.0}, "align": 5,
             "bounds_type": 0, "bounds_align": 0, "bounds": {"x": 0.0, "y": 0.0},
             "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0, "id": number,
             "group_item_backup": False, "scale_filter": "disable", "blend_method": "default", "blend_type": "normal",
             "show_transition": {"duration": 0}, "hide_transition": {"duration": 0}, "private_settings": {}}
    if bounds:
        entry.update(bounds_type=3 if cover else 2, bounds={"x": float(bounds[0]), "y": float(bounds[1])})
    entry.update(crop or {})
    return entry


def scene(name: str, items: list[dict]) -> dict:
    return source(name, "scene", {"id_counter": len(items), "custom_size": False, "items": items})


def collection(port: int, mode: str) -> dict:
    spec = MODES[mode]
    canvas = spec["canvas"]
    base = f"http://127.0.0.1:{port}"
    window = {"type": 1, "show_cursor": False, "show_hidden_windows": False, "show_empty_names": False, "hide_obs": True}
    game = source("WROB 局内面板", "browser_source",
                  {"url": f"{base}/overlay/game.html", "width": canvas[0], "height": canvas[1], "fps_custom": True, "fps": 60,
                   "reroute_audio": False, "restart_when_active": False, "shutdown": False, "css": ""})
    bp = source("WROB BP 面板", "browser_source",
                {"url": f"{base}/overlay/bp.html?bg=1", "width": 1920, "height": 1080, "fps_custom": True, "fps": 60,
                 "reroute_audio": False, "restart_when_active": False, "shutdown": False, "css": ""})
    if mode == "dual":
        picture = source("观战机画面（MuMu 窗口）", "screen_capture", window)
        in_game = scene("局内（双设备）", [item(picture, 1, PICTURE["size"], PICTURE["pos"], cover=True, crop=MUMU_CROP),
                                           item(game, 2)])
    else:
        picture = source("手机画面（scrcpy LOLM 窗口）", "screen_capture", window)
        in_game = scene("局内（单设备）", [item(picture, 1, canvas), item(game, 2)])
    draft = scene("BP", [item(bp, 1, canvas)])
    return {"name": spec["name"], "current_scene": in_game["name"], "current_program_scene": in_game["name"],
            "scene_order": [{"name": draft["name"]}, {"name": in_game["name"]}],
            "sources": [picture, game, bp, draft, in_game], "groups": [], "quick_transitions": [], "transitions": [],
            "saved_projectors": [], "current_transition": "Fade", "transition_duration": 300,
            "preview_locked": False, "scaling_enabled": False, "scaling_level": 0, "scaling_off_x": 0.0, "scaling_off_y": 0.0,
            "virtual-camera": {"type2": 3}, "modules": {}, "version": 2}


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "single"
    if mode not in MODES:
        sys.exit("用法：python tools/setup_obs.py [single|dual]")
    if subprocess.run(["pgrep", "-x", "OBS"], capture_output=True).returncode == 0:
        sys.exit("OBS 还开着：先退出 OBS（⌘Q），再运行这个脚本")
    port = int((load_config("app") or {}).get("port", 8780))
    spec = MODES[mode]
    name, canvas, output = spec["name"], spec["canvas"], spec["output"]

    scenes = OBS / "basic" / "scenes" / f"{spec['file']}.json"
    scenes.parent.mkdir(parents=True, exist_ok=True)
    backup(scenes)
    scenes.write_text(json.dumps(collection(port, mode), ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"场景集合“{name}” → {scenes}")

    profile_dir = OBS / "basic" / "profiles" / spec["file"]
    profile = ini(profile_dir / "basic.ini")
    for section, values in {
        "General": {"Name": name},
        "Video": {"BaseCX": canvas[0], "BaseCY": canvas[1], "OutputCX": output[0], "OutputCY": output[1],
                  "FPSType": 0, "FPSCommon": 60, "ScaleType": "lanczos"},
        "Output": {"Mode": "Simple"},
        # 硬件编码（Apple VideoToolbox）：软编码 x264 在 1080p60 下和模拟器、游戏抢 CPU，实测丢了 44% 的帧
        "SimpleOutput": {"FilePath": str(Path.home() / "Movies"), "RecFormat2": "mp4", "RecQuality": "Small",
                         "StreamEncoder": "apple_h264", "RecEncoder": "apple_h264"},
    }.items():
        if not profile.has_section(section):
            profile.add_section(section)
        for key, value in values.items():
            profile.set(section, key, str(value))
    save_ini(profile, profile_dir / "basic.ini")
    print(f"配置文件“{name}” → {profile_dir / 'basic.ini'}（画布 {canvas[0]}×{canvas[1]}，输出 {output[0]}×{output[1]}）")

    for filename in ("user.ini", "global.ini"):
        path = OBS / filename
        if not path.is_file():
            continue
        parser = ini(path)
        if filename == "global.ini" and not parser.has_section("Basic"):
            continue                # 新版 OBS 的这些设置在 user.ini 里
        if not parser.has_section("Basic"):
            parser.add_section("Basic")
        parser.set("Basic", "Profile", name)
        parser.set("Basic", "ProfileDir", spec["file"])
        parser.set("Basic", "SceneCollection", name)
        parser.set("Basic", "SceneCollectionFile", spec["file"] + ".json")
        save_ini(parser, path)
        print(f"让 OBS 默认用它们 → {path.name}")
    if mode == "dual":
        print("好了。打开 OBS 之后：双击“观战机画面”那个来源，在“窗口”里选 MuMu 模拟器的窗口；"
              "窗口的标题栏 / 工具栏露出来的话，选中它按住 ⌥ 拖边缘裁掉。")
    else:
        print("好了。打开 OBS 之后：双击“手机画面”那个来源，在“窗口”里选 LOLM。")


if __name__ == "__main__":
    main()
