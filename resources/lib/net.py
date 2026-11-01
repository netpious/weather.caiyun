# -*- coding: utf-8 -*-
"""彩云天气网页通道的 HTTP 客户端（纯标准库）。

网页 (h5.caiyunapp.com) 的真实链路只有三步：

  1. GET  https://h5.caiyunapp.com/h5
          ← Set-Cookie: ticket=eyJ...

  2. POST https://h5.caiyunapp.com/api/
          {"url": "https://api.caiyunapp.com/v2.5/<t2.5>/120.2,30.3/weather", "method": "get"}

  3. 服务端把 <t1> / <t2.5> 占位符替换成真实 token 后转发，返回原始 JSON。

换句话说：token 不在前端，也不需要注册 API Key；但必须带 ticket Cookie，
否则 /api/ 返回 401 auth failed。
"""

import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

HOME_URL = "https://h5.caiyunapp.com/h5"
API_URL = "https://h5.caiyunapp.com/api/"
API_HOST = "https://api.caiyunapp.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TICKET_TTL = 240  # 秒；票据有效期未知，宁短勿长


class NetError(Exception):
    """网络/协议错误，调用方据此降级。"""


def _context(verify=True):
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


class Client(object):
    """带 ticket 的会话；一个实例复用到本轮更新结束。"""

    def __init__(self, timeout=20, logger=None, verify_ssl=True):
        self.timeout = timeout
        self.logger = logger or (lambda message: None)
        self.verify_ssl = verify_ssl
        self._ticket = None
        self._ticket_at = 0.0
        self._contexts = {}

    # ---- 底层 -------------------------------------------------------

    def _ctx(self):
        key = bool(self.verify_ssl)
        if key not in self._contexts:
            self._contexts[key] = _context(self.verify_ssl)
        return self._contexts[key]

    def _open(self, request):
        try:
            return urllib.request.urlopen(
                request, timeout=self.timeout, context=self._ctx()
            )
        except ssl.SSLError as exc:
            # 少数 Kodi 环境没有可用 CA 证书，退回不校验（天气数据非敏感）
            if self.verify_ssl:
                self.logger("SSL 校验失败，改为不校验：%s" % exc)
                self.verify_ssl = False
                return self._open(request)
            raise NetError("SSL 错误：%s" % exc)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            raise NetError("网络不可达：%s" % exc)
        except OSError as exc:
            raise NetError("网络错误：%s" % exc)

    @staticmethod
    def _read(response):
        raw = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")

    # ---- ticket -----------------------------------------------------

    def ticket(self, force=False):
        fresh = (
            self._ticket
            and not force
            and (time.time() - self._ticket_at) < TICKET_TTL
        )
        if fresh:
            return self._ticket

        request = urllib.request.Request(
            HOME_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        with self._open(request) as response:
            cookies = response.headers.get_all("Set-Cookie") or []
            self._read(response)

        value = None
        for cookie in cookies:
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("ticket="):
                    value = part[len("ticket="):]
                    break
            if value:
                break

        if not value:
            raise NetError("未能从网页取得 ticket")
        self._ticket = value
        self._ticket_at = time.time()
        self.logger("已取得 ticket（%d 字节）" % len(value))
        return value

    # ---- 代理 -------------------------------------------------------

    def proxy(self, target_url, method="get"):
        """把一条 api.caiyunapp.com 的 URL 交给网页端代理转发。"""
        payload = json.dumps({"url": target_url, "method": method}).encode("utf-8")

        for attempt in (0, 1):
            request = urllib.request.Request(
                API_URL,
                data=payload,
                method="POST",
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json;charset=utf-8",
                    "Accept": "application/json",
                    "Origin": "https://h5.caiyunapp.com",
                    "Referer": "https://h5.caiyunapp.com/h5",
                    "Cookie": "ticket=%s" % self.ticket(force=bool(attempt)),
                },
            )
            try:
                with self._open(request) as response:
                    text = self._read(response)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self.logger("票据失效，重新获取后重试")
                    continue
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                raise NetError("HTTP %s %s" % (exc.code, detail))
        else:  # pragma: no cover - 双次 401
            raise NetError("鉴权失败")

        try:
            data = json.loads(text)
        except ValueError:
            raise NetError("返回不是 JSON：%s" % text[:120])
        if isinstance(data, dict) and data.get("status") == "failed":
            raise NetError("接口报错：%s" % data.get("error", data))
        return data

    # ---- 便捷方法 ---------------------------------------------------

    def weather(self, lon, lat, days=16, hours=24, alert=True, lang="zh_CN"):
        begin = int(time.time()) - 86400
        url = (
            "%s/v2.5/<t2.5>/%.4f,%.4f/weather"
            "?lang=%s&dailysteps=%d&hourlysteps=%d&alert=%s&begin=%d"
            % (API_HOST, lon, lat, lang, days, hours, "true" if alert else "false", begin)
        )
        return self.proxy(url)

    def place(self, keyword):
        url = "%s/v2/place?query=%s&token=<t1>" % (
            API_HOST,
            urllib.parse.quote(keyword),
        )
        return self.proxy(url)

    def radar(self, lon, lat, level=1, forecast=False):
        endpoint = "forecast_images" if forecast else "fine_images"
        url = "%s/v1/radar/%s?lon=%.4f&lat=%.4f&level=%d&token=<t1>" % (
            API_HOST,
            endpoint,
            lon,
            lat,
            level,
        )
        return self.proxy(url)

    def radar_images(self, lon, lat, level=1):
        """一次拿全：images（实况 20 帧）+ forecast_images（预报 25 帧）。"""
        url = "%s/v1/radar/fine_images?lon=%.4f&lat=%.4f&level=%d&token=<t1>" % (
            API_HOST,
            lon,
            lat,
            level,
        )
        return self.proxy(url)

    def aqi_images(self, lon, lat):
        """空气质量图层：全国范围的 AQI 栅格图，实况 + 预报。"""
        url = "%s/v1/aqi/images?token=<t1>&lonlat=%.4f,%.4f&forecast=true" % (
            API_HOST,
            lon,
            lat,
        )
        return self.proxy(url)

    def lifeindex(self, lon, lat, fields):
        url = "%s/v1/lifeindex/?token=<t1>&lng=%.4f&lat=%.4f&fields=%s" % (
            API_HOST,
            lon,
            lat,
            fields,
        )
        return self.proxy(url)

    def vehicle_limit(self, lon, lat):
        url = "%s/v1/vehicle_limit?lng=%.4f&lat=%.4f&token=<t1>" % (
            API_HOST,
            lon,
            lat,
        )
        return self.proxy(url)


def fetch_bytes(url, timeout=25, referer="https://h5.caiyunapp.com/"):
    """下载图片等二进制资源（雷达帧、底图瓦片）。"""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "Accept": "image/*,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context(True)) as response:
            return response.read()
    except Exception as exc:
        raise NetError("下载失败：%s" % exc)
