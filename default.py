# -*- coding: utf-8 -*-
"""彩云天气 · Kodi 天气插件入口。

Kodi 调用本插件的两种方式：

  RunScript(weather.caiyun, N)
      Kodi 需要第 N 个城市的天气，抓取后写入 Window(12600) 的属性。
      插件不常驻，Kodi 每 30 分钟拉起一次。

  RunScript(weather.caiyun, locationN / detailN / radarN)
      设置页按钮与详情窗口的入口。
"""

import os
import sys

_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
if _ADDON_DIR not in sys.path:
    sys.path.insert(0, _ADDON_DIR)

SLOTS = (1, 2, 3)


def _slot_from(args, default=1):
    if len(args) > 1:
        raw = args[1].strip()
        if raw.isdigit() and int(raw) in SLOTS:
            return int(raw)
    return default


def main():
    args = [arg for arg in sys.argv[1:] if arg and arg.strip()]
    mode = args[0].strip() if args else ""

    # 云图窗口：RunAddon(weather.caiyun, radar[,2])
    if mode.startswith("radar"):
        from resources.lib import radardialog
        radardialog.show(_slot_from(args))
        return 0

    # 详情窗口：RunAddon(weather.caiyun, detail[,2])
    if mode.startswith("detail"):
        from resources.lib import detaildialog
        detaildialog.show(_slot_from(args))
        return 0

    # 城市选择：设置页按钮
    if mode.startswith("location"):
        suffix = mode[len("location"):]
        if suffix.isdigit() and int(suffix) in SLOTS:
            from resources.lib import citypicker
            citypicker.pick(int(suffix))
            return 0
        return 0

    # 取数模式
    try:
        index = int(mode)
    except ValueError:
        index = 1
    if index not in SLOTS:
        index = 1

    from resources.lib import weather
    weather.update(index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
