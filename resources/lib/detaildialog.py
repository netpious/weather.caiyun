# -*- coding: utf-8 -*-
"""天气详情窗口：把 Kodi 首页装不下的数据铺开。

Kodi 的天气属性只够画一张首页；彩云给的东西（16 天、120 小时、六项污染物、
生活指数、分钟级降水）远超这个容量，所以单独开一页。
"""

import datetime
import time

import xbmcgui

from . import api, codes, kodi, mapper, weather

PAGE_NOW = 0
PAGE_HOURS = 1
PAGE_DAYS = 2
PAGE_EXTRA = 3

PAGE_TITLES = (32032, 32053, 32033, 32034)


def _rows_now(props, data):
    realtime = data.get("realtime") or {}
    rows = []
    rows.append((kodi.localize(32032), props.get("Current.Condition", "")))
    rows.append(("温度 / 体感", "%s / %s" % (
        props.get("Current.Temperature", "-") + "°C",
        props.get("Current.FeelsLike", "-") + "°C")))
    rows.append(("湿度 / 露点", "%s / %s" % (
        props.get("Current.Humidity", "-"),
        props.get("Current.DewPoint", "-") + "°C")))
    rows.append(("风", "%s %s（%s 级）" % (
        props.get("Current.WindDirection", ""),
        props.get("Current.Wind", ""),
        props.get("Caiyun.Current.WindLevel", ""))))
    rows.append(("气压 / 能见度", "%s / %s" % (
        props.get("Current.Pressure", "-"),
        props.get("Current.Visibility", "-"))))
    rows.append((kodi.localize(32060) + " / " + kodi.localize(32061),
                 "%s / %s" % (props.get("Current.Cloudiness", "-"),
                              props.get("Current.Precipitation", "-"))))
    rows.append((kodi.localize(32035), "AQI %s  %s" % (
        props.get("Caiyun.AQI.Value", "-"),
        props.get("Caiyun.AQI.Level", ""))))
    rows.append(("PM2.5 / PM10", "%s / %s μg/m³" % (
        props.get("Caiyun.AQI.PM25", "-"), props.get("Caiyun.AQI.PM10", "-"))))
    rows.append(("O₃ / NO₂ / SO₂", "%s / %s / %s μg/m³" % (
        props.get("Caiyun.AQI.O3", "-"), props.get("Caiyun.AQI.NO2", "-"),
        props.get("Caiyun.AQI.SO2", "-"))))
    rows.append(("CO", "%s mg/m³" % props.get("Caiyun.AQI.CO", "-")))
    rows.append(("日出 / 日落", "%s / %s" % (
        props.get("Today.Sunrise", "-"), props.get("Today.Sunset", "-"))))
    text = props.get("Caiyun.Minutely.Description", "")
    if text:
        rows.append((kodi.localize(32038), text))
    keypoint = props.get("Caiyun.Keypoint", "")
    if keypoint and keypoint != text:
        rows.append((kodi.localize(32036), keypoint))
    observed = props.get("Caiyun.Current.Observed", "")
    if observed:
        rows.append(("观测量", observed))
    return rows


def _rows_hours(props):
    rows = []
    for index in range(1, 17):
        prefix = "Hourly.%d." % index
        when = props.get(prefix + "Time")
        if not when:
            continue
        rows.append(("%s  %s" % (props.get(prefix + "ShortDate", ""), when),
                     "%s   %s   %s" % (props.get(prefix + "Temperature", ""),
                                       props.get(prefix + "Outlook", ""),
                                       props.get(prefix + "Precipitation", ""))))
    return rows


def _rows_days(props):
    rows = []
    for index in range(1, 17):
        prefix = "Caiyun.Day16.%d." % index
        when = props.get(prefix + "Date")
        if not when:
            continue
        rows.append(("%s %s" % (when, props.get(prefix + "Weekday", "")),
                     "%s   %s / %s   %s   AQI %s" % (
                         props.get(prefix + "Outlook", ""),
                         props.get(prefix + "High", ""),
                         props.get(prefix + "Low", ""),
                         props.get(prefix + "Precip", ""),
                         props.get(prefix + "AQI", "-"))))
    return rows


def _rows_life(props):
    """网页天气页那六格生活指数（原来那五项已被它取代）。"""
    rows = []
    for index in range(1, 7):
        prefix = "Caiyun.Life.%d." % index
        name = props.get(prefix + "Name")
        if not name:
            continue
        desc = props.get(prefix + "Desc", "")
        detail = props.get(prefix + "Detail", "")
        value = desc if not detail else "%s    %s" % (desc, detail)
        rows.append((name, value, props.get(prefix + "Icon", "")))
    return rows


def _rows_extra(props, data):
    rows = _rows_life(props)

    alerts = data.get("alerts") or []
    if not alerts:
        rows.append((kodi.localize(32055), "", ""))
        return rows
    for alert in alerts:
        rows.append(("%s %s" % (alert.get("type_zh", ""), alert.get("level_zh", "")),
                     alert.get("title") or "", ""))
        rows.append((kodi.localize(32057), alert.get("source") or "", ""))
        if alert.get("published"):
            stamp = datetime.datetime.fromtimestamp(alert["published"], api.TZ)
            rows.append((kodi.localize(32056), stamp.strftime("%Y-%m-%d %H:%M"), ""))
        description = alert.get("description") or ""
        if description:
            rows.append(("详情", description, ""))
    return rows


class DetailWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.city = kwargs.pop("city", "")
        self.pages = kwargs.pop("pages", [])
        self.page = 0
        super(DetailWindow, self).__init__(*args)

    def onInit(self):
        self._title = self.getControl(3)
        self._sub = self.getControl(4)
        self._list = self.getControl(2000)
        self._title.setLabel("%s · %s" % (self.city, kodi.localize(PAGE_TITLES[self.page])))
        self._fill()

    def _fill(self):
        rows = self.pages[self.page]
        self._list.reset()
        items = []
        for row in rows:
            label, value = row[0], row[1]
            icon = row[2] if len(row) > 2 else ""
            item = xbmcgui.ListItem(label=label, label2=value)
            if icon:
                try:
                    item.setArt({"icon": icon})
                except AttributeError:
                    try:
                        item.setIconImage(icon)
                    except Exception:
                        pass
            items.append(item)
        if not items:
            items.append(xbmcgui.ListItem(label="-", label2=""))
        self._list.addItems(items)
        self._sub.setLabel("%s  %s" % (
            kodi.localize(PAGE_TITLES[self.page]),
            "第 %d/%d 页" % (self.page + 1, len(self.pages))))

    def onAction(self, action):
        code = action.getId()
        if code in (xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK):
            self.close()
            return
        if code == xbmcgui.ACTION_MOVE_LEFT:
            self.page = (self.page - 1) % len(self.pages)
            self.onInit()
            return
        if code == xbmcgui.ACTION_MOVE_RIGHT:
            self.page = (self.page + 1) % len(self.pages)
            self.onInit()
            return

    def onClick(self, control_id):
        if control_id == 61:
            self.close()


def show(index=1):
    data, conf = weather.properties_for(index)
    if data is None:
        kodi.notify(kodi.localize(32051), heading=kodi.localize(32030))
        return

    options = mapper._Options(
        city=conf["name"],
        lang=kodi.language(),
        units=kodi.setting_bool("units_mode", True),
        maps=[],
        life=data.get("life_tiles") or [],
    )
    props = mapper.build(data, options)

    pages = [
        _rows_now(props, data),
        _rows_hours(props),
        _rows_days(props),
        _rows_extra(props, data),
    ]

    try:
        window = DetailWindow(
            "weather-detail.xml",
            kodi.addon_path(),
            "Default",
            "1080i",
            city=conf["name"],
            pages=pages,
        )
        window.doModal()
        del window
    except Exception as exc:
        kodi.log("详情窗口失败：%s" % exc)
        _textviewer(conf["name"], pages)


def _textviewer(city, pages):
    """窗口创建失败时兜底，至少让人看到文字。"""
    lines = [city]
    for title, rows in zip(PAGE_TITLES, pages):
        lines.append("")
        lines.append("== %s ==" % kodi.localize(title))
        for label, value in [(row[0], row[1]) for row in rows]:
            lines.append("%s：%s" % (label, value))
    try:
        kodi.dialog().textviewer(kodi.localize(32030), "\n".join(lines))
    except Exception as exc:
        kodi.log("兜底窗口也失败了：%s" % exc)
