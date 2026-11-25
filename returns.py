"""картинка поста «вернулось в магазин»: один крупный предмет или несколько в ряд"""
import io
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFilter

from card import S, W, _font, s
from style import ACCENT, MUTED, TEXT, background, _logo

RARITY_GLOW = {"legendary": (234, 141, 35), "epic": (195, 89, 255), "rare": (61, 200, 248),
               "uncommon": (105, 187, 30), "common": (160, 160, 160)}

F_KICK = _font("bold", 17)
F_BIG = _font("bold", 64)
F_DAYS = _font("bold", 30)
F_NAME = _font("bold", 40)
F_NAME_S = _font("bold", 24)
F_META = _font("bold", 22)
F_META_S = _font("bold", 17)
F_BRAND = _font("bold", 16)


def _hex(c, default=(80, 130, 170)):
    c = (c or "").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def _stage(w, h, c1, glow):
    """«сцена» под предметом: свечение цвета предмета и пол"""
    st = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    gd.ellipse((w * 0.22, h * 0.12, w * 0.78, h * 0.82), fill=c1 + (150,))
    gd.ellipse((w * 0.34, h * 0.22, w * 0.66, h * 0.6), fill=tuple(min(255, v + 60) for v in c1) + (110,))
    st.alpha_composite(g.filter(ImageFilter.GaussianBlur(min(w, h) // 9)))
    fl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(fl).ellipse((w * 0.18, h * 0.86, w * 0.82, h * 1.0), fill=glow + (110,))
    st.alpha_composite(fl.filter(ImageFilter.GaussianBlur(w // 25)))
    return st


def _put(img, art, box):
    x, y, w, h = box
    a = art.copy().convert("RGBA")
    bb = a.getbbox()
    if bb:
        a = a.crop(bb)          # убираем пустые поля вокруг предмета
    k = min(w / a.width, h / a.height)
    a = a.resize((max(1, int(a.width * k)), max(1, int(a.height * k))), Image.LANCZOS)
    sh = Image.new("RGBA", a.size, (0, 0, 0, 0))
    sh.putalpha(a.split()[3].point(lambda v: int(v * 0.5)))
    sh = Image.merge("RGBA", (*Image.new("RGB", a.size, (4, 10, 20)).split(), sh.split()[3])).filter(ImageFilter.GaussianBlur(s(10)))
    px, py = int(x + (w - a.width) / 2), int(y + h - a.height)
    img.alpha_composite(sh, (px, py + s(8)))
    img.alpha_composite(a, (px, py))


def render_returns(items: list, vbuck: Image.Image | None, channel="@stwradar") -> bytes:
    """items: [{name, type, rarity, days, price, out, image, color1}] — уже отсортированы по дням"""
    big = len(items) == 1
    w = s(W)
    n = len(items)
    rows_ = 1 if big else (n + min(n, 3) - 1) // min(n, 3)
    h = s(1250) if big else s(260 + rows_ * 356 + 110)
    img = background(w, h).convert("RGBA")
    d = ImageDraw.Draw(img)
    pad = s(56)

    # шапка: бренд и дата
    _logo(d, pad + s(16), s(58), s(16), img=img)
    d.text((pad + s(42), s(58)), "STORM RADAR", font=F_BRAND, fill=ACCENT, anchor="lm")
    d.text((w - pad, s(58)), datetime.now(timezone.utc).strftime("%d.%m.%Y"), font=F_BRAND, fill=MUTED, anchor="rm")

    d.text((pad, s(122)), "СНОВА В МАГАЗИНЕ", font=F_KICK, fill=ACCENT)
    if big:
        it = items[0]
        d.text((pad, s(150)), "Вернулся спустя", font=F_BIG, fill=TEXT)
        d.text((pad, s(226)), f"{it['days']} {plural(it['days'])}", font=F_BIG, fill=(255, 209, 102))
        c1 = _hex(it.get("color1"))
        glow = RARITY_GLOW.get(it.get("rarity", ""), c1)
        sx, sy, sw, shh = s(80), s(330), w - s(160), s(700)
        img.alpha_composite(_stage(sw, shh, c1, glow), (sx, sy))
        if it.get("image") is not None:
            _put(img, it["image"], (sx + s(40), sy + s(10), sw - s(80), shh - s(30)))
        d = ImageDraw.Draw(img)
        y = sy + shh + s(24)
        d.text((w / 2, y), it["name"], font=F_NAME, fill=TEXT, anchor="mt")
        meta = it.get("type", "")
        if meta:
            d.text((w / 2, y + s(56)), meta, font=F_META_S, fill=MUTED, anchor="mt")
        _price_row(img, d, w / 2, y + s(104), it, vbuck, F_META, center=True)
    else:
        n = len(items)
        d.text((pad, s(150)), "Давно не было", font=F_BIG, fill=TEXT)
        cols = min(n, 3)
        rows = (n + cols - 1) // cols
        gap = s(26)
        cw = (w - 2 * pad - gap * (cols - 1)) // cols
        ch = s(330)
        for i, it in enumerate(items):
            r, c = divmod(i, cols)
            x, y = pad + c * (cw + gap), s(260) + r * (ch + gap)
            c1 = _hex(it.get("color1"))
            glow = RARITY_GLOW.get(it.get("rarity", ""), c1)
            tile = Image.new("RGBA", (cw, ch), (10, 22, 38, 255))
            tile.alpha_composite(_stage(cw, ch - s(92), c1, glow), (0, 0))
            if it.get("image") is not None:
                _put(tile, it["image"], (s(14), s(10), cw - s(28), ch - s(104)))
            td = ImageDraw.Draw(tile)
            td.rectangle((0, ch - s(92), cw, ch), fill=(11, 24, 40))
            td.line((0, ch - s(92), cw, ch - s(92)), fill=glow, width=s(3))
            td.text((s(16), ch - s(78)), _cut(td, it["name"], F_NAME_S, cw - s(32)), font=F_NAME_S, fill=TEXT)
            _price_row(tile, td, s(16), ch - s(30), it, vbuck, F_META_S)
            # дни в углу
            txt = f"{it['days']} дн."
            tw = td.textlength(txt, font=F_META_S) + s(20)
            td.rounded_rectangle((s(12), s(12), s(12) + tw, s(44)), radius=s(10), fill=(255, 209, 102))
            td.text((s(12) + tw / 2, s(28)), txt, font=F_META_S, fill=(58, 38, 0), anchor="mm")
            m = Image.new("L", (cw, ch), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, cw - 1, ch - 1), radius=s(22), fill=255)
            gl = Image.new("RGBA", (cw + s(40), ch + s(40)), (0, 0, 0, 0))
            ImageDraw.Draw(gl).rounded_rectangle((s(20), s(20), s(20) + cw, s(20) + ch), radius=s(22), fill=glow + (70,))
            img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(s(12))), (x - s(20), y - s(20)))
            img.paste(tile, (x, y), m)
            ImageDraw.Draw(img).rounded_rectangle((x, y, x + cw - 1, y + ch - 1), radius=s(22), outline=glow + (230,), width=s(2))
        d = ImageDraw.Draw(img)

    d.text((w / 2, h - s(46)), f"магазин каждый день  ·  {channel}", font=F_BRAND, fill=MUTED, anchor="mm")
    out = img.convert("RGB").resize((1440, round(h * 1440 / w)), Image.LANCZOS)
    out = out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _price_row(img, d, x, y, it, vbuck, font, center=False):
    parts = f"{it['price']}"
    till = f"до {it['out']}" if it.get("out") else ""
    pw = d.textlength(parts, font=font)
    iw = s(30) if vbuck is not None else 0
    tw = d.textlength(till, font=font) if till else 0
    total = iw + pw + (s(22) + tw if till else 0)
    x0 = x - total / 2 if center else x
    if vbuck is not None:
        v = vbuck.copy().convert("RGBA")
        v.thumbnail((s(26), s(26)), Image.LANCZOS)
        img.alpha_composite(v, (int(x0), int(y - v.height / 2)))
    d.text((x0 + iw, y), parts, font=font, fill=TEXT, anchor="lm")
    if till:
        d.text((x0 + iw + pw + s(22), y), till, font=font, fill=MUTED, anchor="lm")


def _cut(d, text, font, maxw):
    if d.textlength(text, font=font) <= maxw:
        return text
    while text and d.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


def plural(n):
    m, h = n % 10, n % 100
    return "день" if m == 1 and h != 11 else "дня" if 2 <= m <= 4 and not 12 <= h <= 14 else "дней"
