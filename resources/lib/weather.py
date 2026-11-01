# -*- coding: utf-8 -*-
"""取数、缓存、发布到 Kodi 天气窗口。"""

import hashlib
import json
import os
import time

from . import anim, api, kodi, mapper, maps, net, radar

CACHE_VERSION = 3

DAILY_LIMIT = 7
HOURLY_LIMIT = 16


# ---------------------------------------------------------------- 城市槽


def slot_conf(index):
    """读取第 index 个城市槽的配置。"""
    name = (kodi.setting("location%s" % index, "") or "").strip()
    lon = kodi.setting("lon%s" % index, "")
    lat = kodi.setting("lat%s" % index, "")
    try:
        lon_f = float(lon)
        lat_f = float(lat)
    except (TypeError, ValueError):
        lon_f = lat_f = None

    if lon_f is None or lat_f is None:
        # 槽 1 没配就当北京，保证 Kodi 首页有东西可显示
        if index == 1:
            lon_f, lat_f = 116.4074, 39.9042
            name = name or "北京"
        else:
            return {"name": name, "lon": None, "lat": None}

    return {"name": name or "%.2f,%.2f" % (lon_f, lat_f), "lon": lon_f, "lat": lat_f}


def save_slot(index, name, lon, lat):
    kodi.set_setting("location%s" % index, name)
    kodi.set_setting("lon%s" % index, "%.6f" % float(lon))
    kodi.set_setting("lat%s" % index, "%.6f" % float(lat))


# ---------------------------------------------------------------- 缓存


def _cache_path(index):
    return os.path.join(kodi.profile_dir(), "weather_%s.json" % index)


def _read_cache(index, ttl):
    path = _cache_path(index)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (IOError, OSError, ValueError):
        return None
    if payload.get("version") != CACHE_VERSION:
        return None
    if time.time() - payload.get("saved_at", 0) > ttl:
        return None
    return payload.get("data")


def _write_cache(index, data):
    payload = {"version": CACHE_VERSION, "saved_at": time.time(), "data": data}
    try:
        with open(_cache_path(index), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except (IOError, OSError, TypeError, ValueError) as exc:
        kodi.log("写缓存失败：%s" % exc, kodi.xbmc.LOGWARNING)


def _ttl():
    minutes = kodi.setting_int("update_interval", 30)
    return max(600, minutes * 60) - 30


# ---------------------------------------------------------------- 生活指数


def _life_icon_dir():
    return os.path.join(kodi.addon_path(), "resources", "media", "life")


def _life_cache_path(index):
    return os.path.join(kodi.profile_dir(), "life_%s.json" % index)


def save_life(index, tiles):
    """详情窗口要复用这批生活指数，别让它再联网一次。"""
    payload = {"saved_at": int(time.time()), "tiles": tiles}
    try:
        with open(_life_cache_path(index), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except (IOError, OSError, TypeError, ValueError) as exc:
        kodi.log("写生活指数缓存失败：%s" % exc, kodi.xbmc.LOGWARNING)


def load_life(index, ttl=6 * 3600):
    try:
        with open(_life_cache_path(index), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (IOError, OSError, ValueError):
        return []
    if time.time() - payload.get("saved_at", 0) > ttl:
        return []
    tiles = payload.get("tiles")
    return tiles if isinstance(tiles, list) else []


def build_life(client, conf, logger):
    """取网页那六格生活指数（v1/lifeindex + v1/vehicle_limit）。"""
    raw_index = None
    raw_limit = None
    try:
        raw_index = client.lifeindex(conf["lon"], conf["lat"], api.LIFE_FIELDS_QUERY)
    except Exception as exc:
        logger("生活指数取数失败：%s" % exc)
    try:
        raw_limit = client.vehicle_limit(conf["lon"], conf["lat"])
    except Exception as exc:
        logger("限号取数失败：%s" % exc)
    try:
        return api.build_life(raw_index, raw_limit, kodi.language(), _life_icon_dir())
    except Exception as exc:
        logger("生活指数整理失败：%s" % exc)
        return []


# ---------------------------------------------------------------- 云图


def radar_dirs(index):
    base = os.path.join(kodi.profile_dir(), "radar")
    return {
        "local": os.path.join(base, "local%s" % index),
        "forecast": os.path.join(base, "forecast%s" % index),
        "national": os.path.join(base, "national%s" % index),
        "aqi": os.path.join(base, "aqi%s" % index),
        "anim": os.path.join(base, "anim%s" % index),
        "maps": os.path.join(base, "maps"),
    }


def build_maps(client, conf, index, logger):
    """按设置里的两个方框生成云图页（降雨 / 空气）。"""
    if conf["lon"] is None or conf["lat"] is None:
        return []
    dirs = radar_dirs(index)
    try:
        anim.cleanup(dirs["anim"])
        radar.cleanup(dirs["local"])
        radar.cleanup(dirs["forecast"])
        radar.cleanup(dirs["aqi"])
    except Exception as exc:
        logger("清理旧帧失败：%s" % exc)
    return maps.build_pages(client, conf, dirs, logger)


# ---------------------------------------------------------------- 发布


DAILY_KEYS = ("Outlook", "OutlookIcon", "FanartCode", "LongDay", "ShortDay",
              "LongDate", "ShortDate", "HighTemperature", "LowTemperature",
              "TempDay", "TempMorn", "TempEve", "TempNight", "WindSpeed",
              "MaxWind", "WindDirection", "Humidity", "MinHumidity",
              "MaxHumidity", "Pressure", "Precipitation", "Cloudiness", "DewPoint")

HOURLY_KEYS = ("Time", "ShortDate", "Temperature", "Precipitation", "Outlook",
               "OutlookIcon", "FanartCode", "Cloudiness", "WindSpeed",
               "WindDirection", "Humidity")

CURRENT_KEYS = ("Location", "Condition", "ConditionIcon", "FanartCode",
                "Temperature", "FeelsLike", "DewPoint", "Humidity", "Wind",
                "WindDirection", "Pressure", "Precipitation", "Visibility",
                "Cloudiness")

TODAY_KEYS = ("Sunrise", "Sunset", "IsFetched")

MAP_KEYS = ("Area", "Layer", "Legend", "Heading", "Time")


def clear_window(window, index=1):
    """清掉上一轮留下的属性，避免皮肤显示残影。

    私有命名空间（Caiyun.*）的键是动态的（Day16.1..16 之类），所以把上一轮
    实际写过的键记在文件里，按记录清。
    """
    for key in CURRENT_KEYS:
        window.clearProperty("Current." + key)
    for key in TODAY_KEYS:
        window.clearProperty("Today." + key)
    for name in ("IsFetched", "Count"):
        window.clearProperty("Daily." + name)
        window.clearProperty("Hourly." + name)
        window.clearProperty("Alerts." + name)
    window.clearProperty("Map.IsFetched")
    window.clearProperty("Forecast.IsFetched")
    for index_ in range(1, 8):
        for key in DAILY_KEYS:
            window.clearProperty("Daily.%d.%s" % (index_, key))
        for key in ("Outlook", "OutlookIcon", "FanartCode", "HighTemperature",
                    "LowTemperature"):
            window.clearProperty("Day%d.%s" % (index_ - 1, key))
    for index_ in range(1, HOURLY_LIMIT + 1):
        for key in HOURLY_KEYS:
            window.clearProperty("Hourly.%d.%s" % (index_, key))
    for index_ in range(1, 6):
        for key in MAP_KEYS:
            window.clearProperty("Map.%d.%s" % (index_, key))
        for key in ("Title", "Description", "Type", "Level", "Weight",
                    "Source", "Location", "Code", "Time"):
            window.clearProperty("Alerts.%d.%s" % (index_, key))
    for key in _load_keys(index):
        window.clearProperty(key)


def _keys_path(index):
    return os.path.join(kodi.profile_dir(), "keys_%s.json" % index)


def _load_keys(index):
    try:
        with open(_keys_path(index), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_keys(index, props):
    try:
        with open(_keys_path(index), "w", encoding="utf-8") as handle:
            json.dump(sorted(props.keys()), handle)
    except (IOError, OSError, TypeError):
        pass


def publish(props, index=1):
    window = kodi.window()
    clear_window(window, index)
    for key, value in props.items():
        try:
            window.setProperty(key, "" if value is None else str(value))
        except Exception as exc:
            kodi.log("写属性 %s 失败：%s" % (key, exc), kodi.xbmc.LOGWARNING)
    _save_keys(index, props)
    return window


# ---------------------------------------------------------------- 更新


def _client():
    return net.Client(
        timeout=20,
        logger=lambda message: kodi.log(message),
        verify_ssl=not kodi.setting_bool("insecure_ssl", False),
    )


def _provider_name():
    """Estuary 天气页副标题的文字：写自己，别让上一个插件残留。"""
    name = (kodi.localize(32000) or "").strip()
    if name:
        return name
    return "彩云天气" if kodi.language() != "en" else "Caiyun Weather"


def update(index):
    try:
        conf = slot_conf(index)
    except Exception as exc:
        kodi.log("读取配置失败：%s" % exc, kodi.xbmc.LOGERROR)
        return False

    if conf["lon"] is None:
        kodi.debug("槽 %s 没有坐标，跳过" % index)
        return False

    logger = kodi.log
    data = _read_cache(index, _ttl())
    source = "cache"
    client = None

    if data is None:
        try:
            client = _client()
            raw = client.weather(conf["lon"], conf["lat"],
                                 days=max(DAILY_LIMIT, 16),
                                 hours=max(HOURLY_LIMIT, 24))
            data = api.normalize(raw)
            source = "network"
            _write_cache(index, _jsonable(data))
        except Exception as exc:
            kodi.log("取数失败：%s" % exc, kodi.xbmc.LOGERROR)
            _publish_failure(index, conf["name"], str(exc))
            return False
    else:
        data = _restore(data)

    data["city"] = conf["name"]
    logger("槽 %s（%s）数据来自 %s" % (index, conf["name"], source))

    maps = []
    try:
        maps = build_maps(client or _client(), conf, index, logger)
    except Exception as exc:
        logger("云图构建失败：%s" % exc)
    if kodi.setting_bool("radar_enabled", True):
        logger("云图 %d 页" % len(maps))

    life = []
    try:
        life = build_life(client or _client(), conf, logger)
    except Exception as exc:
        logger("生活指数构建失败：%s" % exc)
    logger("生活指数 %d 格" % len(life))
    if life:
        save_life(index, life)

    options = mapper._Options(
        city=conf["name"],
        lang=kodi.language(),
        units=kodi.setting_bool("units_mode", True),
        maps=maps,
        provider=_provider_name(),
        life=life,
    )
    props = mapper.build(data, options)
    publish(props, index)
    logger("已发布 %d 条属性" % len(props))

    _maybe_notify(index, data.get("alerts") or [])
    return True


def _publish_failure(index, city, message):
    props = mapper.build_failure(message, kodi.language(), _provider_name())
    props["Current.Location"] = city or ""
    publish(props, index)


def _maybe_notify(index, alerts):
    if not kodi.setting_bool("notify_alarm", True) or not alerts:
        return
    path = os.path.join(kodi.profile_dir(), "alerts_%s.json" % index)
    seen = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            seen = json.load(handle)
    except (IOError, OSError, ValueError):
        seen = []
    fresh = [alert for alert in alerts if alert.get("id") and alert["id"] not in seen]
    if fresh:
        alert = fresh[0]
        kodi.notify(alert.get("title") or ", ".join(
            [alert.get("type_zh", ""), alert.get("level_zh", "")]),
            heading=kodi.localize(32121))
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([alert.get("id") for alert in alerts if alert.get("id")], handle)
    except (IOError, OSError, TypeError):
        pass


# ---------------------------------------------------------------- 缓存编解码


def _jsonable(data):
    """datetime → ISO 字符串，才存得进 JSON。"""
    import copy

    def convert(value):
        if hasattr(value, "isoformat"):
            return {"__dt__": value.isoformat()}
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    return convert(copy.deepcopy(data))


def _restore(data):
    def convert(value):
        if isinstance(value, dict):
            if "__dt__" in value and len(value) == 1:
                return api.parse_dt(value["__dt__"])
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    return convert(data)


def properties_for(index):
    """给详情窗口/云图窗口复用同一套数据。"""
    conf = slot_conf(index)
    if conf["lon"] is None:
        return None, conf
    cached = _read_cache(index, 3 * 3600)
    data = _restore(cached) if cached else None
    if data is None:
        try:
            client = _client()
            raw = client.weather(conf["lon"], conf["lat"], days=16, hours=48)
            data = api.normalize(raw)
            _write_cache(index, _jsonable(data))
        except Exception as exc:
            kodi.log("详情取数失败：%s" % exc)
            return None, conf
    data["city"] = conf["name"]
    data["life_tiles"] = load_life(index)
    return data, conf
