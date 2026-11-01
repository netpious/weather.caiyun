# -*- coding: utf-8 -*-
"""云图窗口：底图 + 雷达帧叠加，支持逐帧播放。

为什么不用 Kodi 自带的 `Map.*` 天气地图页？
  * Estuary 把地图页放在二级屏，要翻页才能看到；
  * 每页只能放一张静态图，放不了动画。
所以这里自己画一个：OK 键暂停/播放，左右键单帧步进。

动画原理：底图固定不动，只换上面那层雷达 PNG 的纹理。
"""

import os
import threading
import time

import xbmc
import xbmcgui

from . import api, kodi, net, pnglite, radar, weather

FRAME_MS = 350
LIVE_MARK = "live"

# 窗口 XML 里给地图留的位置；实际显示时按底图真实比例缩进这个框
BOX_X, BOX_Y, BOX_W, BOX_H = 100, 96, 900, 860


def _fmt(stamp):
    return time.strftime("%H:%M", time.localtime(stamp))


class RadarWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.index = kwargs.pop("index", 1)
        self.city = kwargs.pop("city", "")
        self.frames = []
        self.playing = True
        self.current = 0
        self.stopped = False
        self.ready = False
        self._lock = threading.Lock()
        super(RadarWindow, self).__init__(*args)

    # ---------------------------------------------------------- 生命周期

    def onInit(self):
        self._base = self.getControl(10)
        self._layer = self.getControl(11)
        self._title = self.getControl(12)
        self._clock = self.getControl(13)
        self._legend = self.getControl(14)
        self._status = self.getControl(16)
        self._feed = self.getControl(20)
        self._temp = self.getControl(21)
        self._aqi = self.getControl(22)
        self._minutely = self.getControl(23)
        self._extra = self.getControl(24)

        self._title.setLabel(self.city or kodi.localize(32031))
        self._fill_info()
        legend = os.path.join(kodi.addon_path(), "resources", "media", "radar-legend.png")
        if os.path.isfile(legend):
            try:
                self._legend.setImage(legend)
            except Exception:
                pass
        self._status.setLabel(kodi.localize(32040))

        thread = threading.Thread(target=self._load)
        thread.daemon = True
        thread.start()

        player = threading.Thread(target=self._loop)
        player.daemon = True
        player.start()

    def _fill_info(self):
        """右侧信息栏直接读天气窗口里已取好的属性，不额外联网。"""
        window = kodi.window()

        def prop(key):
            try:
                return window.getProperty(key) or ""
            except Exception:
                return ""

        try:
            condition = prop("Current.Condition")
            if condition:
                self._feed.setLabel(condition)
            temperature = prop("Current.Temperature")
            if temperature:
                self._temp.setLabel("%s°C" % temperature)
            aqi = prop("Caiyun.AQI.Value")
            if aqi:
                self._aqi.setLabel("AQI %s   %s" % (aqi, prop("Caiyun.AQI.Level")))
            text = prop("Caiyun.Minutely.Description")
            if text:
                self._minutely.setLabel(text)
            parts = []
            wind = prop("Current.Wind")
            if wind:
                parts.append("%s %s" % (prop("Current.WindDirection"), wind))
            for key, label in (("Current.Humidity", "湿度"),
                               ("Current.Visibility", "能见度"),
                               ("Current.Precipitation", "降水")):
                value = prop(key)
                if value:
                    parts.append("%s %s" % (label, value))
            if parts:
                self._extra.setLabel("[CR]".join(parts))
        except Exception as exc:
            kodi.log("信息栏填充失败：%s" % exc)

    def onAction(self, action):
        code = action.getId()
        if code in (xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK):
            self._shutdown()
            self.close()
            return
        if code in (xbmcgui.ACTION_SELECT_ITEM, xbmcgui.ACTION_PLAYER_PLAY,
                    xbmcgui.ACTION_PLAYER_PLAYPAUSE, xbmcgui.ACTION_MOUSE_LEFT_CLICK):
            self.playing = not self.playing
            self._update_status()
            return
        if code == xbmcgui.ACTION_MOVE_LEFT:
            self._step(-1)
            return
        if code == xbmcgui.ACTION_MOVE_RIGHT:
            self._step(1)
            return

    def close(self):
        self._shutdown()
        super(RadarWindow, self).close()

    def _shutdown(self):
        self.stopped = True

    # ---------------------------------------------------------- 数据

    def _load(self):
        conf = weather.slot_conf(self.index)
        if conf["lon"] is None:
            self._status.setLabel(kodi.localize(32064))
            return

        client = net.Client(logger=kodi.log)
        dirs = weather.radar_dirs(self.index)
        level = 1 if kodi.setting_bool("radar_local", True) else 2
        collected = []

        try:
            live = radar.fetch_frames(client, conf["lon"], conf["lat"], level=level,
                                      forecast=False, cache_dir=dirs["local"],
                                      logger=kodi.log)
            collected.extend([dict(frame, kind=LIVE_MARK) for frame in live["frames"]])
            self._install(collected)
        except Exception as exc:
            kodi.log("云图实况加载失败：%s" % exc)

        try:
            forecast = radar.fetch_frames(client, conf["lon"], conf["lat"], level=level,
                                          forecast=True, cache_dir=dirs["forecast"],
                                          logger=kodi.log)
            for frame in forecast["frames"]:
                if collected and frame["time"] <= collected[-1]["time"]:
                    continue
                collected.append(dict(frame, kind="forecast"))
            self._install(collected)
        except Exception as exc:
            kodi.log("云图预报加载失败：%s" % exc)

        if not collected:
            try:
                self._status.setLabel(kodi.localize(32064))
            except Exception:
                pass

    def _install(self, frames):
        if not frames:
            return
        maps_dir = weather.radar_dirs(self.index)["maps"]
        cache = {}
        for frame in frames:
            bounds = frame.get("bounds")
            if not bounds:
                continue
            key = tuple(bounds)
            if key not in cache:
                cache[key] = weather._base_map(bounds, maps_dir, kodi.log)
            frame["base"] = cache[key]
        self._fit(cache)
        with self._lock:
            self.frames = frames
            self.current = 0
            self.ready = True
        self._paint()

    def _fit(self, cache):
        """按底图真实比例把两个图片控件缩放居中，避免地理形状被拉扁。"""
        base = next((path for path in cache.values() if path), None)
        if not base:
            return
        size = pnglite.size(base)
        if not size or not size[0] or not size[1]:
            return
        width, height = size
        scale = min(BOX_W / float(width), BOX_H / float(height))
        draw_w = int(width * scale)
        draw_h = int(height * scale)
        left = BOX_X + (BOX_W - draw_w) // 2
        top = BOX_Y + (BOX_H - draw_h) // 2
        for control in (self._base, self._layer):
            try:
                control.setPosition(left, top)
                control.setWidth(draw_w)
                control.setHeight(draw_h)
            except Exception:
                return

    # ---------------------------------------------------------- 绘制

    def _paint(self):
        with self._lock:
            if not self.frames:
                return
            frame = self.frames[self.current]
            total = len(self.frames)
            position = self.current
        path = frame.get("path")
        if not path:
            return
        try:
            self._layer.setImage(path)
            base = frame.get("base")
            if base and os.path.isfile(base):
                self._base.setImage(base)
            self._clock.setLabel(_fmt(frame["time"]))
            self._update_status(position, total, frame.get("kind"))
        except Exception:
            pass

    def _update_status(self, position=None, total=None, kind=None):
        try:
            if position is None:
                with self._lock:
                    position = self.current
                    total = len(self.frames)
                    kind = self.frames[self.current].get("kind") if self.frames else ""
            mark = kodi.localize(32043) if kind == LIVE_MARK else kodi.localize(32044)
            state = "▶" if self.playing else "❚❚"
            self._status.setLabel("%s %s  %d/%d   %s" % (mark, state, position + 1, total,
                                                         self.city))
        except Exception:
            pass

    # ---------------------------------------------------------- 播放

    def _loop(self):
        while not self.stopped:
            try:
                if self.playing and self.ready:
                    with self._lock:
                        if self.frames:
                            self.current = (self.current + 1) % len(self.frames)
                    self._paint()
                xbmc.sleep(FRAME_MS if self.playing else 120)
            except Exception:
                xbmc.sleep(400)

    def _step(self, delta):
        self.playing = False
        with self._lock:
            if not self.frames:
                return
            self.current = (self.current + delta) % len(self.frames)
        self._paint()


def show(index=1):
    conf = weather.slot_conf(index)
    if conf["lon"] is None:
        kodi.notify(kodi.localize(32051), heading=kodi.localize(32031))
        return

    addon_path = kodi.addon_path()
    try:
        window = RadarWindow(
            "weather-radar.xml",
            addon_path,
            "Default",
            "1080i",
            index=index,
            city=conf["name"],
        )
        window.doModal()
        del window
    except Exception as exc:
        kodi.log("云图窗口失败：%s" % exc)
        kodi.notify(kodi.localize(32064), heading=kodi.localize(32031))
