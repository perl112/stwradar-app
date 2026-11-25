"""собирает лёгкие файлы для миниаппа: data/shop.json (сегодняшний магазин) и data/catalog.json (все предметы).
запускается гитхабом каждый день, без секретов."""
import json
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

API = "https://fortnite-api.com/v2"
OUT = Path("data")
KNOWN = {"outfit", "emote", "pickaxe", "glider", "backpack", "wrap", "shoe", "contrail", "music", "loadingscreen",
         "spray", "emoji", "pet", "petcarrier", "toy"}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "stwradar-app"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def day(s: str) -> date:
    return date.fromisoformat(s[:10])


def col(h: str | None) -> str:
    h = (h or "").lstrip("#")
    return "#" + h[:6] if len(h) >= 6 else "#3b5f80"


def kind_of(t: str) -> str:
    return t if t in KNOWN else "other"


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
            "rarity": (first.get("rarity") or {}).get("displayValue", ""),
            "set": (first.get("set") or {}).get("text", ""),
            "price": e.get("finalPrice"), "regular": e.get("regularPrice"),
            "out": e.get("outDate"),
            "img": (nda[0]["image"] if nda else None) or imgs.get("featured") or imgs.get("icon") or "",
            "c1": col((e.get("colors") or {}).get("color1")),
            "c2": col((e.get("colors") or {}).get("color3") or (e.get("colors") or {}).get("color2")),
            "isNew": is_new, "days": days, "returned": (not is_new) and days is not None and days >= 2,
            "times": len(prior), "firstSeen": prior[0].isoformat() if prior else None,
            "lastSeen": last.isoformat() if last else None,
            "video": next((b.get("showcaseVideo") for b in br if b.get("showcaseVideo")), None),
            "parts": [{"id": b["id"], "name": b.get("name"), "img": (b.get("images") or {}).get("smallIcon")}
                      for b in br] if e.get("bundle") else [],
            "keys": [b["id"] for b in br],
            "order": (e.get("layout") or {}).get("index", 0),
        })
    return {"date": today.isoformat(), "vbuck": d.get("vbuckIcon"), "items": items}


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
            "r": (c.get("rarity") or {}).get("displayValue", ""),
            "s": (c.get("set") or {}).get("text", ""),
            "d": c.get("description") or "",
            "i": icon, "b": imgs.get("featured") or imgs.get("icon") or icon,
            "v": c.get("showcaseVideo"),
            "h": hist[-1] if hist else None, "c": len(hist), "f": hist[0] if hist else None,
            "a": (c.get("added") or "")[:10],
        })
    # сначала то, что недавно было в магазине, потом остальное по дате добавления
    out.sort(key=lambda x: (x["h"] or "", x["a"]), reverse=True)
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    shop = build_shop()
    (OUT / "shop.json").write_text(json.dumps(shop, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"магазин: {len(shop['items'])} предметов за {shop['date']}")
    cat = build_catalog()
    (OUT / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"каталог: {len(cat)} предметов")


if __name__ == "__main__":
    main()
