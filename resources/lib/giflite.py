# -*- coding: utf-8 -*-
"""极简 GIF89a 编码器（纯标准库）。

Kodi 的 image 控件只认一种动画格式：**动图 GIF**（官方文档原文：only
animated GIF，并且要求 disposal = restore to background）。所以雷达/空气
质量那一栏的「动图」，就是把若干帧量化成调色板索引再拼成一个 GIF。

这里只做本插件需要的那一档：

* 全局调色板固定为 6×8×5 = 240 色网格（等距切分，量化是纯整数运算，
  不需要最近色搜索），索引 240 留作全透明；
* 每帧全画幅、disposal=2（恢复背景）、无限循环；
* LZW 按 GIF 规范实现，码长 9→12 自动增长，满表后发 clear。

固定的调色板让「每帧的索引图」可以缓存到磁盘，下一轮只要处理新增的帧。
"""

import struct

GRID_R = 6
GRID_G = 8
GRID_B = 5
TRANSPARENT = 240
PALETTE_SIZE = 256


def build_palette():
    """6×8×5 网格 → 256 项调色板（后 16 项留黑，240 号做透明）。"""
    table = bytearray(PALETTE_SIZE * 3)
    for ri in range(GRID_R):
        red = ri * 255 // (GRID_R - 1)
        for gi in range(GRID_G):
            green = gi * 255 // (GRID_G - 1)
            for bi in range(GRID_B):
                blue = bi * 255 // (GRID_B - 1)
                index = _index(ri, gi, bi)
                table[index * 3] = red
                table[index * 3 + 1] = green
                table[index * 3 + 2] = blue
    return bytes(table)


def _index(ri, gi, bi):
    return (ri * GRID_G + gi) * GRID_B + bi


def _channel_tables():
    """颜色分量 → 网格序号（256 项翻译表，比逐像素除法快）。"""
    reds = bytes(min(GRID_R - 1, value * GRID_R // 256) for value in range(256))
    greens = bytes(min(GRID_G - 1, value * GRID_G // 256) for value in range(256))
    blues = bytes(min(GRID_B - 1, value * GRID_B // 256) for value in range(256))
    return reds, greens, blues


_RED, _GREEN, _BLUE = _channel_tables()


def quantize(rgba, alpha_min=96):
    """RGBA 字节 → 调色板索引（每像素 1 字节）。alpha < alpha_min 视为透明。"""
    count = len(rgba) // 4
    out = bytearray(count)
    red = rgba[0::4].translate(_RED)
    green = rgba[1::4].translate(_GREEN)
    blue = rgba[2::4].translate(_BLUE)
    alpha = rgba[3::4]
    step_g = GRID_G * GRID_B
    for i in range(count):
        if alpha[i] < alpha_min:
            out[i] = TRANSPARENT
        else:
            out[i] = red[i] * step_g + green[i] * GRID_B + blue[i]
    return out


def encode(indices_frames, width, height, delay_cs=30, palette=None):
    """[{index bytes}] → GIF 字节。"""
    return build_gif(width, height, [_lzw(frame) for frame in indices_frames],
                     delay_cs=delay_cs, palette=palette)


def build_gif(width, height, payloads, delay_cs=30, palette=None):
    """已经 LZW 压缩好的每帧数据 → 完整的 GIF 字节。

    单帧的 LZW 数据和帧间无关（全局调色板 + 全画幅），所以可以逐帧缓存，
    拼装时只做字节拷贝——动图就不必每轮重新压缩。
    """
    palette = palette or build_palette()
    if len(palette) < PALETTE_SIZE * 3:
        palette = palette + bytes(PALETTE_SIZE * 3 - len(palette))

    out = bytearray()
    out += b"GIF89a"
    # 逻辑屏描述符：全局调色板、256 色、背景色 0、无宽高比
    out += struct.pack("<HHBBB", width, height, 0xF7, 0, 0)
    out += palette
    # NETSCAPE2.0 无限循环
    out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"

    for payload in payloads:
        # 图形控制扩展：disposal=2（恢复背景）+ 透明色
        out += b"\x21\xF9\x04"
        out.append(0x09)  # (2 << 2) | 1
        out += struct.pack("<H", delay_cs)
        out.append(TRANSPARENT)
        out.append(0)
        # 图像描述符
        out += b"\x2C"
        out += struct.pack("<HHHHB", 0, 0, width, height, 0)
        out.append(8)  # LZW minimum code size
        for start in range(0, len(payload), 255):
            block = payload[start:start + 255]
            out.append(len(block))
            out += block
        out.append(0)

    out += b"\x3B"
    return bytes(out)


def _lzw(indices, min_code_size=8):
    clear_code = 1 << min_code_size
    end_code = clear_code + 1
    code_size = min_code_size + 1
    next_code = end_code + 1
    table = {}

    out = bytearray()
    acc = 0
    bits = 0

    def emit(code):
        nonlocal acc, bits
        acc |= code << bits
        bits += code_size
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8

    emit(clear_code)
    if not indices:
        emit(end_code)
        if bits:
            out.append(acc & 0xFF)
        return bytes(out)

    prefix = indices[0]
    for byte in indices[1:]:
        key = (prefix << 8) | byte
        code = table.get(key)
        if code is None:
            emit(prefix)
            if next_code < 4096:
                table[key] = next_code
                next_code += 1
                if next_code > (1 << code_size) and code_size < 12:
                    code_size += 1
            else:
                emit(clear_code)
                table.clear()
                next_code = end_code + 1
                code_size = min_code_size + 1
            prefix = byte
        else:
            prefix = code

    emit(prefix)
    emit(end_code)
    if bits:
        out.append(acc & 0xFF)
    return bytes(out)
