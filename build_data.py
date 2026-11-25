"""собирает лёгкие файлы для миниаппа: data/shop.json (сегодняшний магазин) и data/catalog.json (все предметы).
запускается гитхабом каждый день, без секретов."""
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

API = "https://fortnite-api.com/v2"
OUT = Path("site/data")
IMG = Path("site/img")
SHOP_PX, CAT_PX = 512, 320
CAT_PER_RUN = 3000   # сколько новых иконок каталога качать за один запуск   # размеры картинок для витрины и каталога
KNOWN = {"outfit", "emote", "pickaxe", "glider", "backpack", "wrap", "shoe", "contrail", "music", "loadingscreen",
         "spray", "emoji", "pet", "petcarrier", "toy"}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "stwradar-app"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


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
    from PIL import Image
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                im = Image.open(io.BytesIO(r.read())).convert("RGBA")
            im.thumbnail((px, px), Image.LANCZOS)
            path.parent.mkdir(parents=True, exist_ok=True)
            im.save(path, "WEBP", quality=86, method=6)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(float(e.headers.get("Retry-After") or 2 * (attempt + 1)))
                continue
            FAILS.append(f"{e.code} {url}")
            return False
        except Exception as e:
            if attempt < 3:
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


def build_shop() -> dict:
    d = get(f"{API}/shop?language=ru&responseFlags=4")["data"]
    today = day(d["date"])
    now = datetime.now(timezone.utc)
    items, seen = [], set()
    for e in d.get("entries") or []:
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
        t = "bundle" if e.get("bundle") else (first.get("type") or {}).get("value", "other")
        nda = (e.get("newDisplayAsset") or {}).get("renderImages") or []
        imgs = first.get("images") or {}
        items.append({
            "id": e["offerId"],
            "name": (e.get("bundle") or {}).get("name") or first.get("name"),
            "kind": t if t == "bundle" else kind_of(t),
            "typeName": "Набор" if e.get("bundle") else (first.get("type") or {}).get("displayValue", ""),
            "desc": first.get("description") or "",
            "rarity": (first.get("series") or {}).get("value") or (first.get("rarity") or {}).get("displayValue", ""),
            "set": (first.get("set") or {}).get("text", ""),
            "rb": rarity_colors(first)[2],
            "src": sources({"gameplayTags": [t for b in br for t in (b.get("gameplayTags") or [])],
                            "shopHistory": first.get("shopHistory")}),
            "intro": intro(first),
            "price": e.get("finalPrice"), "regular": e.get("regularPrice"),
            "out": e.get("outDate"),
            "img": (nda[0]["image"] if nda else None) or imgs.get("featured") or imgs.get("icon") or "",
            "c1": col((e.get("colors") or {}).get("color1")),
            "c2": col((e.get("colors") or {}).get("color3") or (e.get("colors") or {}).get("color2")),
            "isNew": is_new, "days": days, "returned": (not is_new) and days is not None and days >= 2,
            "times": len(prior), "firstSeen": prior[0].isoformat() if prior else None,
            "lastSeen": last.isoformat() if last else None,
            "video": next((b.get("showcaseVideo") for b in br if b.get("showcaseVideo")), None),
            "parts": [{"id": b.get("id", ""), "name": b.get("name"), "img": (b.get("images") or {}).get("smallIcon")}
                      for b in br] if e.get("bundle") else [],
            "keys": [b.get("id", "") for b in br],
            "order": (e.get("layout") or {}).get("index", 0),
        })
    # зеркалим картинки магазина (каждый день новые) и иконку в-баксов
    jobs, links = [], []
    for it in items:
        if it["img"]:
            p = IMG / "shop" / f"{safe(it['id'])}.webp"
            jobs.append((it["img"], p, SHOP_PX)); links.append((it, "img", it["img"], p))
        for part in it["parts"]:
            if part.get("img"):
                p = IMG / "c2" / f"{safe(part['id'])}.webp"
                jobs.append((part["img"], p, CAT_PX)); links.append((part, "img", part["img"], p))
    vb = d.get("vbuckIcon") or ""
    vbp = IMG / "vbuck.webp"
    if vb:
        jobs.append((vb, vbp, 64))
    mirror(jobs)
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
        icon = imgs.get("smallIcon") or imgs.get("icon")
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
        p = IMG / "c2" / f"{safe(x['id'])}.webp"
        if not p.exists() and len(jobs) < CAT_PER_RUN:
            jobs.append((x["i"], p, CAT_PX))
    mirror(jobs)
    have = 0
    for x in out:
        p = IMG / "c2" / f"{safe(x['id'])}.webp"
        x["i"] = x["b"] = local_or_remote(x["i"], p)
        have += p.exists()
    print(f"иконки каталога: {have} из {len(out)} уже свои")
    return out


# ---------- номера предметов на fortnite.gg (для видео с их водяным знаком)
# как у FNPacks: по одному запросу на предмет, только для сегодняшнего магазина, найденное запоминаем навсегда
GG_MAP = Path("ggmap/ggmap.json")          # хранится в кэше гитхаба между запусками
GG_UA = "StormRadarBot/1.0 (+https://t.me/stwradar)"
GG_PER_RUN = 400


def gg_lookup(cid: str):
    req = urllib.request.Request("https://fortnite.gg/api/item?id=" + urllib.parse.quote(cid),
                                 headers={"User-Agent": GG_UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return ("blocked", e.code)
    except Exception as e:
        return ("error", type(e).__name__)
    import re
    try:
        o = json.loads(text)
        for c in (o.get("id") if isinstance(o, dict) else None,
                  (o.get("item") or {}).get("id") if isinstance(o, dict) else None,
                  (o.get("data") or {}).get("id") if isinstance(o, dict) else None):
            if c is not None and str(c).isdigit():
                return ("ok", int(c))
    except Exception:
        pass
    m = re.search(r"items/(\d{1,7})/", text) or re.search(r'"id"\s*:\s*"?(\d{1,7})', text)
    return ("ok", int(m.group(1))) if m else ("none", None)


def build_videos(shop: dict) -> dict:
    try:
        mp = json.loads(GG_MAP.read_text(encoding="utf-8"))
    except Exception:
        mp = {}
    ids = []
    for it in shop["items"]:
        for k in it["keys"]:
            if k and k not in mp and k not in ids:
                ids.append(k)
    blocked = checked = 0
    for cid in ids[:GG_PER_RUN]:
        status, val = gg_lookup(cid)
        checked += 1
        if status == "ok":
            mp[cid] = val
        elif status == "none":
            mp[cid] = 0           # у fortnite.gg нет такого предмета — больше не спрашиваем
        elif status == "blocked":
            blocked += 1
            print(f"fortnite.gg не пустил запрос ({val}) — останавливаюсь, не долбим их сервер")
            break
        time.sleep(1)             # по одному запросу в секунду
    GG_MAP.parent.mkdir(parents=True, exist_ok=True)
    GG_MAP.write_text(json.dumps(mp), encoding="utf-8")
    videos = {k: v for k, v in mp.items() if v}
    print(f"номера fortnite.gg: всего {len(videos)}, проверено сейчас {checked}, блокировок {blocked}")
    return videos


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # старые картинки магазина не копим — только сегодняшние
    for f in (IMG / "shop").glob("*.webp"):
        f.unlink()
    shop = build_shop()
    (OUT / "shop.json").write_text(json.dumps(shop, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"магазин: {len(shop['items'])} предметов за {shop['date']}")
    try:
        vids = build_videos(shop)
    except Exception as e:
        print(f"видео fortnite.gg: {type(e).__name__}: {e}")
        vids = {}
    (OUT / "videos.json").write_text(json.dumps(vids, separators=(",", ":")), encoding="utf-8")
    # наборы за реальные деньги — ведутся вручную в packs.json в корне репо
    try:
        packs = json.loads(Path("packs.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"packs.json не прочитан: {e}")
        packs = []
    (OUT / "packs.json").write_text(json.dumps(packs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"наборы: {len(packs)}")
    cat = build_catalog()
    (OUT / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"каталог: {len(cat)} предметов")
    if FAILS:
        print(f"не скачалось картинок: {len(FAILS)}, первые:")
        for f in FAILS[:10]:
            print("  ", f)


if __name__ == "__main__":
    main()
