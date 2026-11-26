"""собирает лёгкие файлы для миниаппа: data/shop.json (сегодняшний магазин) и data/catalog.json (все предметы).
запускается гитхабом каждый день, без секретов."""
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import sys

START = time.time()
IMG_BUDGET = 7 * 60      # на картинки за запуск — не больше 7 минут (магазин качается первым), остальное докачается в следующие запуски
XB_BUDGET = 3 * 60       # на xbox-магазин — не больше 3 минут
sys.stdout.reconfigure(line_buffering=True)   # чтобы лог в Actions шёл сразу
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

API = "https://fortnite-api.com/v2"
OUT = Path("site/data")
IMG = Path("site/img")
SHOP_PX, CAT_PX = 800, 352   # магазин — крупно и чётко; каталог — из большой иконки, а не маленькой
CAT_PER_RUN = 2500   # за запуск качаем не больше стольких новых иконок
KNOWN = {"outfit", "emote", "pickaxe", "glider", "backpack", "wrap", "shoe", "contrail", "music", "loadingscreen",
         "spray", "emoji", "pet", "petcarrier", "toy"}


def get(url: str, tries: int = 6) -> dict:
    """fortnite-api иногда отвечает 503/502/429 или рвёт соединение на пару минут —
    повторяем с паузами (10, 20, 30, 45, 60 с), а не валим всю сборку"""
    req = urllib.request.Request(url, headers={"User-Agent": "stwradar-app"})
    waits = [10, 20, 30, 45, 60]
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429 or n == tries - 1:
                raise
            why = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            if n == tries - 1:
                raise
            why = type(e).__name__
        w = waits[min(n, len(waits) - 1)]
        print(f"  {url.split('?')[0]}: {why}, повтор через {w} с ({n + 1}/{tries - 1})")
        time.sleep(w)


def safe(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in s)


def day(s: str) -> date:
    return date.fromisoformat(s[:10])


def col(h: str | None) -> str:
    h = (h or "").lstrip("#")
    return "#" + h[:6] if len(h) >= 6 else "#3b5f80"


# цвета редкостей как в игре: (верх градиента, низ градиента)
RARITY = {
    "common": ("#9a9a9a", "#4c4c4c"),
    "uncommon": ("#69bb1e", "#1c5a12"),
    "rare": ("#3dc8f8", "#135296"),
    "epic": ("#c359ff", "#4a1f86"),
    "legendary": ("#ea8d23", "#7a3416"),
    "mythic": ("#ffd84a", "#9c7420"),
    "exotic": ("#76d6e3", "#2f7b86"),
    "transcendent": ("#ff4f7d", "#7a1534"),
}


def rarity_colors(obj: dict) -> tuple[str, str, str]:
    """цвета карточки по редкости или серии (Marvel, Icon, DC...): фон сверху, фон снизу, рамка"""
    ser = obj.get("series") or {}
    cols = [c for c in (ser.get("colors") or []) if isinstance(c, str) and len(c) >= 6]
    if cols:
        top = "#" + cols[0][:6]
        bot = "#" + (cols[2] if len(cols) > 2 else cols[-1])[:6]
        return top, bot, top
    r = ((obj.get("rarity") or {}).get("value") or "").lower()
    top, bot = RARITY.get(r, RARITY["common"])
    return top, bot, top


# откуда предмет: по тегам игры (Cosmetics.Source.*) + был ли он в магазине
SOURCE_WORDS = [
    ("itemshop", "Магазин"), ("battlepass", "Боевой пропуск"), ("subscription", "Отряд Fortnite"), ("crew", "Отряд Fortnite"),
    ("tournament", "Турнир"), ("cup", "Турнир"), ("competitive", "Турнир"), ("ranked", "Рейтинговый режим"),
    ("twitch", "Twitch Drops"), ("drop", "Twitch Drops"), ("quest", "Задания"), ("challenge", "Задания"),
    ("refer", "Пригласи друга"), ("starterpack", "Стартовый набор"), ("pack", "Платный набор"),
    ("promo", "Промо-акция"), ("event", "Событие"), ("festival", "Fortnite Festival"), ("lego", "LEGO Fortnite"),
    ("rocket", "Rocket Pass"), ("winterfest", "Зимний фестиваль"), ("fortnitemares", "Кошмарный Fortnite"),
    ("savetheworld", "Сражение с Бурей"), ("stw", "Сражение с Бурей"), ("founder", "Набор основателя"),
    ("playstation", "Эксклюзив PlayStation"), ("xbox", "Эксклюзив Xbox"), ("nintendo", "Эксклюзив Nintendo"),
    ("samsung", "Эксклюзив Samsung"), ("mobile", "Мобильная акция"), ("gift", "Подарок"),
]


def sources(obj: dict) -> list[str]:
    out = []
    for t in obj.get("gameplayTags") or []:
        if not isinstance(t, str) or not t.lower().startswith("cosmetics.source."):
            continue
        rest = t[len("cosmetics.source."):].lower().replace(".", "").replace("_", "")
        label = next((ru for key, ru in SOURCE_WORDS if key in rest), None)
        # неизвестные служебные теги не показываем, чтобы не было английской каши
        if label and label not in out:
            out.append(label)
    if obj.get("shopHistory") and "Магазин" not in out:
        out.insert(0, "Магазин")
    return out


def intro(obj: dict) -> str:
    i = obj.get("introduction") or {}
    if i.get("chapter") and i.get("season"):
        return f"Глава {i['chapter']}, сезон {i['season']}"
    return (i.get("text") or "").strip().rstrip(".")


def kind_of(t: str) -> str:
    return t if t in KNOWN else "other"


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
FAILS = []


def fetch_img(url: str, path: Path, px: int) -> bool:
    """скачиваем, ужимаем в webp и кладём рядом с сайтом — так всё грузится с github.io без впн"""
    if path.exists():
        return True
    if time.time() - START > IMG_BUDGET:
        return False   # время вышло — докачаем в следующий запуск
    from PIL import Image
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
            with urllib.request.urlopen(req, timeout=15) as r:
                im = Image.open(io.BytesIO(r.read())).convert("RGBA")
            im.thumbnail((px, px), Image.LANCZOS)
            path.parent.mkdir(parents=True, exist_ok=True)
            im.save(path, "WEBP", quality=90 if px >= 700 else 86, method=6)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(float(e.headers.get("Retry-After") or 2 * (attempt + 1)))
                continue
            FAILS.append(f"{e.code} {url}")
            return False
        except Exception as e:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            FAILS.append(f"{type(e).__name__} {url}")
            return False
    return False


def mirror(jobs: list[tuple[str, Path, int]], workers: int = 6) -> None:
    jobs = [j for j in jobs if j[0] and not j[1].exists()]
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda j: fetch_img(*j), jobs))


def local_or_remote(remote: str, path: Path) -> str:
    """если картинка скачалась — отдаём свою копию, если нет — оригинальную ссылку"""
    return str(path.relative_to("site")).replace("\\", "/") if path.exists() else remote


def gg_numbers() -> dict:
    """номера предметов на fortnite.gg (из ggitems.json + ggextra.json), ключи в нижнем регистре"""
    mp = {}
    for f in ("ggitems.json", "ggextra.json"):
        try:
            mp.update({k.lower(): str(v) for k, v in json.loads(Path(f).read_text(encoding="utf-8")).items()})
        except Exception:
            pass
    return mp


def pick_img(e, br, nda, imgs, gg, is_b=False) -> str:
    ids = {(b.get("id") or "").lower() for b in br}
    nd = e.get("newDisplayAsset") or {}
    ndid, cid = (nd.get("id") or "").lower(), (nd.get("cosmeticId") or "").lower()
    # верим картинке магазина, только если она точно про этот предмет: совпал cosmeticId или id предмета есть в её id
    nd_ok = bool(nda) and ((cid and cid in ids) or (not cid and any(i and i in ndid for i in ids)))
    if is_b:
        # обложка набора: из api, иначе коллаж с fortnite.gg (если знаем номер набора), иначе картинка магазина
        gg_b = f"https://fortnite.gg/img/items/{gg[cid]}/featured.png?1" if cid and gg.get(cid) else None
        return ((e.get("bundle") or {}).get("image") or gg_b or (nda[0]["image"] if nda else None)
                or imgs.get("featured") or imgs.get("icon") or "")
    first = (br[0].get("id") or "").lower()
    gg_img = f"https://fortnite.gg/img/items/{gg[first]}/featured.png?1" if gg.get(first) else None
    return (nda[0]["image"] if nd_ok else None) or imgs.get("featured") or gg_img or imgs.get("icon") or ""


def fresh_shop() -> dict:
    """магазин меняется в 00:00 UTC, а fortnite-api иногда отдаёт новый на несколько минут позже —
    в первые полчаса после смены ждём свежий (до 20 минут), чтобы не собрать вчерашний"""
    global START
    for i in range(21):
        d = get(f"{API}/shop?language=ru&responseFlags=4")["data"]
        now = datetime.now(timezone.utc)
        if day(d["date"]) >= now.date() or now.hour != 0 or now.minute > 40:
            if i:
                print(f"свежий магазин появился через {i} мин")
                START = time.time()   # время на картинки считаем с этого момента
            return d
        print(f"магазин ещё вчерашний ({day(d['date'])}), жду минуту…")
        time.sleep(60)
    return d


def build_shop() -> dict:
    d = fresh_shop()
    today = day(d["date"])
    now = datetime.now(timezone.utc)
    items, seen = [], set()
    gg = gg_numbers()
    for pos, e in enumerate(d.get("entries") or []):
        br = e.get("brItems") or []
        if not br or e.get("offerId") in seen:
            continue
        if e.get("outDate") and datetime.fromisoformat(e["outDate"].replace("Z", "+00:00")) < now:
            continue
        seen.add(e["offerId"])
        first = br[0]
        hist = {day(h) for b in br for h in (b.get("shopHistory") or [])}
        prior = sorted(t for t in hist if t < today)
        last = prior[-1] if prior else None
        is_new = (last is None) if hist else ((e.get("banner") or {}).get("backendValue") == "New")
        days = (today - last).days if last else None
        # набор: либо помечен в api, либо несколько предметов со скидкой за комплект
        # (у некоторых наборов, например «Знаменитая Эддисон Рэй» за 3600, fortnite-api не ставит пометку bundle)
        is_b = bool(e.get("bundle")) or (len(br) >= 2 and (e.get("regularPrice") or 0) > (e.get("finalPrice") or 0))
        t = "bundle" if is_b else (first.get("type") or {}).get("value", "other")
        nda = (e.get("newDisplayAsset") or {}).get("renderImages") or []
        imgs = first.get("images") or {}
        items.append({
            "id": e["offerId"],
            "name": (e.get("bundle") or {}).get("name") or (f"Набор «{first.get('name')}»" if is_b else first.get("name")),
            "kind": t if t == "bundle" else kind_of(t),
            "typeName": "Набор" if is_b else (first.get("type") or {}).get("displayValue", ""),
            "desc": first.get("description") or "",
            "rarity": (first.get("series") or {}).get("value") or (first.get("rarity") or {}).get("displayValue", ""),
            "set": (first.get("set") or {}).get("text", ""),
            "rb": rarity_colors(first)[2],
            "src": sources({"gameplayTags": [t for b in br for t in (b.get("gameplayTags") or [])],
                            "shopHistory": first.get("shopHistory")}),
            "intro": intro(first),
            "price": e.get("finalPrice"), "regular": e.get("regularPrice"),
            "out": e.get("outDate"),
            # картинка как в игровом магазине (newDisplayAsset); у «проблемных» лотов её нет — тогда берём с fortnite.gg
            # картинка: у набора — его собственная обложка (как в игре, все предметы вместе);
            # у предмета — newDisplayAsset, но только если он реально про этот предмет (иначе бывают чужие, как черепаха у микрофона)
            "img": pick_img(e, br, nda, imgs, gg, is_b),
            "alt": imgs.get("featured") or imgs.get("icon") or "",
            "c1": col((e.get("colors") or {}).get("color1")),
            "c2": col((e.get("colors") or {}).get("color3") or (e.get("colors") or {}).get("color2")),
            "isNew": is_new, "days": days, "returned": (not is_new) and days is not None and days >= 2,
            "times": len(prior), "firstSeen": prior[0].isoformat() if prior else None,
            "lastSeen": last.isoformat() if last else None,
            "video": next((b.get("showcaseVideo") for b in br if b.get("showcaseVideo")), None),
            "parts": [{"id": b.get("id", ""), "name": b.get("name"), "img": (b.get("images") or {}).get("smallIcon")}
                      for b in br] if is_b else [],
            "keys": [b.get("id", "") for b in br],
            "n": len(br),
            "order": pos,
            "sec": (e.get("layout") or {}).get("name") or "",
        })
    # зеркалим картинки магазина (каждый день новые) и иконку в-баксов
    jobs, links = [], []
    for it in items:
        if it["img"]:
            p = IMG / "shop" / f"{safe(it['id'])}.webp"
            jobs.append((it["img"], p, SHOP_PX)); links.append((it, "img", it["img"], p))
        for part in it["parts"]:
            if part.get("img"):
                p = IMG / "c3" / f"{safe(part['id'])}.webp"
                jobs.append((part["img"], p, CAT_PX)); links.append((part, "img", part["img"], p))
    vb = d.get("vbuckIcon") or ""
    vbp = IMG / "vbuck.webp"
    if vb:
        jobs.append((vb, vbp, 64))
    mirror(jobs)
    # если основную картинку скачать не дали (например, fortnite.gg), сохраняем запасную —
    # в миниаппе сначала грузится основная по ссылке, а не загрузилась — подставляется запасная
    jobs2 = []
    for it in items:
        p = IMG / "shop" / f"{safe(it['id'])}.webp"
        alt = it.get("alt") or ""
        if p.exists() or not alt or alt == it["img"]:
            it["alt"] = ""
            continue
        pa = IMG / "shop" / f"{safe(it['id'])}_a.webp"
        jobs2.append((alt, pa, SHOP_PX)); it["_pa"] = pa
    mirror(jobs2)
    for it in items:
        pa = it.pop("_pa", None)
        if pa is not None:
            it["alt"] = local_or_remote(it["alt"], pa)
    for obj, key, remote, p in links:
        obj[key] = local_or_remote(remote, p)
    ok = sum(1 for *_, p in links if p.exists())
    print(f"картинки магазина: {ok} из {len(links)} скачано")
    return {"date": today.isoformat(), "vbuck": local_or_remote(vb, vbp) if vb else "", "items": items}


def build_catalog() -> list:
    data = get(f"{API}/cosmetics/br?language=ru&responseFlags=4")["data"]
    out = []
    for c in data:
        imgs = c.get("images") or {}
        icon = imgs.get("icon") or imgs.get("smallIcon")
        if not icon or not c.get("name"):
            continue
        hist = sorted({h[:10] for h in (c.get("shopHistory") or [])})
        out.append({
            "id": c["id"], "n": c["name"], "k": kind_of((c.get("type") or {}).get("value", "")),
            "t": (c.get("type") or {}).get("displayValue", ""),
            "r": (c.get("series") or {}).get("value") or (c.get("rarity") or {}).get("displayValue", ""),
            "c1": rarity_colors(c)[0], "c2": rarity_colors(c)[1],
            "o": sources(c), "in": intro(c),
            "s": (c.get("set") or {}).get("text", ""),
            "d": c.get("description") or "",
            "i": icon, "b": imgs.get("icon") or icon,
            "v": c.get("showcaseVideo"),
            "h": hist[-1] if hist else None, "c": len(hist), "f": hist[0] if hist else None,
            "a": (c.get("added") or "")[:10],
        })
    # сначала то, что недавно было в магазине, потом остальное по дате добавления
    out.sort(key=lambda x: (x["h"] or "", x["a"]), reverse=True)
    # иконки зеркалим постепенно: за запуск не больше CAT_PER_RUN новых (сначала недавние),
    # остальные пока по оригинальной ссылке; уже скачанные лежат в кэше гитхаба
    jobs = []
    for x in out:
        p = IMG / "c3" / f"{safe(x['id'])}.webp"
        if not p.exists() and len(jobs) < CAT_PER_RUN:
            jobs.append((x["i"], p, CAT_PX))
    mirror(jobs)
    have = 0
    for x in out:
        p = IMG / "c3" / f"{safe(x['id'])}.webp"
        x["i"] = x["b"] = local_or_remote(x["i"], p)
        have += p.exists()
    print(f"иконки каталога: {have} из {len(out)} уже свои")
    return out


# ---------- номера предметов на fortnite.gg (для видео с их водяным знаком)
# таблица «id из игры -> номер на fortnite.gg» лежит в репо (ggitems.json), к fortnite.gg мы не ходим
def fresh_ggitems() -> dict:
    """раз в день пробуем взять свежую таблицу с fortnite.gg (один запрос, бот представляется честно).
    не пустили — работаем с копией из репо, ничего не обходим"""
    try:
        req = urllib.request.Request("https://fortnite.gg/api/items.json",
                                     headers={"User-Agent": "StormRadarBot/1.0 (+https://t.me/stwradar)", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        if isinstance(d, dict) and len(d) > 1000:
            print(f"свежая таблица fortnite.gg: {len(d)} предметов")
            return d
    except urllib.error.HTTPError as e:
        print(f"свежую таблицу fortnite.gg не дали ({e.code}) — беру копию из репо")
    except Exception as e:
        print(f"свежая таблица fortnite.gg: {type(e).__name__} — беру копию из репо")
    return {}


def build_videos(shop: dict, catalog: list) -> dict:
    try:
        mp = json.loads(Path("ggitems.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ggitems.json не прочитан: {e}")
        mp = {}
    mp.update(fresh_ggitems())
    # ручные номера для новинок: ggextra.json вида {"EID_Новый": 25327}
    try:
        mp.update({k: str(v) for k, v in json.loads(Path("ggextra.json").read_text(encoding="utf-8")).items()})
    except Exception:
        pass
    low = {k.lower(): v for k, v in mp.items()}
    need = {k for it in shop["items"] for k in it["keys"]} | {c["id"] for c in catalog}
    out = {}
    for cid in need:
        v = mp.get(cid) or low.get(cid.lower())
        if v and str(v).isdigit():
            out[cid] = int(v)
    print(f"видео fortnite.gg: номера есть у {len(out)} из {len(need)} предметов")
    return out


# ---------- наборы: обложка в хорошем качестве и состав берём из магазина xbox (открытый каталог microsoft)
XB_TYPES = r"(outfit|back bling|pickaxe|harvesting tool|glider|wrap|emote|contrail|loading screen|spray|emoticon|music pack|lobby track|jam track|kicks|shoes|banner|sidekick)"


def xb_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "StormRadarBot/1.0 (+https://t.me/stwradar)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.load(r)


def xb_find(p):
    """ищем id набора в xbox-сторе по названию (если id не прописан руками)"""
    tries = []
    if p.get("en"):
        tries.append(("en-US", "Fortnite - " + p["en"], lambda t: t.lower().replace("–", "-").strip() == ("fortnite - " + p["en"]).lower()))
    if p.get("name"):
        tries.append(("ru-RU", "Fortnite " + p["name"], lambda t: ("«" + p["name"].lower() + "»") in t.lower()))
    for loc, q, ok in tries:
        try:
            url = "https://storeedgefd.dsx.mp.microsoft.com/v9.0/search?" + urllib.parse.urlencode(
                {"market": "US", "locale": loc, "deviceFamily": "Windows.Xbox", "query": q})
            for r in (xb_json(url).get("Payload") or {}).get("SearchResults") or []:
                if ok(r.get("Title") or ""):
                    return r.get("ProductId")
        except Exception as e:
            print(f"  поиск xbox «{q}»: {type(e).__name__}")
    return None


def xb_product(pid, lang):
    url = f"https://displaycatalog.mp.microsoft.com/v7.0/products?bigIds={pid}&market=US&languages={lang}"
    prods = xb_json(url).get("Products") or []
    return ((prods[0].get("LocalizedProperties") or [{}])[0]) if prods else {}


def xb_items(desc: str) -> list:
    """из описания «Includes: - X Outfit - Y Back Bling ...» достаём названия предметов"""
    desc = re.split(r"V-Bucks are in-game currency", desc or "")[0]
    out = []
    for part in re.split(r"\s+-\s+|\n-\s*|•", desc):
        part = re.sub(r"\((?:with|includes)[^)]*\)", "", part, flags=re.I).strip(" .:;")
        m = re.match(r"^(.+?)\s+" + XB_TYPES + r"\b", part, re.I) if part else None
        if m and len(m.group(1)) < 40 and not re.search(r"v-bucks|quest|challenge|access to|experience", m.group(1), re.I):
            out.append(m.group(1).strip())
    return out


def accent(im) -> str:
    """главный яркий цвет обложки — для обводки карточки набора"""
    import colorsys
    from PIL import Image
    sm = im.convert("RGB").resize((48, 48))
    q = sm.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    best, score = (120, 180, 255), -1
    for cnt, idx in q.getcolors():
        r, g, b = pal[idx * 3: idx * 3 + 3]
        h, l, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        sc = cnt * (sat ** 1.5) * (1 - abs(l - 0.5))   # частый, насыщенный и не слишком тёмный/светлый
        if sc > score:
            best, score = (r, g, b), sc
    h, l, sat = colorsys.rgb_to_hls(*(c / 255 for c in best))
    r, g, b = colorsys.hls_to_rgb(h, min(max(l, 0.55), 0.68), max(sat, 0.75))
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def xb_perks(desc: str) -> list:
    """что ещё даёт набор, кроме предметов: в-баксы, задания, доступ к пве"""
    d = (desc or "").replace("\u2019", "'")
    out = []
    m = re.search(r"(?:^|[-•:]\s*)([\d,\.]+)\s*V-Bucks(?!\s*as you)", d)
    if m and not re.search(r"up to\s*" + re.escape(m.group(1)), d):
        out.append(f"{m.group(1).replace(',', '').replace('.', '')} В-баксов сразу")
    m = re.search(r"(?:earn|unlock)\s+up to\s+([\d,\.]+)\s*V-Bucks", d, re.I)
    if m:
        out.append(f"задания на {m.group(1).replace(',', '').replace('.', '')} В-баксов")
    if re.search(r"access to\s*[\"“«]?\s*save the world", d, re.I):
        out.append("доступ к «Сражению с Бурей»")
    return out


XB_DEAD = {"n": 0}
XB_START = []


def xb_enrich(p) -> None:
    # ограничение по времени и если xbox не отвечает — дальше не пытаемся, сборка не должна висеть
    if XB_DEAD["n"] >= 3 or time.time() - XB_START[0] > XB_BUDGET:
        return
    if p.get("active") is False and not p.get("xbox"):
        return   # архивные без id не ищем — только те, что вписаны вручную
    try:
        pid = p.get("xbox") or xb_find(p)
    except Exception:
        XB_DEAD["n"] += 1
        return
    if not pid:
        return
    p["xbox"] = pid
    try:
        en = xb_product(pid, "en-US")
        ru = xb_product(pid, "ru-RU")
    except Exception as e:
        print(f"  xbox {pid}: {type(e).__name__}")
        XB_DEAD["n"] += 1
        return
    imgs = {i.get("ImagePurpose"): i for i in (en.get("Images") or []) if i.get("Uri")}
    art = imgs.get("BoxArt") or imgs.get("FeaturePromotionalSquareArt") or imgs.get("Poster")
    if art:
        dst = IMG / "packs" / f"{pid}.webp"
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image
            req = urllib.request.Request("https:" + art["Uri"] + "?w=720&h=720", headers={"User-Agent": "StormRadarBot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                im = Image.open(io.BytesIO(r.read())).convert("RGB")
            w, h = im.size
            if w != h:  # квадрат по центру
                s = min(w, h)
                im = im.crop(((w - s) // 2, 0, (w - s) // 2 + s, s))
            im.resize((600, 600), Image.LANCZOS).save(dst, "WEBP", quality=88, method=6)
            p["img"] = local_or_remote("", dst)
            p["c"] = accent(im)
        except Exception as e:
            print(f"  обложка {pid}: {type(e).__name__}")
    if not p.get("items"):
        p["items"] = xb_items(en.get("ProductDescription") or "")
    for x in xb_perks(en.get("ProductDescription") or ""):
        if x not in p.setdefault("perks", []):
            p["perks"].append(x)
    m = re.search(r"«([^»]+)»", ru.get("ProductTitle") or "")
    if m and (not p.get("name") or p.get("name") == p.get("en")):
        p["name"] = m.group(1)   # русское название из xbox
    note = (ru.get("ShortDescription") or "").strip()
    if note:
        p["note"] = note


# ---------- наборы за реальные деньги (packs.json в корне репо, ведётся вручную)
PACK_TAIL = re.compile(r"\s*\b(starter quest pack|starter pack|quest pack|challenge pack|pack|bundle)$", re.I)


def build_packs(cat: list) -> None:
    try:
        packs = json.loads(Path("packs.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"packs.json не прочитан: {e}")
        packs = []
    by_id = {c["id"].lower(): c for c in cat}
    def short(c):
        return {"id": c["id"], "n": c["n"], "i": c["i"], "t": c["t"], "c1": c["c1"], "c2": c["c2"]}

    found = 0
    for p in packs:
        if not XB_START:
            XB_START.append(time.time())
        xb_enrich(p)
        if not p.get("img") and p.get("img0"):
            p["img"] = p["img0"]   # запасная обложка
            try:
                import base64
                from PIL import Image
                p["c"] = accent(Image.open(io.BytesIO(base64.b64decode(p["img0"].split(",", 1)[1]))))
            except Exception:
                pass
        p.pop("img0", None)
        full = []
        for name in p.get("items") or []:
            hit = None
            try:
                q = urllib.parse.urlencode({"name": name, "language": "ru", "searchLanguage": "en", "matchMethod": "full"})
                hit = get(f"{API}/cosmetics/br/search?{q}").get("data")
            except Exception:
                pass
            if hit and hit["id"].lower() in by_id:
                full.append(short(by_id[hit["id"].lower()]))
        seen = set()
        p["full"] = [x for x in full if not (x["id"] in seen or seen.add(x["id"]))]
        found += bool(full)
        if not p.get("name"):
            p["name"] = p.get("en", "")
    (OUT / "packs.json").write_text(json.dumps(packs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"наборы: {len(packs)}, с составом: {found}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # старые картинки магазина не копим — только сегодняшние
    for f in (IMG / "shop").glob("*.webp"):
        f.unlink()
    shop = build_shop()
    (OUT / "shop.json").write_text(json.dumps(shop, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"магазин: {len(shop['items'])} предметов за {shop['date']}")
    cat = build_catalog()
    (OUT / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"каталог: {len(cat)} предметов")
    build_packs(cat)
    vids = build_videos(shop, cat)
    (OUT / "videos.json").write_text(json.dumps(vids, separators=(",", ":")), encoding="utf-8")
    if FAILS:
        print(f"не скачалось картинок: {len(FAILS)}, первые:")
        for f in FAILS[:10]:
            print("  ", f)


if __name__ == "__main__":
    main()
