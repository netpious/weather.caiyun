# -*- coding: utf-8 -*-
"""底图：把高德瓦片拼成一张与雷达帧同范围的图。

彩云的雷达帧是**透明叠加层**，只有降水，没有地理信息；所以必须自己配一张
底图，且底图的地理范围要和帧完全一致，叠起来才对得上。

高德瓦片是 Web Mercator（GCJ-02），彩云帧是 WGS84/3857；在国家尺度上
两者的偏移不到一个像素，可以忽略。
"""

import math
import os
import threading

from . import net, pnglite

TILE_URL = (
    "https://webrd0%d.is.autonavi.com/appmaptile"
    "?lang=zh_cn&size=1&scale=1&style=8&x=%d&y=%d&z=%d"
)

MAX_TILES = 24
TARGET_WIDTH = 1000


class TileError(Exception):
    pass


def deg2num(lon, lat, zoom):
    """经纬度 → 瓦片浮点坐标。"""
    lat = max(min(lat, 85.05112878), -85.05112878)
    count = 2 ** zoom
    x = (lon + 180.0) / 360.0 * count
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * count
    return x, y


def _pick_zoom(bounds):
    south, west, north, east = bounds
    for zoom in range(12, 2, -1):
        x0, y0 = deg2num(west, north, zoom)
        x1, y1 = deg2num(east, south, zoom)
        width = (x1 - x0) * 256
        height = (y1 - y0) * 256
        if width <= TARGET_WIDTH * 1.3 and height <= 1400:
            tiles = (int(x1) - int(x0) + 1) * (int(y1) - int(y0) + 1)
            if tiles <= MAX_TILES:
                return zoom
    return 4


def build_base_map(bounds, fetch=None, logger=None):
    """bounds = [南纬, 西经, 北纬, 东经] → (width, height, rgb bytes)。"""
    logger = logger or (lambda message: None)
    fetch = fetch or net.fetch_bytes
    south, west, north, east = bounds
    if not (south < north and west < east):
        raise TileError("范围不合法：%s" % (bounds,))

    zoom = _pick_zoom(bounds)
    x0, y0 = deg2num(west, north, zoom)
    x1, y1 = deg2num(east, south, zoom)

    tile_x0, tile_x1 = int(math.floor(x0)), int(math.floor(x1))
    tile_y0, tile_y1 = int(math.floor(y0)), int(math.floor(y1))
    cols = tile_x1 - tile_x0 + 1
    rows = tile_y1 - tile_y0 + 1
    if cols * rows > MAX_TILES:
        raise TileError("瓦片过多：%d" % (cols * rows))

    canvas = pnglite.Canvas(cols * 256, rows * 256, (18, 22, 28))
    jobs = []
    for column in range(cols):
        for row in range(rows):
            index = (column + row) % 4 + 1
            jobs.append((
                (column, row),
                TILE_URL % (index, tile_x0 + column, tile_y0 + row, zoom),
            ))

    fetched = 0
    for (column, row), raw in zip([job[0] for job in jobs],
                                  _fetch_many([job[1] for job in jobs], fetch)):
        if raw is None:
            logger("瓦片 (%d,%d) 下载失败" % (tile_x0 + column, tile_y0 + row))
            continue
        try:
            width, height, mode, pixels = pnglite.decode(raw)
        except Exception as exc:
            logger("瓦片 (%d,%d) 解码失败：%s" % (tile_x0 + column, tile_y0 + row, exc))
            continue
        if mode == "RGBA":
            pixels = _drop_alpha(pixels, width * height)
        canvas.paste((width, height, pixels), column * 256, row * 256)
        fetched += 1

    if fetched == 0:
        raise TileError("所有瓦片都下载失败")

    left = (x0 - tile_x0) * 256
    top = (y0 - tile_y0) * 256
    right = (x1 - tile_x0) * 256
    bottom = (y1 - tile_y0) * 256
    width, height, pixels = canvas.crop(left, top, right, bottom)
    logger("底图 %dx%d，zoom=%d，瓦片 %d 张" % (width, height, zoom, fetched))
    return width, height, pixels


def _drop_alpha(pixels, count):
    out = bytearray(count * 3)
    for i in range(count):
        out[i * 3:i * 3 + 3] = pixels[i * 4:i * 4 + 3]
    return out


def _fetch_many(urls, fetch, workers=10):
    """并发抓瓦片：20 张串行要 2 秒，并发只要 0.3 秒。"""
    results = [None] * len(urls)

    def run(index):
        try:
            results[index] = fetch(urls[index])
        except Exception:
            results[index] = None

    active = []
    pending = list(range(len(urls)))
    while pending or active:
        while pending and len(active) < workers:
            index = pending.pop(0)
            thread = threading.Thread(target=run, args=(index,))
            thread.daemon = True
            thread.start()
            active.append(thread)
        active[0].join()
        active.pop(0)
    return results


def save_base_map(bounds, path, fetch=None, logger=None):
    width, height, pixels = build_base_map(bounds, fetch=fetch, logger=logger)
    data = pnglite.encode_rgb(width, height, pixels)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "wb") as handle:
        handle.write(data)
    return path, width, height


def bounds_key(bounds):
    return "%.3f_%.3f_%.3f_%.3f" % tuple(bounds)


def bounds_from_images(frames):
    """一组帧共用的 bounds（彩云每帧都带，取最新那帧的）。"""
    for frame in reversed(list(frames or [])):
        bounds = frame.get("bounds")
        if bounds and len(bounds) == 4:
            return [float(value) for value in bounds]
    return None
