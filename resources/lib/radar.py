# -*- coding: utf-8 -*-
"""雷达帧的下载与缓存。

彩云返回的图片 URL 上挂着 `auth_key`，有效期约一小时，所以：

* URL 不缓存，每轮更新重新取列表；
* 下载下来的 PNG 按「站点 + 时间戳」落盘，可以长期复用。

一帧约 95 KB，20 帧两小时实况约 1.9 MB，25 帧预报约 2.4 MB。所以例行更新
只取 1 帧，把整套帧留给云图窗口按需下载。
"""

import os
import threading
import time

from . import api, net

WORKERS = 6
KEEP_DAYS = 2

# 空帧也是一个合法的 PNG：整幅 1000x1000 全透明，压缩后只有 900 字节左右。
# 早先按「大于 1KB 才算成功」判断，结果无降水的城市（比如晴朗的北京）永远
# 拿不到云图——首页地图页整块不显示。只能看魔数，不能看体积。
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_FRAME_BYTES = 100


def _is_png(data):
    return bool(data) and data.startswith(PNG_MAGIC) and len(data) >= MIN_FRAME_BYTES


def _parallel(tasks, workers=WORKERS):
    """tasks = [(index, callable)] → {index: result or None}。"""
    results = {}
    lock = threading.Lock()

    def runner(index, func):
        try:
            value = func()
        except Exception:
            value = None
        with lock:
            results[index] = value

    threads = []
    for index, func in tasks:
        thread = threading.Thread(target=runner, args=(index, func))
        thread.daemon = True
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return results


def _frame_path(cache_dir, station, stamp):
    safe = "".join(char for char in (station or "radar") if char.isalnum()) or "radar"
    return os.path.join(cache_dir, "%s_%d.png" % (safe, int(stamp)))


def cleanup(cache_dir, keep_days=KEEP_DAYS):
    """删掉过期帧，避免 addon_data 无限膨胀。"""
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for name in names:
        if not name.endswith(".png"):
            continue
        path = os.path.join(cache_dir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def download_frames(frames, cache_dir, station, logger=None):
    """把一组帧下载到 cache_dir（已存在且有效的跳过），就地补上 path。"""
    logger = logger or (lambda message: None)
    if not cache_dir:
        return frames
    if not os.path.isdir(cache_dir):
        try:
            os.makedirs(cache_dir)
        except OSError as exc:
            logger("创建帧缓存目录失败：%s" % exc)
            return frames

    tasks = []
    for index, frame in enumerate(frames):
        path = _frame_path(cache_dir, station, frame["time"])
        frame["path"] = path
        try:
            if os.path.getsize(path) >= MIN_FRAME_BYTES:
                continue
        except OSError:
            pass
        tasks.append((index, _downloader(frame["url"], path, logger)))

    if tasks:
        logger("下载 %d 帧图片（%s）" % (len(tasks), station))
        results = _parallel(tasks)
        for index, ok in results.items():
            if not ok:
                frames[index]["path"] = None

    kept = [frame for frame in frames if frame.get("path")]
    if not kept:
        logger("没有可用的图片帧")
    return kept


def fetch_frames(client, lon, lat, level=1, forecast=False, cache_dir=None,
                 count=None, logger=None):
    """取雷达帧列表并把需要的帧下载到本地。

    返回 {"station": str, "frames": [{"time": ts, "bounds": [...], "path": ...}]}
    """
    logger = logger or (lambda message: None)
    raw = client.radar(lon, lat, level=level, forecast=forecast)
    parsed = api.parse_radar(raw)
    frames = parsed["forecast"] if forecast else parsed["frames"]
    if not frames:
        frames = parsed["forecast"]
    if count:
        frames = frames[-count:]

    if not cache_dir:
        return {"station": parsed["station"], "frames": frames}

    kept = download_frames(frames, cache_dir, parsed["station"], logger)
    return {"station": parsed["station"], "frames": kept}


def _downloader(url, path, logger):
    def run():
        try:
            data = net.fetch_bytes(url)
        except Exception as exc:
            logger("帧下载失败：%s" % exc)
            return False
        if not _is_png(data):
            logger("帧不是有效 PNG（%d 字节）：%s" % (len(data), url[:80]))
            return False
        try:
            with open(path, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            logger("帧写入失败：%s" % exc)
            return False
        return True

    return run


def latest(cache_dir):
    """缓存里最新的那一帧路径（按文件名里的时间戳排序）。"""
    try:
        names = [name for name in os.listdir(cache_dir) if name.endswith(".png")]
    except OSError:
        return None
    if not names:
        return None
    names.sort()
    return os.path.join(cache_dir, names[-1])
