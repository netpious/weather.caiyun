# -*- coding: utf-8 -*-
"""规范化数据 → Kodi 窗口属性。纯函数，离线可测。

设计原则（沿用中国天气插件的经验）：

* `Current.*` 写纯数字，Kodi 自己按系统单位换算；
* `Daily.*` / `Hourly.*` 必须自带单位，否则 Kodi 按华氏度理解；
* 每个 `*.IsFetched` 只有在该块真的有数据时才写，否则皮肤会画出空面板；
* Estuary 只读十来个属性，其余全部进 `Caiyun.*` 私有命名空间，交给插件
  自己的详情窗口/云图窗口使用。
"""

import datetime

from . import api, codes

TZ = api.TZ


def _num(value, digits=0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if digits <= 0:
        return "%d" % round(number)
    return ("%." + str(digits) + "f") % number


def _temp(value, units=False, digits=0):
    text = _num(value, digits)
    if not text:
        return ""
    return text + "°C" if units else text


def _clock(moment, fallback=""):
    if moment is None:
        return fallback
    return moment.strftime("%H:%M")


def _kmh(value):
    text = _num(value)
    return text + " km/h" if text else ""


def _percent(fraction):
    text = _num((fraction or 0) * 100 if fraction is not None else None)
    return text + "%" if text else ""


def _mm(value, digits=1):
    text = _num(value, digits)
    return text + " mm" if text else ""


class _Options(object):
    """把设置项收拢，避免 mapper 直接依赖 kodi 模块。"""

    def __init__(self, city="", lang="zh", units=True, maps=None, provider="", life=None):
        self.city = city
        self.lang = lang
        self.units = units
        self.maps = maps or []
        self.provider = provider
        self.life = life or []


def _hourly_buckets(hourly):
    """按日期把小时数据分成 早晨/白天/傍晚/夜间 四段平均温度。"""
    buckets = {}
    for entry in hourly or []:
        moment = entry.get("time")
        temperature = entry.get("temperature")
        if moment is None or temperature is None:
            continue
        key = moment.date()
        slot = buckets.setdefault(key, {"morn": [], "day": [], "eve": [], "night": []})
        hour = moment.hour
        if 6 <= hour <= 10:
            slot["morn"].append(temperature)
        elif 11 <= hour <= 17:
            slot["day"].append(temperature)
        elif 18 <= hour <= 21:
            slot["eve"].append(temperature)
        else:
            slot["night"].append(temperature)

    result = {}
    for key, slot in buckets.items():
        item = {}
        for name, values in slot.items():
            if values:
                item[name] = sum(values) / float(len(values))
        result[key] = item
    return result


def build(data, options=None):
    options = options or _Options()
    lang = options.lang
    units = options.units
    props = {}

    def put(key, value):
        if value is None:
            return
        text = value if isinstance(value, str) else str(value)
        if text == "":
            return
        props[key] = text

    realtime = data.get("realtime") or {}
    today = (data.get("daily") or [{}])[0]
    city = options.city or ""

    # ---------------------------------------------------------- 实况
    condition = codes.describe(realtime.get("skycon"), lang)
    # Estuary 的天气页副标题读的就是这个属性；不写就会被上一个天气插件
    # 留下的名字顶替（换插件后还显示「中国天气」）。
    put("WeatherProvider", options.provider)
    put("Location", city)
    put("Current.Location", city)
    put("Current.Condition", condition["text"])
    put("Current.ConditionIcon", condition["icon"])
    put("Current.FanartCode", "%d" % condition["code"])
    put("Current.Temperature", _temp(realtime.get("temperature")))
    put("Current.FeelsLike", _temp(realtime.get("apparent")))
    put("Current.DewPoint", _temp(api.dew_point(realtime.get("temperature"),
                                                realtime.get("humidity"))))
    put("Current.Humidity", _percent(realtime.get("humidity")))
    put("Current.Wind", _kmh(realtime.get("wind_speed")))
    put("Current.WindDirection", codes.wind_direction(realtime.get("wind_direction"), lang))
    pressure = realtime.get("pressure")
    if pressure:
        put("Current.Pressure", "%d hPa" % round(float(pressure) / 100.0))
    if realtime.get("visibility") is not None:
        put("Current.Visibility", _num(realtime.get("visibility"), 1) + " km")
    put("Current.Cloudiness", _percent(realtime.get("cloudrate")))

    # 实况降水用小时数据的第一格（mm/h），没有就退化到强度描述
    hourly = data.get("hourly") or []
    precip_now = None
    for entry in hourly:
        if entry.get("precipitation") is not None:
            precip_now = entry["precipitation"]
            break
    if precip_now is not None:
        put("Current.Precipitation", _mm(precip_now))
    else:
        text = codes.intensity_text(realtime.get("precip_intensity"), lang)
        put("Current.Precipitation", text)

    # ---------------------------------------------------------- 今日
    put("Today.IsFetched", "true")
    put("Today.Sunrise", today.get("sunrise") or "")
    put("Today.Sunset", today.get("sunset") or "")

    # ---------------------------------------------------------- 逐日
    daily = data.get("daily") or []
    if daily:
        put("Daily.IsFetched", "true")
    buckets = _hourly_buckets(hourly)
    limit_daily = min(len(daily), 7)
    for index in range(limit_daily):
        item = daily[index]
        day = index + 1
        prefix = "Daily.%d." % day
        date = item.get("date")
        skycon = item.get("skycon_day") or item.get("skycon")
        outlook = codes.describe(skycon, lang)
        put(prefix + "Outlook", outlook["text"])
        put(prefix + "OutlookIcon", "%02d.png" % outlook["code"])
        put(prefix + "FanartCode", "%d" % outlook["code"])
        put(prefix + "LongDay", codes.long_weekday(date, lang))
        put(prefix + "ShortDay", codes.short_weekday(date, lang))
        put(prefix + "LongDate", _long_date(date, lang))
        put(prefix + "ShortDate", date.strftime("%m-%d") if date else "")
        put(prefix + "HighTemperature", _temp(item.get("temp_max"), units))
        put(prefix + "LowTemperature", _temp(item.get("temp_min"), units))
        put(prefix + "TempDay", _temp(item.get("temp_avg"), units))
        slot = buckets.get(date.date()) if date else None
        if slot:
            put(prefix + "TempMorn", _temp(slot.get("morn"), units))
            put(prefix + "TempEve", _temp(slot.get("eve"), units))
            put(prefix + "TempNight", _temp(slot.get("night"), units))
        put(prefix + "WindSpeed", _kmh(item.get("wind_avg")))
        put(prefix + "MaxWind", _kmh(item.get("wind_max")))
        put(prefix + "WindDirection",
            codes.wind_direction(item.get("wind_dir"), lang))
        put(prefix + "Humidity", _percent(item.get("humidity_avg")))
        put(prefix + "MinHumidity", _percent(item.get("humidity_min")))
        put(prefix + "MaxHumidity", _percent(item.get("humidity_max")))
        put(prefix + "Pressure", _num((item.get("pressure_avg") or 0) / 100.0)
            + " hPa" if item.get("pressure_avg") else "")
        put(prefix + "Precipitation", _mm(item.get("precip_max")))
        put(prefix + "Cloudiness", _percent(item.get("cloudrate_avg")))
        put(prefix + "DewPoint", _temp(api.dew_point(item.get("temp_avg"),
                                                     item.get("humidity_avg"))))

    # 兼容旧皮肤的 Day0..Day6
    for index in range(min(len(daily), 7)):
        item = daily[index]
        prefix = "Day%d." % index
        skycon = item.get("skycon_day") or item.get("skycon")
        outlook = codes.describe(skycon, lang)
        put(prefix + "Outlook", outlook["text"])
        put(prefix + "OutlookIcon", "%02d.png" % outlook["code"])
        put(prefix + "FanartCode", "%d" % outlook["code"])
        put(prefix + "HighTemperature", _temp(item.get("temp_max"), units))
        put(prefix + "LowTemperature", _temp(item.get("temp_min"), units))

    # ---------------------------------------------------------- 逐小时
    hourly_sorted = [entry for entry in hourly if entry.get("time") is not None]
    hourly_sorted.sort(key=lambda entry: entry["time"])
    if hourly_sorted:
        put("Hourly.IsFetched", "true")
    for index in range(min(len(hourly_sorted), 16)):
        item = hourly_sorted[index]
        prefix = "Hourly.%d." % (index + 1)
        moment = item["time"]
        outlook = codes.describe(item.get("skycon"), lang)
        put(prefix + "Time", _clock(moment))
        put(prefix + "ShortDate", moment.strftime("%m-%d"))
        put(prefix + "Temperature", _temp(item.get("temperature"), units))
        put(prefix + "Precipitation", _mm(item.get("precipitation")))
        put(prefix + "Outlook", outlook["text"])
        put(prefix + "OutlookIcon", "%02d.png" % outlook["code"])
        put(prefix + "FanartCode", "%d" % outlook["code"])
        put(prefix + "Cloudiness", _percent(item.get("cloudrate")))
        put(prefix + "WindSpeed", _kmh(api.wind_kmh(item.get("wind_speed"))))
        put(prefix + "WindDirection",
            codes.wind_direction(item.get("wind_direction"), lang))
        put(prefix + "Humidity", _percent(item.get("humidity")))

    # ---------------------------------------------------------- 预警
    alerts = data.get("alerts") or []
    put("Alerts.IsFetched", "true" if alerts else "false")
    put("Alerts.Count", "%d" % len(alerts))
    for index, alert in enumerate(alerts[:5], 1):
        prefix = "Alerts.%d." % index
        put(prefix + "Title", alert.get("title"))
        put(prefix + "Description", alert.get("description"))
        put(prefix + "Type", alert.get("type_en") if lang == "en" else alert.get("type_zh"))
        put(prefix + "Level", alert.get("level_en") if lang == "en" else alert.get("level_zh"))
        put(prefix + "Weight", "%d" % alert.get("weight", 0))
        put(prefix + "Source", alert.get("source"))
        put(prefix + "Location", alert.get("location"))
        put(prefix + "Code", alert.get("code"))
        if alert.get("published"):
            stamp = datetime.datetime.fromtimestamp(alert["published"], TZ)
            put(prefix + "Time", stamp.strftime("%m-%d %H:%M"))

    # ---------------------------------------------------------- 预报总览
    put("Forecast.IsFetched", "true")
    put("Forecast.City", city)
    put("Forecast.Country", "中国" if lang != "en" else "China")
    put("Forecast.Updated", _updated(data.get("fetched_at")))
    if data.get("lat") is not None:
        put("Forecast.Latitude", "%.4f" % float(data["lat"]))
    if data.get("lon") is not None:
        put("Forecast.Longitude", "%.4f" % float(data["lon"]))

    # ---------------------------------------------------------- 云图
    _build_maps(props, options.maps)

    # ---------------------------------------------------------- 彩云私有
    _build_caiyun(props, data, options, buckets)

    return props


def _updated(stamp):
    if not stamp:
        return ""
    moment = datetime.datetime.fromtimestamp(int(stamp), TZ)
    return moment.strftime("%Y-%m-%d %H:%M")


def _long_date(moment, lang):
    if moment is None:
        return ""
    if lang == "en":
        return moment.strftime("%B ") + "%d" % moment.day
    return "%d月%d日" % (moment.month, moment.day)


def _build_maps(props, maps):
    if not maps:
        return
    props["Map.IsFetched"] = "true"
    for index, item in enumerate(list(maps)[:5], 1):
        prefix = "Map.%d." % index
        for key in ("Area", "Layer", "Legend", "Heading", "Time"):
            value = item.get(key.lower())
            if value:
                props[prefix + key] = value


def _build_life(props, options):
    """网页天气页那六格生活指数，编号 1..6，皮肤按号取。"""
    tiles = options.life or []
    props["Caiyun.Life.Count"] = "%d" % len(tiles)
    for index, tile in enumerate(tiles[:6], 1):
        prefix = "Caiyun.Life.%d." % index
        props[prefix + "Key"] = tile.get("key") or ""
        props[prefix + "Name"] = tile.get("name") or ""
        props[prefix + "Desc"] = tile.get("desc") or ""
        detail = tile.get("detail") or ""
        if detail:
            props[prefix + "Detail"] = detail
        icon = tile.get("icon") or ""
        if icon:
            props[prefix + "Icon"] = icon
        level = tile.get("level")
        if level is not None and level != "":
            props[prefix + "Level"] = str(level)


def _build_caiyun(props, data, options, buckets):
    """私有命名空间：Kodi 标准属性装不下的部分。"""
    lang = options.lang
    realtime = data.get("realtime") or {}
    air = data.get("air") or {}
    daily = data.get("daily") or []
    hourly = data.get("hourly") or []
    minutely = data.get("minutely") or {}
    life = {}
    if daily:
        life = daily[0].get("life") or {}

    props["Caiyun.Available"] = "true"
    props["Caiyun.Updated"] = _updated(data.get("fetched_at"))

    # 空气质量
    aqi = air.get("aqi")
    level = codes.aqi_level(aqi, lang)
    props["Caiyun.AQI.Value"] = _num(aqi)
    props["Caiyun.AQI.Level"] = air.get("desc_zh") if lang != "en" else (level["text"] or "")
    props["Caiyun.AQI.Level2"] = level["text"]
    props["Caiyun.AQI.Color"] = level["color"]
    props["Caiyun.AQI.USA"] = _num(air.get("aqi_usa"))
    for key, label in (("pm25", "PM25"), ("pm10", "PM10"), ("o3", "O3"),
                       ("so2", "SO2"), ("no2", "NO2")):
        props["Caiyun.AQI." + label] = _num(air.get(key), 1)
    props["Caiyun.AQI.CO"] = _num(air.get("co"), 1)
    for key, label in (("pm25_iaqi", "PM25"), ("pm10_iaqi", "PM10"), ("o3_iaqi", "O3"),
                       ("so2_iaqi", "SO2"), ("no2_iaqi", "NO2"), ("co_iaqi", "CO")):
        props["Caiyun.AQI.IAQI." + label] = _num(air.get(key))

    # 分钟级降水
    text = minutely.get("description") or ""
    props["Caiyun.Minutely.Description"] = text
    probability = minutely.get("probability") or []
    if probability:
        props["Caiyun.Minutely.Probability"] = _percent(probability[0])
    accumulation = minutely.get("accumulation") or []
    if accumulation:
        props["Caiyun.Minutely.Accumulation"] = _mm(accumulation[0])
    series = minutely.get("series") or []
    if series:
        props["Caiyun.Minutely.Points"] = "%d" % len(series)
        peak = max(series) if series else 0
        props["Caiyun.Minutely.Peak"] = _num(peak, 2)

    props["Caiyun.Hourly.Text"] = data.get("hourly_text") or ""
    props["Caiyun.Keypoint"] = data.get("keypoint") or ""
    props["Caiyun.Hours"] = "%d" % len(hourly)
    props["Caiyun.Days"] = "%d" % len(daily)

    # 实况补充
    props["Caiyun.Current.CloudRate"] = _percent(realtime.get("cloudrate"))
    props["Caiyun.Current.Radiation"] = _num(realtime.get("dswrf"), 1)
    props["Caiyun.Current.Observed"] = _updated(realtime.get("observed_at"))
    props["Caiyun.Current.WindSpeed"] = _num(realtime.get("wind_speed"), 1)
    props["Caiyun.Current.WindBearing"] = codes.wind_degrees(realtime.get("wind_direction"))
    props["Caiyun.Current.WindLevel"] = "%d" % codes.wind_level(realtime.get("wind_speed"))
    props["Caiyun.Current.PrecipIntensity"] = codes.intensity_text(
        realtime.get("precip_intensity"), lang
    )

    # 生活指数
    index_names = [
        ("ultraviolet", "UV", "紫外线"),
        ("carWashing", "CarWashing", "洗车"),
        ("dressing", "Dressing", "穿衣"),
        ("comfort", "Comfort", "舒适度"),
        ("coldRisk", "ColdRisk", "感冒"),
    ]
    for key, label, zh in index_names:
        item = life.get(key) or {}
        if not item and key in ("ultraviolet", "comfort"):
            item = (realtime.get("life_index") or {}).get(key) or {}
        if not item:
            continue
        props["Caiyun.Life.%s.Level" % label] = str(item.get("index", ""))
        desc = item.get("desc") or ""
        props["Caiyun.Life.%s.Desc" % label] = desc
        props["Caiyun.Life.%s.Name" % label] = zh if lang != "en" else label

    # 生活指数（网页那六格：运动 / 限号 / 紫外线 / 穿衣 / 感冒 / 旅游）
    _build_life(props, options)

    # 16 天（超出 Kodi 7 天上限的部分也留一份）
    for index, item in enumerate(daily[:16]):
        prefix = "Caiyun.Day16.%d." % (index + 1)
        date = item.get("date")
        outlook = codes.describe(item.get("skycon_day") or item.get("skycon"), lang)
        props[prefix + "Date"] = date.strftime("%m-%d") if date else ""
        props[prefix + "Weekday"] = codes.short_weekday(date, lang)
        props[prefix + "Outlook"] = outlook["text"]
        props[prefix + "Icon"] = "%02d.png" % outlook["code"]
        props[prefix + "High"] = _temp(item.get("temp_max"), options.units)
        props[prefix + "Low"] = _temp(item.get("temp_min"), options.units)
        props[prefix + "Precip"] = _mm(item.get("precip_max"))
        props[prefix + "AQI"] = _num(item.get("aqi_avg"))
        props[prefix + "Sunrise"] = item.get("sunrise") or ""
        props[prefix + "Sunset"] = item.get("sunset") or ""

    # 云图清单（详情窗口用）
    frames = options.maps[0].get("frames") if options.maps else None
    if frames:
        props["Caiyun.Radar.Frames"] = "%d" % len(frames)


def build_failure(message, lang="zh", provider=""):
    """取数失败时写一组"安全"属性，避免皮肤显示上一轮的残影。"""
    props = {
        "WeatherProvider": provider,
        "Current.Location": "",
        "Current.Condition": message,
        "Current.ConditionIcon": codes.icon_path(26),
        "Current.FanartCode": "26",
        "Daily.IsFetched": "false",
        "Hourly.IsFetched": "",
        "Alerts.IsFetched": "false",
        "Map.IsFetched": "",
        "Caiyun.Available": "false",
        "Caiyun.Error": message,
        "Forecast.IsFetched": "",
    }
    return props
