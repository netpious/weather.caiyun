# -*- coding: utf-8 -*-
"""彩云枚举 → Kodi 天气码 / 中英文案。纯数据 + 纯函数。"""

# 彩云 skycon → (Kodi 天气码, 中文, English)
SKYCON = {
    "CLEAR_DAY": (32, "晴", "Sunny"),
    "CLEAR_NIGHT": (31, "晴", "Clear"),
    "PARTLY_CLOUDY_DAY": (30, "多云", "Partly cloudy"),
    "PARTLY_CLOUDY_NIGHT": (29, "多云", "Partly cloudy"),
    "CLOUDY": (26, "阴", "Cloudy"),
    "LIGHT_HAZE": (21, "轻度霾", "Light haze"),
    "MODERATE_HAZE": (21, "中度霾", "Moderate haze"),
    "HEAVY_HAZE": (21, "重度霾", "Heavy haze"),
    "LIGHT_RAIN": (11, "小雨", "Light rain"),
    "MODERATE_RAIN": (12, "中雨", "Moderate rain"),
    "HEAVY_RAIN": (12, "大雨", "Heavy rain"),
    "STORM_RAIN": (4, "暴雨", "Rainstorm"),
    "FOG": (20, "雾", "Fog"),
    "LIGHT_SNOW": (13, "小雪", "Light snow"),
    "MODERATE_SNOW": (16, "中雪", "Moderate snow"),
    "HEAVY_SNOW": (42, "大雪", "Heavy snow"),
    "STORM_SNOW": (42, "暴雪", "Snowstorm"),
    "DUST": (19, "浮尘", "Dust"),
    "SAND": (19, "沙尘", "Sand"),
    "WIND": (24, "大风", "Windy"),
}

# 没有 skycon 时的兜底
UNKNOWN = (26, "未知", "Unknown")

ICON_BASE = "resource://resource.images.weathericons.default"


def describe(skycon, lang="zh"):
    code, zh, en = SKYCON.get(str(skycon or "").upper(), UNKNOWN)
    return {
        "code": code,
        "text": en if lang == "en" else zh,
        "zh": zh,
        "en": en,
        "icon": "%s/%02d.png" % (ICON_BASE, code),
    }


def icon_path(code):
    return "%s/%02d.png" % (ICON_BASE, int(code))


# ---------------------------------------------------------------- 风向

_WIND_ZH = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
_WIND_EN = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def wind_direction(degrees, lang="zh"):
    try:
        angle = float(degrees) % 360.0
    except (TypeError, ValueError):
        return ""
    index = int((angle + 22.5) // 45) % 8
    return _WIND_EN[index] if lang == "en" else _WIND_ZH[index] + "风"


def wind_degrees(degrees):
    try:
        return "%d°" % int(round(float(degrees) % 360.0))
    except (TypeError, ValueError):
        return ""


def wind_level(kmh):
    """km/h → 蒲福风级（0–12）。"""
    try:
        value = float(kmh)
    except (TypeError, ValueError):
        return 0
    limits = [1, 5, 11, 19, 28, 38, 49, 61, 74, 88, 102, 117]
    for level, limit in enumerate(limits):
        if value < limit:
            return level
    return 12


# ---------------------------------------------------------------- 空气质量

AQI_LEVELS = [
    (50, "优", "Good", "FF00E400"),
    (100, "良", "Moderate", "FFFFFF00"),
    (150, "轻度污染", "Unhealthy for sensitive", "FFFF7E00"),
    (200, "中度污染", "Unhealthy", "FFFF0000"),
    (300, "重度污染", "Very unhealthy", "FF99004C"),
    (100000, "严重污染", "Hazardous", "FF7E0023"),
]


def aqi_level(value, lang="zh"):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return {"text": "", "color": "FFAAAAAA", "index": -1}
    for limit, zh, en, color in AQI_LEVELS:
        if number <= limit:
            return {"text": en if lang == "en" else zh, "color": color, "index": limit}
    return {"text": "", "color": "FFAAAAAA", "index": -1}


# ---------------------------------------------------------------- 日期

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]
WEEKDAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def short_weekday(moment, lang="zh"):
    if moment is None:
        return ""
    index = moment.weekday()
    return WEEKDAY_EN[index] if lang == "en" else "周" + WEEKDAY_ZH[index]


def long_weekday(moment, lang="zh"):
    if moment is None:
        return ""
    index = moment.weekday()
    if lang == "en":
        return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday"][index]
    return "星期" + WEEKDAY_ZH[index]


# ---------------------------------------------------------------- 降水强度

INTENSITY_ZH = ["无降水", "小毛毛雨", "毛毛雨", "小雨", "中雨", "大雨", "暴雨"]
INTENSITY_EN = ["Dry", "Very light", "Light drizzle", "Light rain", "Rain",
                "Heavy rain", "Downpour"]


def intensity_text(intensity, lang="zh"):
    """彩云 local.intensity：0–1 的强度指数。"""
    try:
        value = float(intensity)
    except (TypeError, ValueError):
        return ""
    if value <= 0.031:
        level = 0
    elif value <= 0.15:
        level = 1
    elif value <= 0.35:
        level = 2
    elif value <= 0.55:
        level = 3
    elif value <= 0.8:
        level = 4
    elif value <= 0.95:
        level = 5
    else:
        level = 6
    return (INTENSITY_EN if lang == "en" else INTENSITY_ZH)[level]
