# -*- coding: utf-8 -*-
"""极简 PNG 编解码（纯标准库，不依赖 PIL）。

Kodi 不带 Pillow，而彩云只给透明叠加层，底图必须自己拼瓦片。所以这里实现
刚好够用的一个子集：

* 解码：8 位、非隔行，颜色类型 0/2/3/4/6（灰度、RGB、调色板、灰度+α、RGBA）
* 编码：RGB、filter 0
* 裁剪与拼接

高德瓦片是 256×256、8 位调色板 PNG，正好落在这个子集里。
"""

import struct
import zlib

CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
MODE_OF = {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}


class PngError(Exception):
    pass


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _expand_bits(data, width, height, channels, depth):
    """1/2/4 位样本 → 每样本一字节（高德偶尔给 1 位空瓦片）。"""
    if depth == 1 and channels == 1:
        # 1 位图可以用查表整字节展开，比逐样本循环快得多
        table = [bytes(((byte >> shift) & 1) for shift in (7, 6, 5, 4, 3, 2, 1, 0))
                 for byte in range(256)]
        per_row = (width + 7) // 8
        out = bytearray(width * height)
        for y in range(height):
            row = data[y * per_row:(y + 1) * per_row]
            expanded = b"".join(table[value] for value in row)
            out[y * width:(y + 1) * width] = expanded[:width]
        return out

    samples_per_byte = 8 // depth
    mask = (1 << depth) - 1
    row_in = (width * channels * depth + 7) // 8
    out = bytearray(width * height * channels)
    for y in range(height):
        base = y * row_in
        start = y * width * channels
        for x in range(width * channels):
            value = data[base + x // samples_per_byte]
            shift = 8 - depth * ((x % samples_per_byte) + 1)
            out[start + x] = (value >> shift) & mask
    return out


def decode(data):
    """→ (width, height, mode, pixels)。mode ∈ {'L','RGB','RGBA'}。"""
    if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngError("不是 PNG")
    pos = 8
    idat = []
    palette = None
    trns = None
    width = height = depth = ctype = interlace = None

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _cm, _fl, interlace = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break

    if width is None:
        raise PngError("缺少 IHDR")
    if depth not in (1, 2, 4, 8):
        raise PngError("不支持色深 %s" % depth)
    if depth != 8 and ctype not in (0, 3):
        raise PngError("色深 %s 只支持灰度/调色板" % depth)
    if interlace:
        raise PngError("不支持隔行 PNG")
    if ctype not in CHANNELS:
        raise PngError("不支持的颜色类型 %s" % ctype)

    channels = CHANNELS[ctype]
    stride = (width * channels * depth + 7) // 8
    raw = zlib.decompress(b"".join(idat))
    if len(raw) < (stride + 1) * height:
        raise PngError("图像数据不完整")

    out = bytearray(stride * height)
    prev = bytearray(stride)
    offset = 0
    bpp = channels if depth == 8 else 1
    for y in range(height):
        filter_type = raw[offset]
        offset += 1
        base = y * stride
        if filter_type == 0:
            out[base:base + stride] = raw[offset:offset + stride]
            offset += stride
            prev = out[base:base + stride]
            continue

        line = bytearray(raw[offset:offset + stride])
        offset += stride

        if filter_type == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(bpp):
                line[i] = (line[i] + (prev[i] >> 1)) & 0xFF
            for i in range(bpp, stride):
                line[i] = (line[i] + ((line[i - bpp] + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth（内联，省掉每字节一次函数调用）
            previous = prev
            for i in range(bpp):
                line[i] = (line[i] + previous[i]) & 0xFF
            for i in range(bpp, stride):
                a = line[i - bpp]
                b = previous[i]
                c = previous[i - bpp]
                p = a + b - c
                pa = p - a
                if pa < 0:
                    pa = -pa
                pb = p - b
                if pb < 0:
                    pb = -pb
                pc = p - c
                if pc < 0:
                    pc = -pc
                if pa <= pb:
                    value = a if pa <= pc else c
                elif pb <= pc:
                    value = b
                else:
                    value = c
                line[i] = (line[i] + value) & 0xFF
        elif filter_type != 0:
            raise PngError("未知过滤器 %s" % filter_type)

        out[base:base + stride] = line
        prev = line

    if depth < 8:
        out = _expand_bits(out, width, height, channels, depth)
        stride = width * channels

    if ctype == 2:
        return width, height, "RGB", out
    if ctype == 6:
        return width, height, "RGBA", out
    if ctype == 0:
        return width, height, "L", out
    if ctype == 4:
        # 灰度+α → 灰度
        gray = bytearray(width * height)
        for i in range(width * height):
            gray[i] = out[i * 2]
        return width, height, "L", gray

    # 调色板 → RGB
    if palette is None:
        raise PngError("调色板缺失")
    count = width * height
    entries = len(palette) // 3
    # 三个通道各做一张 256 字节的翻译表，再用切片赋值按通道写入。
    # 这一步比逐像素循环快一个数量级：translate 和带步长的切片都在 C 里跑。
    tables = []
    for channel in range(3):
        table = bytes(
            palette[index * 3 + channel] if index < entries else 0
            for index in range(256)
        )
        tables.append(table)
    indices = bytes(out)
    rgb = bytearray(count * 3)
    rgb[0::3] = indices.translate(tables[0])
    rgb[1::3] = indices.translate(tables[1])
    rgb[2::3] = indices.translate(tables[2])
    return width, height, "RGB", rgb


def encode_rgb(width, height, pixels):
    """RGB 像素 → PNG 字节（filter 0，压缩级别 6）。"""
    return _encode(width, height, pixels, 3, 2)


def encode_rgba(width, height, pixels):
    """RGBA 像素 → PNG 字节（filter 0）。"""
    return _encode(width, height, pixels, 4, 6)


def scale_alpha(pixels, table):
    """RGBA 就地按 256 项查表替换 alpha（用来把叠加层调得更醒目）。"""
    pixels = bytearray(pixels)
    pixels[3::4] = bytes(pixels[3::4]).translate(table)
    return pixels


def crop_rgba(width, height, pixels, x0, y0, x1, y1):
    """RGBA 裁剪（不缩放）。"""
    x0 = max(0, min(width - 1, int(x0)))
    y0 = max(0, min(height - 1, int(y0)))
    x1 = max(x0 + 1, min(width, int(x1)))
    y1 = max(y0 + 1, min(height, int(y1)))
    out = bytearray()
    for y in range(y0, y1):
        out += pixels[(y * width + x0) * 4:(y * width + x1) * 4]
    return x1 - x0, y1 - y0, out


def alpha_table(factor):
    """alpha × factor，超 255 截断。"""
    return bytes(min(255, int(round(value * factor))) for value in range(256))


def _encode(width, height, pixels, channels, color_type):
    stride = width * channels
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride:(y + 1) * stride])

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def size(path):
    """只读文件头拿宽高，不做完整解码。"""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except (IOError, OSError):
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


class Canvas(object):
    """RGB 画布，支持粘贴与裁剪。"""

    def __init__(self, width, height, color=(0, 0, 0)):
        self.width = width
        self.height = height
        self.data = bytearray(bytes(color) * (width * height))

    def paste(self, other, x, y):
        src_w, src_h, src = other
        for row in range(src_h):
            dst_y = y + row
            if dst_y < 0 or dst_y >= self.height:
                continue
            start = row * src_w * 3
            line = src[start:start + src_w * 3]
            left = 0
            right = src_w
            if x < 0:
                left = -x
                line = line[left * 3:]
            if x + src_w > self.width:
                right = self.width - x
                line = line[:(right - left) * 3]
            if right <= left:
                continue
            offset = (dst_y * self.width + x + left) * 3
            self.data[offset:offset + len(line)] = line

    def crop(self, x0, y0, x1, y1):
        x0 = max(0, int(x0))
        y0 = max(0, int(y0))
        x1 = min(self.width, int(x1))
        y1 = min(self.height, int(y1))
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        out = bytearray(width * height * 3)
        for row in range(height):
            start = ((y0 + row) * self.width + x0) * 3
            out[row * width * 3:(row + 1) * width * 3] = self.data[start:start + width * 3]
        return width, height, out
