# -*- coding: utf-8 -*-
"""Kodi API 薄封装。

整个插件里只有本模块直接依赖 xbmc*；net / api / codes / mapper / pnglite
保持纯 Python，可以在没有 Kodi 的环境里直接单测。
"""

import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "weather.caiyun"
WEATHER_WINDOW = 12600


def addon():
    return xbmcaddon.Addon(ADDON_ID)


def localize(string_id):
    try:
        return addon().getLocalizedString(string_id)
    except Exception:
        return ""


def setting(key, default=""):
    try:
        value = addon().getSetting(key)
    except Exception:
        return default
    if value is None or value == "":
        return default
    return value


def set_setting(key, value):
    try:
        addon().setSetting(key, "" if value is None else str(value))
    except Exception as exc:
        log("setSetting(%s) failed: %s" % (key, exc), xbmc.LOGWARNING)


def setting_int(key, default):
    try:
        return int(float(setting(key, default)))
    except (TypeError, ValueError):
        return int(default)


def setting_float(key, default):
    try:
        return float(setting(key, default))
    except (TypeError, ValueError):
        return float(default)


def setting_bool(key, default=False):
    value = str(setting(key, "true" if default else "false")).strip().lower()
    return value in ("true", "1", "yes", "on")


def log(message, level=xbmc.LOGINFO):
    try:
        xbmc.log("[%s] %s" % (ADDON_ID, message), level)
    except Exception:
        pass


def debug(message):
    if setting_bool("debug_log", False):
        log(message, xbmc.LOGINFO)


def notify(message, heading=None, icon=None, time_ms=6000):
    heading = heading or localize(32000)
    icon = icon or xbmcgui.NOTIFICATION_INFO
    try:
        xbmcgui.Dialog().notification(heading, message, icon, time_ms, False)
    except TypeError:
        try:
            xbmcgui.Dialog().notification(heading, message, icon, time_ms)
        except Exception:
            pass
    except Exception:
        pass


def translate(path):
    try:
        return xbmcvfs.translatePath(path)
    except (AttributeError, NameError):
        return xbmc.translatePath(path)


def profile_dir():
    """addon_data 目录；不存在则创建。"""
    path = translate("special://profile/addon_data/%s/" % ADDON_ID)
    try:
        if not os.path.isdir(path):
            os.makedirs(path)
    except OSError:
        pass
    return path


def addon_path():
    try:
        return addon().getAddonInfo("path")
    except Exception:
        return translate("special://home/addons/%s" % ADDON_ID)


def window():
    return xbmcgui.Window(WEATHER_WINDOW)


def dialog():
    return xbmcgui.Dialog()


def input_alphanum():
    return getattr(xbmcgui, "INPUT_ALPHANUM", 0)


def language():
    """返回 'zh' 或 'en'，用于挑选文案。"""
    try:
        lang = xbmc.getLanguage(xbmc.ISO_639_1, False) or "zh"
    except Exception:
        lang = "zh"
    lang = lang.lower()
    if lang.startswith("zh"):
        return "zh"
    return "en"
