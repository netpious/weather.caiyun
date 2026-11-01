# -*- coding: utf-8 -*-
"""把若干张帧图拼成一张动图 GIF。

Kodi 的 image 控件只支持一种动画：animated GIF（官方文档原话是 "we support
only animated GIF"，并要求 disposal = restore to background）。

纯 Python 解一张 872×872 的 RGBA 要 2～3 秒，所以这里把开销拆开摊薄：

* 每帧只处理一次：解码 → 量化 → LZW，结果按帧缓存到 addon_data；
  单帧的 LZW 数据与帧序无关（全局调色板、全画幅），拼装时直接字节拷贝；
* 每轮**最多**处理 MAX_NEW 个新帧，避免天气更新卡住；
* 太大的帧（空气质量图 958×868）先缩到 MAX_WIDTH 宽再量化。
"""

import hashlib
import os
import struct
import time
import zlib

from . import giflite, pnglite

VERSION = 4
MAX_FRAMES = 8
MAX_NEW = 5
MAX_WIDTH = 640
DELAY_CS = 30  # 每帧 0.3 秒
KEEP_DAYS = 2


def _key(frame, suffix=""):
    raw = "%d|%s|%s" % (VERSION, suffix, frame.get("path") or frame.get("url") or "")
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _cache_path(cache_dir, frame, suffix=""):
    return os.path.join(cache_dir, _key(frame, suffix) + ".lzw")


def _read_cached(path):
    with open(path, "rb") as handle:
        payload = handle.read()
    width, height = struct.unpack(">HH", payload[:4])
    return width, height, zlib.decompress(payload[4:])


def _write_cached(path, width, height, lzw):
    payload = struct.pack(">HH", width, height) + zlib.compress(lzw, 6)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(payload)
    os.replace(tmp, path)


def cleanup(cache_dir, keep_days=KEEP_DAYS):
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for name in names:
        path = os.path.join(cache_dir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def _shrink(width, height, pixels, max_width):
    if width <= max_width:
        return width, height, pixels
    scale = max_width / float(width)
    new_w = max_width
    new_h = max(1, int(round(height * scale)))
    out = bytearray(new_w * new_h * 4)
    for y in range(new_h):
        source_y = min(height - 1, int(y / scale)) * width
        target = y * new_w * 4
        for x in range(new_w):
            base = (source_y + min(width - 1, int(x / scale))) * 4
            out[target + x * 4:target + x * 4 + 4] = pixels[base:base + 4]
    return new_w, new_h, out


def build(frames, cache_dir, out_path, logger=None, max_frames=MAX_FRAMES,
          max_new=MAX_NEW, delay_cs=DELAY_CS, max_width=MAX_WIDTH,
          transform=None, key_suffix=""):
    """frames（已下载到本地）→ 动图 GIF 路径；没有可用帧时返回 None。"""
    logger = logger or (lambda message: None)
    if not frames:
        return None

    if not os.path.isdir(cache_dir):
        try:
            os.makedirs(cache_dir)
        except OSError as exc:
            logger("动图缓存目录创建失败：%s" % exc)
            return None

    usable = [frame for frame in frames if frame.get("path")][-max_frames:]
    ready = []
    new_count = 0
    for frame in usable:
        path = _cache_path(cache_dir, frame, key_suffix)
        if not os.path.isfile(path):
            if new_count >= max_new:
                continue
            new_count += 1
            try:
                with open(frame["path"], "rb") as handle:
                    raw = handle.read()
                width, height, mode, pixels = pnglite.decode(raw)
            except Exception as exc:
                logger("动图帧解码失败：%s" % exc)
                continue
            if mode != "RGBA":
                logger("动图帧不是 RGBA，跳过：%s" % frame["path"])
                continue
            try:
                if transform is not None:
                    width, height, pixels = transform(width, height, pixels)
                width, height, pixels = _shrink(width, height, pixels, max_width)
                indices = giflite.quantize(pixels)
                _write_cached(path, width, height, giflite._lzw(indices))
            except Exception as exc:
                logger("动图帧缓存失败：%s" % exc)
                continue
        ready.append(path)

    if not ready:
        logger("动图没有可用的帧")
        return None

    width = height = None
    names = []
    for path in ready:
        try:
            w, h, _payload = _read_cached(path)
        except Exception:
            continue
        if width is None:
            width, height = w, h
        if (w, h) != (width, height):
            continue
        names.append(path)
    if not names:
        return None

    signature = hashlib.md5(
        ("%d|%d|%s" % (delay_cs, VERSION, ",".join(names))).encode("utf-8")
    ).hexdigest()[:16]
    target = "%s_%s.gif" % (os.path.splitext(out_path)[0], signature)
    if os.path.isfile(target):
        _touch(target)
        return target

    payloads = []
    for path in names:
        _w, _h, payload = _read_cached(path)
        payloads.append(payload)
    data = giflite.build_gif(width, height, payloads, delay_cs=delay_cs)
    tmp = target + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, target)
    logger("动图 %d 帧 %dx%d，%.0f KB" % (len(payloads), width, height, len(data) / 1024.0))
    return target


def _touch(path):
    try:
        os.utime(path, None)
    except OSError:
        pass
