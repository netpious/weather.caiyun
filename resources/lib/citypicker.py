# -*- coding: utf-8 -*-
"""城市选择：输入城市名 → 彩云 place 搜索 → 选中后写入坐标。"""

from . import api, kodi, net, weather


def pick(index):
    dialog = kodi.dialog()
    keyword = dialog.input(kodi.localize(32049), type=kodi.input_alphanum())
    if keyword is None:
        return False
    keyword = keyword.strip()
    if not keyword:
        return False

    client = net.Client(logger=kodi.log)
    try:
        raw = client.place(keyword)
        places = api.parse_place(raw)
    except Exception as exc:
        kodi.log("搜索城市失败：%s" % exc)
        kodi.notify(kodi.localize(32051), heading=kodi.localize(32048))
        return False

    if not places:
        kodi.notify("%s：%s" % (kodi.localize(32050), keyword),
                    heading=kodi.localize(32048))
        return False

    labels = []
    for place in places[:10]:
        name = api.short_place_name(place["address"]) or place["name"]
        labels.append("%s  (%.4f, %.4f)" % (name, place["lat"], place["lon"]))

    choice = dialog.select(kodi.localize(32048), labels)
    if choice is None or choice < 0:
        return False

    place = places[choice]
    name = api.short_place_name(place["address"]) or place["name"]
    weather.save_slot(index, name, place["lon"], place["lat"])
    kodi.notify("%s：%s" % (kodi.localize(32054), name), heading=kodi.localize(32048))

    # 立刻刷新一次，省得用户等下一个周期
    try:
        weather.update(index)
    except Exception as exc:
        kodi.log("选城后刷新失败：%s" % exc)
    return True
