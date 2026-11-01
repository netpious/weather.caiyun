# -*- coding: utf-8 -*-
"""彩云数据 → 规范化的中间结构（纯函数，可离线单测）。

原始响应字段很多且嵌套，这里只做"掏出来 + 单位统一"，不做任何 Kodi 相关的
格式化；格式化在 mapper 里做。
"""

import datetime
import math
import os
import time

TZ = datetime.timezone(datetime.timedelta(hours=8))


class ApiError(Exception):
    pass


# ---------------------------------------------------------------- 小工具


def parse_dt(text):
    """'2026-10-31T07:00+08:00' → datetime；失败返回 None。"""
    if not text:
        return None
    raw = str(text).strip()
    try:
        value = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=TZ)
    return value


def wind_kmh(speed_ms):
    try:
        return float(speed_ms) * 3.6
    except (TypeError, ValueError):
        return 0.0


def dew_point(temp_c, humidity):
    """Magnus 公式，输入相对湿度 0–1。"""
    try:
        temp_c = float(temp_c)
        rh = float(humidity)
    except (TypeError, ValueError):
        return None
    if rh <= 0:
        return None
    rh = min(max(rh, 0.01), 1.0)
    a, b = 17.27, 237.7
    gamma = (a * temp_c) / (b + temp_c) + math.log(rh)
    return (b * gamma) / (a - gamma)


# ---------------------------------------------------------------- 预警

ALERT_TYPES = {
    "01": ("台风", "Typhoon"),
    "02": ("暴雨", "Rainstorm"),
    "03": ("暴雪", "Snowstorm"),
    "04": ("寒潮", "Cold wave"),
    "05": ("大风", "Gale"),
    "06": ("沙尘暴", "Sandstorm"),
    "07": ("高温", "Heat"),
    "08": ("干旱", "Drought"),
    "09": ("雷电", "Lightning"),
    "10": ("冰雹", "Hail"),
    "11": ("霜冻", "Frost"),
    "12": ("大雾", "Fog"),
    "13": ("霾", "Haze"),
    "14": ("道路结冰", "Icy road"),
    "15": ("森林火灾", "Forest fire"),
    "16": ("雷雨大风", "Thunderstorm gale"),
}

ALERT_LEVELS = {
    "01": ("蓝色", "Blue", 1),
    "02": ("黄色", "Yellow", 2),
    "03": ("橙色", "Orange", 3),
    "04": ("红色", "Red", 4),
}


def decode_alert_code(code):
    """'0901' → (类型中文, 类型英文, 级别中文, 级别英文, 级别权重)。"""
    raw = str(code or "").strip()
    kind = ALERT_TYPES.get(raw[:2], ("预警", "Alert"))
    level = ALERT_LEVELS.get(raw[2:4], ("未知", "Unknown", 0))
    return kind[0], kind[1], level[0], level[1], level[2]


def normalize_alerts(block):
    items = []
    content = (block or {}).get("content") or []
    for entry in content:
        if not isinstance(entry, dict):
            continue
        zh_type, en_type, zh_level, en_level, weight = decode_alert_code(entry.get("code"))
        published = entry.get("pubtimestamp")
        items.append(
            {
                "id": entry.get("alertId") or entry.get("adcode") or "",
                "code": entry.get("code") or "",
                "type_zh": zh_type,
                "type_en": en_type,
                "level_zh": zh_level,
                "level_en": en_level,
                "weight": weight,
                "title": entry.get("title") or "",
                "description": entry.get("description") or "",
                "location": entry.get("location") or "",
                "province": entry.get("province") or "",
                "city": entry.get("city") or "",
                "county": entry.get("county") or "",
                "source": entry.get("source") or "",
                "status": entry.get("status") or "",
                "published": int(published) if isinstance(published, (int, float)) else 0,
            }
        )
    items.sort(key=lambda item: (-item["weight"], -item["published"]))
    return items


# ---------------------------------------------------------------- 主结构


def _series(pairs, key="value"):
    out = []
    for entry in pairs or []:
        if not isinstance(entry, dict):
            continue
        moment = parse_dt(entry.get("datetime"))
        if moment is None:
            continue
        out.append(
            {
                "time": moment,
                "value": entry.get(key),
                "speed": entry.get("speed"),
                "direction": entry.get("direction"),
            }
        )
    return out


def normalize_hourly(block):
    """把 hourly 的分列数组拼成按时间的列表。"""
    block = block or {}
    columns = {}
    for name, value in block.items():
        if name in ("status", "description"):
            continue
        if isinstance(value, list):
            columns[name] = value
        elif isinstance(value, dict):
            for sub, series in value.items():
                if isinstance(series, list):
                    columns["%s_%s" % (name, sub)] = series

    rows = {}
    for column, series in columns.items():
        for entry in series:
            if not isinstance(entry, dict):
                continue
            moment = entry.get("datetime")
            if not moment:
                continue
            row = rows.setdefault(moment, {"datetime": moment})
            if column == "wind":
                row["wind_speed"] = entry.get("speed")
                row["wind_direction"] = entry.get("direction")
            else:
                row[column] = entry.get("value")

    result = []
    for moment in sorted(rows, key=lambda key: parse_dt(key) or datetime.datetime.min.replace(tzinfo=TZ)):
        raw = rows[moment]
        when = parse_dt(moment)
        if when is None:
            continue
        result.append(
            {
                "time": when,
                "temperature": raw.get("temperature"),
                "precipitation": raw.get("precipitation"),
                "humidity": raw.get("humidity"),
                "cloudrate": raw.get("cloudrate"),
                "skycon": raw.get("skycon"),
                "pressure": raw.get("pressure"),
                "visibility": raw.get("visibility"),
                "dswrf": raw.get("dswrf"),
                "wind_speed": raw.get("wind_speed"),
                "wind_direction": raw.get("wind_direction"),
                "aqi": raw.get("air_quality_aqi"),
                "pm25": raw.get("air_quality_pm25"),
            }
        )
    return result


def _daily_series(pairs):
    out = []
    for entry in pairs or []:
        if not isinstance(entry, dict):
            continue
        moment = parse_dt(entry.get("date"))
        out.append(
            {
                "date": moment,
                "max": entry.get("max"),
                "min": entry.get("min"),
                "avg": entry.get("avg"),
            }
        )
    return out


def normalize_daily(block):
    block = block or {}
    dates = []
    seen = set()

    def collect(pairs):
        for entry in pairs or []:
            if isinstance(entry, dict) and entry.get("date") and entry["date"] not in seen:
                seen.add(entry["date"])
                dates.append(entry["date"])

    for key in ("astro", "temperature", "precipitation", "wind", "humidity",
                "cloudrate", "pressure", "visibility", "skycon", "dswrf"):
        collect(block.get(key))
    for sub in (block.get("life_index") or {}).values():
        collect(sub)
    for sub in (block.get("air_quality") or {}).values():
        collect(sub)

    dates.sort(key=lambda text: parse_dt(text) or datetime.datetime.min.replace(tzinfo=TZ))

    astro = {entry.get("date"): entry for entry in block.get("astro") or [] if isinstance(entry, dict)}
    skycon = {entry.get("date"): entry.get("value") for entry in block.get("skycon") or [] if isinstance(entry, dict)}
    skycon_day = {entry.get("date"): entry.get("value") for entry in block.get("skycon_08h_20h") or [] if isinstance(entry, dict)}
    skycon_night = {entry.get("date"): entry.get("value") for entry in block.get("skycon_20h_32h") or [] if isinstance(entry, dict)}

    def index(pairs):
        """按原始日期字符串建索引（dates 里存的就是原始字符串）。"""
        out = {}
        for entry in pairs or []:
            if isinstance(entry, dict) and entry.get("date"):
                out[entry["date"]] = entry
        return out

    def speed(value):
        """daily.wind 的 max/min/avg 是 {speed, direction} 对象。"""
        if isinstance(value, dict):
            value = value.get("speed")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def direction(value):
        if isinstance(value, dict):
            value = value.get("direction")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    temperature = index(block.get("temperature"))
    precipitation = index(block.get("precipitation"))
    wind = index(block.get("wind"))
    humidity = index(block.get("humidity"))
    cloudrate = index(block.get("cloudrate"))
    pressure = index(block.get("pressure"))
    visibility = index(block.get("visibility"))
    dswrf = index(block.get("dswrf"))
    aqi = index((block.get("air_quality") or {}).get("aqi"))
    pm25 = index((block.get("air_quality") or {}).get("pm25"))
    life = {}
    for name, pairs in (block.get("life_index") or {}).items():
        life[name] = {entry.get("date"): entry for entry in pairs or [] if isinstance(entry, dict)}

    result = []
    for date in dates:
        entry = astro.get(date) or {}
        sunrise = ((entry.get("sunrise") or {}).get("time") if isinstance(entry.get("sunrise"), dict) else None)
        sunset = ((entry.get("sunset") or {}).get("time") if isinstance(entry.get("sunset"), dict) else None)
        result.append(
            {
                "date": parse_dt(date),
                "skycon": skycon.get(date),
                "skycon_day": skycon_day.get(date),
                "skycon_night": skycon_night.get(date),
                "temp_max": (temperature.get(date) or {}).get("max"),
                "temp_min": (temperature.get(date) or {}).get("min"),
                "temp_avg": (temperature.get(date) or {}).get("avg"),
                "precip_max": (precipitation.get(date) or {}).get("max"),
                "precip_avg": (precipitation.get(date) or {}).get("avg"),
                "wind_max": speed((wind.get(date) or {}).get("max")),
                "wind_min": speed((wind.get(date) or {}).get("min")),
                "wind_avg": speed((wind.get(date) or {}).get("avg")),
                "wind_dir": direction((wind.get(date) or {}).get("avg")),
                "humidity_max": (humidity.get(date) or {}).get("max"),
                "humidity_min": (humidity.get(date) or {}).get("min"),
                "humidity_avg": (humidity.get(date) or {}).get("avg"),
                "cloudrate_avg": (cloudrate.get(date) or {}).get("avg"),
                "pressure_avg": (pressure.get(date) or {}).get("avg"),
                "visibility_avg": (visibility.get(date) or {}).get("avg"),
                "dswrf_avg": (dswrf.get(date) or {}).get("avg"),
                "aqi_avg": (aqi.get(date) or {}).get("avg"),
                "pm25_avg": (pm25.get(date) or {}).get("avg"),
                "sunrise": sunrise,
                "sunset": sunset,
                "life": {name: (series.get(date) or {}) for name, series in life.items()},
            }
        )
    return result


def normalize(raw):
    """原始 weather 响应 → 统一结构。"""
    if not isinstance(raw, dict):
        raise ApiError("响应为空")
    if raw.get("status") != "ok":
        raise ApiError("状态异常：%s" % raw.get("status"))

    result = raw.get("result") or {}
    realtime = result.get("realtime") or {}
    if not realtime or realtime.get("status") not in (None, "ok"):
        raise ApiError("实况数据缺失")

    air = realtime.get("air_quality") or {}
    aqi_block = air.get("aqi") or {}
    aqi_desc = air.get("description") or {}
    wind = realtime.get("wind") or {}
    life_now = realtime.get("life_index") or {}
    local = ((realtime.get("precipitation") or {}).get("local") or {})
    nearest = ((realtime.get("precipitation") or {}).get("nearest") or {})

    minutely = result.get("minutely") or {}
    hourly = normalize_hourly(result.get("hourly") or {})
    daily = normalize_daily(result.get("daily") or {})

    location = raw.get("location") or []
    lat = location[0] if len(location) > 0 else None
    lon = location[1] if len(location) > 1 else None

    return {
        "fetched_at": int(time.time()),
        "server_time": raw.get("server_time"),
        "timezone": raw.get("timezone"),
        "tzshift": raw.get("tzshift"),
        "lat": lat,
        "lon": lon,
        "realtime": {
            "temperature": realtime.get("temperature"),
            "apparent": realtime.get("apparent_temperature"),
            "humidity": realtime.get("humidity"),
            "cloudrate": realtime.get("cloudrate"),
            "skycon": realtime.get("skycon"),
            "visibility": realtime.get("visibility"),
            "dswrf": realtime.get("dswrf"),
            "pressure": realtime.get("pressure"),
            "wind_speed": wind_kmh(wind.get("speed")),
            "wind_direction": wind.get("direction"),
            "precip_intensity": local.get("intensity"),
            "precip_distance": nearest.get("distance"),
            "precip_source": local.get("datasource"),
            "observed_at": air.get("obs_time"),
        },
        "air": {
            "aqi": aqi_block.get("chn"),
            "aqi_usa": aqi_block.get("usa"),
            "desc_zh": aqi_desc.get("chn"),
            "desc_en": aqi_desc.get("usa"),
            "pm25": air.get("pm25"),
            "pm10": air.get("pm10"),
            "o3": air.get("o3"),
            "so2": air.get("so2"),
            "no2": air.get("no2"),
            "co": air.get("co"),
            "pm25_iaqi": air.get("pm25_iaqi_chn"),
            "pm10_iaqi": air.get("pm10_iaqi_chn"),
            "o3_iaqi": air.get("o3_iaqi_chn"),
            "so2_iaqi": air.get("so2_iaqi_chn"),
            "no2_iaqi": air.get("no2_iaqi_chn"),
            "co_iaqi": air.get("co_iaqi_chn"),
        },
        "life_now": life_now,
        "minutely": {
            "status": minutely.get("status"),
            "source": minutely.get("datasource"),
            "description": minutely.get("description") or "",
            "probability": minutely.get("probability") or [],
            "accumulation": minutely.get("accumulation") or [],
            "series": minutely.get("precipitation_2h") or [],
        },
        "hourly": hourly,
        "daily": daily,
        "alerts": normalize_alerts(result.get("alert")),
        "keypoint": result.get("forecast_keypoint") or "",
        "hourly_text": (result.get("hourly") or {}).get("description") or "",
    }


# ---------------------------------------------------------------- 生活指数

# 网页 h5 天气页那六格，顺序与网页一致：(接口字段, 内部键, 中文名, 英文名, 图标)
LIFE_FIELDS = [
    ("SportIndex", "sport", "运动", "Sport", "sport.png"),
    ("__vehicle__", "limit", "限号", "Traffic limit", "limit.png"),
    ("UltravioletIndex", "uv", "紫外线", "UV", "uv.png"),
    ("DressingIndex", "dressing", "穿衣", "Dressing", "dressing.png"),
    ("ColdRiskIndex", "coldrisk", "感冒", "Cold risk", "coldrisk.png"),
    ("TravelIndex", "travel", "旅游", "Travel", "travel.png"),
]

LIFE_FIELDS_QUERY = ",".join(
    field for field, _key, _zh, _en, _icon in LIFE_FIELDS if not field.startswith("__")
)


def normalize_lifeindex(raw):
    """v1/lifeindex → {key: {level, desc, detail}}（只取今天那一格）。"""
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        raise ApiError("生活指数状态异常")
    result = raw.get("result") or {}
    out = {}
    for field, key, _zh, _en, _icon in LIFE_FIELDS:
        if field.startswith("__"):
            continue
        rows = result.get(field)
        if not isinstance(rows, list) or not rows:
            continue
        row = rows[0]
        if not isinstance(row, dict):
            continue
        desc = str(row.get("desc") or "").strip()
        if not desc:
            continue
        out[key] = {
            "level": row.get("level"),
            "desc": desc,
            "detail": str(row.get("detail") or "").strip(),
            "datetime": row.get("datetime") or "",
        }
    return out


def normalize_vehicle_limit(raw, today=None):
    """v1/vehicle_limit → 今天这一格的限行描述。"""
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        return None
    rows = [row for row in raw.get("result") or [] if isinstance(row, dict)]
    if not rows:
        return None
    wanted = (today or datetime.datetime.now(TZ).strftime("%Y-%m-%d"))
    row = None
    for entry in rows:
        if str(entry.get("date") or "").startswith(wanted):
            row = entry
            break
    if row is None:
        row = rows[0]
    limits = [item for item in row.get("limits") or [] if isinstance(item, dict)]
    if not limits:
        return None
    numbers = str(limits[0].get("limitNumber") or "").strip()
    if not numbers:
        return None
    if numbers.upper() == "N":
        desc = "禁止通行"
    elif numbers.upper() == "W":
        desc = "今日不限行"
    else:
        parts = [part for part in numbers.replace("，", ",").replace("、", ",").split(",") if part]
        desc = "尾号 " + "、".join(parts) if parts else numbers
    return {
        "level": None,
        "desc": desc,
        "detail": str(limits[0].get("limitArea") or "").strip(),
        "datetime": str(row.get("date") or ""),
        "city": str(row.get("cityName") or "").strip(),
    }


def build_life(raw_index, raw_limit, lang="zh", icon_dir=""):
    """把两份原始响应合成皮肤要的六格。"""
    index = {}
    try:
        index = normalize_lifeindex(raw_index)
    except ApiError:
        index = {}
    try:
        limit = normalize_vehicle_limit(raw_limit)
    except ApiError:
        limit = None
    if limit:
        index["limit"] = limit

    tiles = []
    for _field, key, zh, en, icon in LIFE_FIELDS:
        item = index.get(key)
        if not item:
            continue
        tiles.append(
            {
                "key": key,
                "name": zh if lang != "en" else en,
                "desc": item.get("desc") or "",
                "detail": item.get("detail") or "",
                "level": item.get("level"),
                "icon": os.path.join(icon_dir, icon) if icon_dir else "",
            }
        )
    return tiles


# ---------------------------------------------------------------- 城市


def parse_place(raw):
    """v2/place 响应 → [{name, address, lat, lon}]。"""
    places = []
    for entry in (raw or {}).get("places") or []:
        if not isinstance(entry, dict):
            continue
        location = entry.get("location") or {}
        try:
            lat = float(location.get("lat"))
            lon = float(location.get("lng"))
        except (TypeError, ValueError):
            continue
        if not (-90 < lat < 90 and -180 < lon < 180):
            continue
        address = (entry.get("formatted_address") or "").strip()
        name = (entry.get("name") or address).strip()
        places.append({"name": name, "address": address, "lat": lat, "lon": lon})
    return places


def short_place_name(address):
    """'中国 浙江省 杭州市 西湖区' → '杭州市 西湖区' 之类的简短名。"""
    parts = [part for part in str(address or "").replace(",", " ").split() if part]
    parts = [part for part in parts if part not in ("中国", "中华人民共和国")]
    if not parts:
        return str(address or "").strip()
    if len(parts) >= 2:
        return " ".join(parts[-2:])
    return parts[-1]


# ---------------------------------------------------------------- 雷达


def parse_image_frames(entries):
    """[[url, ts, [south, west, north, east]], ...] → 帧列表（按时间升序）。"""
    frames = []
    for entry in entries or []:
        try:
            url = entry[0]
            stamp = int(entry[1])
            bounds = [float(value) for value in entry[2]]
        except (IndexError, TypeError, ValueError):
            continue
        if len(bounds) != 4:
            continue
        frames.append({"url": url, "time": stamp, "bounds": bounds})
    frames.sort(key=lambda item: item["time"])
    return frames


def parse_radar(raw):
    """雷达响应 → {station, frames, forecast}。"""
    if not isinstance(raw, dict):
        raise ApiError("雷达响应为空")
    if raw.get("status") != "ok":
        raise ApiError("雷达状态异常：%s" % raw.get("status"))
    frames = parse_image_frames(raw.get("images"))
    if not frames:
        raise ApiError("雷达没有可用帧")
    return {
        "station": raw.get("station") or "",
        "frames": frames,
        "forecast": parse_image_frames(raw.get("forecast_images")),
    }


def parse_aqi(raw):
    """空气质量图层响应 → {frames, forecast}。"""
    if not isinstance(raw, dict):
        raise ApiError("空气质量响应为空")
    if raw.get("status") != "ok":
        raise ApiError("空气质量状态异常：%s" % raw.get("status"))
    frames = parse_image_frames(raw.get("images"))
    forecast = parse_image_frames(raw.get("forecast_images"))
    if not frames and not forecast:
        raise ApiError("空气质量图层没有可用帧")
    return {"station": "aqi", "frames": frames, "forecast": forecast}
