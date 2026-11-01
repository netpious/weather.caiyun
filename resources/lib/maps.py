# -*- coding: utf-8 -*-
"""首页那两块云图：降雨 / 空气 × 实况 / 两小时后 / 动图。

彩云网页的地图只给了两种图块接口：

* 降雨：`v1/radar/fine_images`（实况 20 帧 + 预报 25 帧，都是透明叠加层）
* 空气：`v1/aqi/images`（实况 48 帧 + 预报 121 帧，全国栅格，方向不同）

三个时间模式的做法：

* `live`      取最新一帧，直接给皮肤当 Layer；
* `forecast`  取 +2 小时那一帧；
* `anim`      最近若干帧拼成动图 GIF（Kodi 只认 animated GIF）。
"""

import hashlib
import math
import os
import time

from . import anim, api, kodi, pnglite, radar, tiles

BASE_TTL = 7 * 86400
AIR_ALPHA = 1.45         # 皮肤给 Layer 打了 colordiffuse B0FFFFFF，正好抵消它
ANIM_LIVE = 6            # 动图里实况帧数
ANIM_FORECAST = 4        # 动图里预报帧数
# 皮肤里 Layer 那张图带 colordiffuse B0FFFFFF（= 69%），叠加前先按 1/0.69 提亮，
# 上屏后才是原图的透明度。
# 空气质量图层是覆盖亚洲的一整张栅格，网页地图按当前缩放看其中一块。
# 桌面默认 zoom=9，但那张图只有 11 像素/经度，9 会糊成一片；「省份」档是 8。
AIR_VIEW_ZOOM = 8
AIR_VIEW_WIDTH = 1680
AIR_VIEW_HEIGHT = 800

# 设置里的选项编号 → (图层, 时间模式)
CHOICES = {
    0: (None, None),
    1: ("rain", "live"),
    2: ("rain", "forecast"),
    3: ("rain", "anim"),
    4: ("air", "live"),
    5: ("air", "anim"),
}


def _media(name):
    path = os.path.join(kodi.addon_path(), "resources", "media", name)
    return path if os.path.isfile(path) else ""


def _clock(stamp):
    return time.strftime("%H:%M", time.localtime(stamp))


def _stamp(stamp):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


class Context(object):
    """一次更新内复用的取数与底图。两个方框选同一图层时只算一次。"""

    def __init__(self, client, conf, dirs, logger):
        self.client = client
        self.conf = conf
        self.dirs = dirs
        self.logger = logger
        self._memo = {}

    def _once(self, name, factory):
        if name not in self._memo:
            self._memo[name] = factory()
        return self._memo[name]

    # ---- 底图 -------------------------------------------------------

    def base_map(self, bounds):
        key = "base_" + hashlib.md5(
            tiles.bounds_key(bounds).encode("utf-8")
        ).hexdigest()[:12] + ".png"
        path = os.path.join(self.dirs["maps"], key)
        if os.path.isfile(path):
            try:
                if time.time() - os.path.getmtime(path) < BASE_TTL:
                    return path
            except OSError:
                pass
        try:
            if not os.path.isdir(self.dirs["maps"]):
                os.makedirs(self.dirs["maps"])
            tiles.save_base_map(bounds, path, logger=self.logger)
            return path
        except Exception as exc:
            self.logger("底图生成失败：%s" % exc)
            return ""

    # ---- 数据 -------------------------------------------------------

    def radar(self):
        def factory():
            level = 1 if kodi.setting_bool("radar_local", True) else 2
            raw = self.client.radar_images(self.conf["lon"], self.conf["lat"], level=level)
            return api.parse_radar(raw)

        return self._once("radar", factory)

    def air(self):
        def factory():
            raw = self.client.aqi_images(self.conf["lon"], self.conf["lat"])
            return api.parse_aqi(raw)

        return self._once("air", factory)


# ---------------------------------------------------------------- 降雨


def _rain(ctx, mode):
    parsed = ctx.radar()
    live = parsed["frames"]
    forecast = parsed["forecast"]
    source = live or forecast
    if not source:
        return None
    bounds = tiles.bounds_from_images(source)
    if not bounds:
        return None
    base = ctx.base_map(bounds)
    if not base:
        return None
    station = parsed["station"]
    legend = _media("radar-legend.png")

    if mode == "live":
        frames = radar.download_frames(live[-1:], ctx.dirs["local"], station, ctx.logger)
        if not frames:
            return None
        frame = frames[-1]
        return {
            "area": base,
            "layer": frame["path"],
            "legend": legend,
            "heading": "雷达实况 " + _clock(frame["time"]),
            "time": _stamp(frame["time"]),
            "frames": frames,
        }

    if mode == "forecast":
        frames = radar.download_frames(forecast[-1:], ctx.dirs["forecast"], station, ctx.logger)
        if not frames:
            return None
        frame = frames[-1]
        return {
            "area": base,
            "layer": frame["path"],
            "legend": legend,
            "heading": "雷达预报 " + _clock(frame["time"]),
            "time": _stamp(frame["time"]),
            "frames": frames,
        }

    if mode != "anim":
        return None

    window = live[-ANIM_LIVE:] + forecast[:ANIM_FORECAST]
    frames = radar.download_frames(window, ctx.dirs["local"], station, ctx.logger)
    if not frames:
        return None
    gif = anim.build(frames, ctx.dirs["anim"],
                     os.path.join(ctx.dirs["anim"], "rain"),
                     logger=ctx.logger, max_frames=len(frames))
    if not gif:
        return None
    first, last = frames[0]["time"], frames[-1]["time"]
    return {
        "area": base,
        "layer": gif,
        "legend": legend,
        "heading": "雷达动图 %s–%s" % (_clock(first), _clock(last)),
        "time": _stamp(last),
        "frames": frames,
    }


# ---------------------------------------------------------------- 空气


def _pixel_to_lonlat(px, py, zoom):
    world = 256.0 * (2 ** zoom)
    lon = px / world * 360.0 - 180.0
    north_pi = math.pi - 2.0 * math.pi * py / world
    lat = math.degrees(math.atan(math.sinh(north_pi)))
    return lon, lat


def viewport_bounds(lon, lat, zoom, width=AIR_VIEW_WIDTH, height=AIR_VIEW_HEIGHT):
    """中心点 + 缩放 + 视窗像素 → [南, 西, 北, 东]（和高德瓦片同一套墨卡托）。

    彩云网页地图就是这么显示空气质量图层的：一张覆盖亚洲的栅格钉在它自己的
    bounds 上，再按当前缩放看其中一块。桌面端初始 zoom=9，页面上的缩放锚点里
    「省份」是 8——空气质量本身只有 11 像素/经度，zoom 9 会糊成一片，
    所以这里取 8。
    """
    tile_x, tile_y = tiles.deg2num(lon, lat, zoom)
    center_x, center_y = tile_x * 256.0, tile_y * 256.0
    west, north = _pixel_to_lonlat(center_x - width / 2.0, center_y - height / 2.0, zoom)
    east, south = _pixel_to_lonlat(center_x + width / 2.0, center_y + height / 2.0, zoom)
    return [south, west, north, east]


def _crop_rect(src_bounds, dst_bounds, width, height):
    """目标经纬范围在源栅格里的像素矩形。"""
    south, west, north, east = [float(value) for value in src_bounds]
    d_south, d_west, d_north, d_east = [float(value) for value in dst_bounds]
    x0 = int(round((d_west - west) / (east - west) * width))
    x1 = int(round((d_east - west) / (east - west) * width))
    y0 = int(round((north - d_north) / (north - south) * height))
    y1 = int(round((north - d_south) / (north - south) * height))
    return x0, y0, x1, y1


def _air_layer(ctx, frame, view):
    """下载一帧空气质量图，裁到城市视窗，把 alpha 放大，落成一张叠加图。"""
    frames = radar.download_frames([frame], ctx.dirs["aqi"], "aqi", ctx.logger)
    if not frames:
        return None
    path = frames[0]["path"]
    if not view:
        return path
    tag = hashlib.md5(tiles.bounds_key(view).encode("utf-8")).hexdigest()[:8]
    target = "%s_%s.png" % (os.path.splitext(path)[0], tag)
    try:
        if os.path.getsize(target) > 512:
            return target
    except OSError:
        pass
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        width, height, mode, pixels = pnglite.decode(raw)
        if mode != "RGBA":
            return path
        x0, y0, x1, y1 = _crop_rect(frame["bounds"], view, width, height)
        width, height, pixels = pnglite.crop_rgba(width, height, pixels, x0, y0, x1, y1)
        pixels = pnglite.scale_alpha(pixels, pnglite.alpha_table(AIR_ALPHA))
        with open(target, "wb") as handle:
            handle.write(pnglite.encode_rgba(width, height, pixels))
        return target
    except Exception as exc:
        ctx.logger("空气质量图层裁剪失败：%s" % exc)
        return path


def _flatten_for_gif(pixels, alpha_min=96):
    """GIF 没有半透明：把 AQI 的 alpha 压到白底上，再决定透明与否。

    静态页是原 alpha 盖在浅色底图上，动图只能近似成「按 alpha 混白 + 全不透明」，
    上屏强度才和静态页接近。
    """
    out = bytearray(pixels)
    for index in range(0, len(pixels), 4):
        alpha = pixels[index + 3]
        if alpha < alpha_min:
            out[index + 3] = 0
            continue
        out[index + 3] = 255
        ratio = alpha / 255.0
        for channel in range(3):
            out[index + channel] = int(out[index + channel] * ratio + 255 * (1 - ratio))
    return out


def _air(ctx, mode):
    parsed = ctx.air()
    live = parsed["frames"]
    forecast = parsed["forecast"]
    source = live or forecast
    if not source:
        return None
    source_bounds = tiles.bounds_from_images(source)
    if not source_bounds:
        return None
    view = viewport_bounds(ctx.conf["lon"], ctx.conf["lat"], AIR_VIEW_ZOOM)
    base = ctx.base_map(view)
    if not base:
        return None
    legend = _media("aqi-legend.png")

    if mode == "live":
        frame = live[-1] if live else forecast[0]
        layer = _air_layer(ctx, frame, view)
        if not layer:
            return None
        return {
            "area": base,
            "layer": layer,
            "legend": legend,
            "heading": "空气质量实况 " + _clock(frame["time"]),
            "time": _stamp(frame["time"]),
            "frames": [frame],
        }

    if mode != "anim":
        return None

    window = live[-anim.MAX_FRAMES:] or forecast[:anim.MAX_FRAMES]
    frames = radar.download_frames(window, ctx.dirs["aqi"], "aqi", ctx.logger)
    if not frames:
        return None
    source_bounds = tiles.bounds_from_images(frames) or source_bounds

    def transform(width, height, pixels):
        x0, y0, x1, y1 = _crop_rect(source_bounds, view, width, height)
        cw, ch, cropped = pnglite.crop_rgba(width, height, pixels, x0, y0, x1, y1)
        return cw, ch, _flatten_for_gif(cropped)

    gif = anim.build(frames, ctx.dirs["anim"], os.path.join(ctx.dirs["anim"], "air"),
                     logger=ctx.logger, max_frames=len(frames),
                     transform=transform,
                     key_suffix=hashlib.md5(tiles.bounds_key(view).encode("utf-8")).hexdigest()[:8])
    if not gif:
        return None
    return {
        "area": base,
        "layer": gif,
        "legend": legend,
        "heading": "空气质量动图 " + _clock(frames[-1]["time"]),
        "time": _stamp(frames[-1]["time"]),
        "frames": frames,
    }


# ---------------------------------------------------------------- 出口


def build_page(ctx, layer, mode):
    if layer == "rain":
        return _rain(ctx, mode)
    if layer == "air":
        return _air(ctx, mode)
    return None


def build_pages(client, conf, dirs, logger):
    """按设置里的两个方框生成 Map.1 / Map.2。"""
    if not kodi.setting_bool("radar_enabled", True):
        return []
    ctx = Context(client, conf, dirs, logger)
    pages = []
    for slot in (1, 2):
        code = kodi.setting_int("map_slot%d" % slot, slot)
        layer, mode = CHOICES.get(code, (None, None))
        if not layer:
            continue
        try:
            page = build_page(ctx, layer, mode)
        except Exception as exc:
            logger("云图第 %d 块（%s/%s）失败：%s" % (slot, layer, mode, exc))
            page = None
        if page:
            pages.append(page)
        else:
            logger("云图第 %d 块（%s/%s）没有内容" % (slot, layer, mode))
    return pages
