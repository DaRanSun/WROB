"""状态中心：BP 状态机、系列赛、局内规则引擎。不依赖画面识别，也不依赖网络层。"""

SIDES = ("blue", "red")
OTHER_SIDE = {"blue": "red", "red": "blue"}
ROLES = ("top", "jungle", "mid", "bot", "support")
ROLE_NAMES = {"top": "上单", "jungle": "打野", "mid": "中路", "bot": "下路", "support": "辅助"}


class CommandError(ValueError):
    """导播台操作被拒绝。消息直接显示给导播，所以写中文。"""
