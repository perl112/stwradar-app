"""постит алерты stw в тг-канал. запускается гитхабом раз в день (см. .github/workflows/post.yml).

нужны переменные окружения (в гитхабе это Secrets):
  BOT_TOKEN         токен бота, бот должен быть админом канала
  CHANNEL_ID        @username канала или -100... для приватного
  EPIC_DEVICE_AUTH  содержимое device_auth.json целиком
необязательные:
  WATERMARK         подпись внизу картинки (по умолчанию = CHANNEL_ID)
  DRY_RUN=1         ничего не отправлять, только сохранить картинки в папку out/
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from alerts import Alert, parse_world_info, vbucks_total
from poster import TOP_N, alert_score, render_rows
from shop import by_category, download_images, fetch_shop, parse_shop, render_sections, shop_is_today
from epic import EpicClient
from rewards import power_level
from story import render_story
from returns import render_returns

VBUCKS = "AccountResource:currency_mtxswap"
ALBUM_MAX = 10          # лимит телеги на альбом
PAUSE_BETWEEN = 45      # сек между альбомами, чтобы не словить лимит канала


def is_fresh(alerts: list[Alert]) -> bool:
    """алерты уже обновились сегодня? (новые живут до следующей полуночи utc)"""
    now = datetime.now(timezone.utc)
    next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return any(a.available_until and abs((a.available_until - next_reset).total_seconds()) < 2 * 3600
               for a in alerts)


async def fetch(epic: EpicClient) -> tuple[list[Alert], dict]:
    # если запустились прямо в момент обновления, ждём, пока эпики отдадут свежие
    for attempt in range(8):
        raw = await epic.world_info()
        alerts = parse_world_info(raw)
        if is_fresh(alerts) or attempt == 7:
            return alerts, raw
        print("алерты ещё старые, жду 3 минуты...")
        await asyncio.sleep(180)
    return alerts, raw


async def tg(session: aiohttp.ClientSession, token: str, method: str, make_data) -> dict:
    """make_data собирает тело запроса заново на каждую попытку"""
    for attempt in range(6):
        async with session.post(f"https://api.telegram.org/bot{token}/{method}", data=make_data()) as r:
            body = await r.json(content_type=None)
        if body.get("ok"):
            return body
        retry = (body.get("parameters") or {}).get("retry_after")
        if body.get("error_code") == 429 and retry and attempt < 5:
            print(f"телега просит подождать {retry} сек, жду...")
            await asyncio.sleep(int(retry) + 2)
            continue
        raise RuntimeError(f"телега ответила ошибкой в {method}: {body}")
    raise RuntimeError(f"телега так и не приняла {method}")


async def send_album(session, token: str, chat: str, images: list[bytes], caption: str) -> None:
    """1 картинка — обычное фото, 2–10 — альбом. подпись на первой"""
    if len(images) == 1:
        def single():
            f = aiohttp.FormData()
            f.add_field("chat_id", chat)
            f.add_field("caption", caption)
            f.add_field("parse_mode", "HTML")
            f.add_field("photo", images[0], filename="alerts.png", content_type="image/png")
            return f
        await tg(session, token, "sendPhoto", single)
        return

    def album():
        f = aiohttp.FormData()
        f.add_field("chat_id", chat)
        media = []
        for i, png in enumerate(images):
            item = {"type": "photo", "media": f"attach://img{i}"}
            if i == 0 and caption:
                item.update(caption=caption, parse_mode="HTML")
            media.append(item)
            f.add_field(f"img{i}", png, filename=f"img{i}.png", content_type="image/png")
        f.add_field("media", json.dumps(media))
        return f
    await tg(session, token, "sendMediaGroup", album)


# премиум-эмодзи: впиши id (цифрами) — бот поставит премиум-версию,
# пусто — останется обычный эмодзи
EMOJI = {
    "shop":   ("🛒", "5431588703933857822"),
    "storm":  ("🌪", "5393264560427782483"),
    "vbucks": ("🤑", "5431747261241505121"),
}


def E(name: str) -> str:
    ch, eid = EMOJI[name]
    return f'<tg-emoji emoji-id="{eid}">{ch}</tg-emoji>' if eid else ch


def _vb(a: Alert) -> int:
    return sum(q for it, q in a.rewards if it == VBUCKS)


def build_posts(alerts: list[Alert], watermark: str) -> list[tuple[list[bytes], str]]:
    """один пост: альбом из 3 картинок — В-баксы, Лучшие награды, Итого по зонам"""
    date = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    vb = sorted([a for a in alerts if _vb(a)], key=lambda a: (-_vb(a), -(power_level(a.difficulty_row) or 0)))
    top_all = sorted([a for a in alerts if a not in vb and alert_score(a) >= 1], key=alert_score, reverse=True)
    top = top_all[:TOP_N]
    total = vbucks_total(alerts)

    images = [
        render_rows(vb, "В-баксы",
                    f"всего {total} В-баксов в {len(vb)} миссиях" if vb else "",
                    watermark, icon_item=VBUCKS, icon_kind="vbucks",
                    empty_text="Сегодня миссий с В-баксами нет"),
        render_rows(top, "Лучшие награды",
                    f"топ-{len(top)} из {len(top_all)}: легендарный улучшарий, осколки бури, "
                    f"легендарные герои, выжившие и чертежи" if top else "",
                    watermark, icon_item="AccountResource:reagent_c_t04", icon_kind="shard",
                    empty_text="Сегодня без топовых наград"),
    ]

    cap = f"{E('storm')} <b>Сражение с Бурей — {date}</b>\n"
    cap += (f"{E('vbucks')} В-баксов в срочных миссиях: <b>{total}</b>" if total
            else f"{E('vbucks')} Срочных миссий с В-баксами сегодня нет")
    return [(images, cap)]


LAST_SHOP: dict = {}


async def send_story(token: str, chat: str, vbucks: int | None, dry: bool) -> bool:
    """вертикальная картинка для сторис — шлём админу в личку, он выкладывает в историю канала"""
    if not LAST_SHOP.get("items"):
        return False
    jpg = render_story(LAST_SHOP["items"], LAST_SHOP.get("vbuck"), vbucks)
    if dry:
        Path("out").mkdir(exist_ok=True)
        Path("out/story.jpg").write_bytes(jpg)
        return True
    date = datetime.now(timezone.utc).strftime("%d.%m")

    def data():
        f = aiohttp.FormData()
        f.add_field("chat_id", chat)
        f.add_field("caption", f"📲 сторис на {date} — сохрани и выложи в историю канала")
        f.add_field("photo", jpg, filename="story.jpg", content_type="image/jpeg")
        return f
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        await tg(session, token, "sendPhoto", data)
    return True


RETURN_MIN_DAYS = int(os.getenv("RETURN_MIN_DAYS", "100"))   # с какого перерыва считаем «давно не было»
APP_LINK = "https://t.me/STWradar_bot?startapp"


async def send_returns(token: str, chat: str, dry: bool) -> bool:
    """отдельный пост, если в магазин вернулось то, чего давно не было"""
    items = LAST_SHOP.get("items") or []
    best, seen = [], set()
    for it in sorted(items, key=lambda i: i.last_days or 0, reverse=True):
        if not it.returned or (it.last_days or 0) < RETURN_MIN_DAYS or it.name in seen or it.image is None:
            continue
        seen.add(it.name)
        best.append(it)
        if len(best) == 6:
            break
    if not best:
        print("давно не было: сегодня ничего")
        return False
    from shop import MSK
    cards = [{"name": it.name, "type": it.type, "rarity": "", "days": it.last_days, "price": it.price,
              "out": it.out.astimezone(MSK).strftime("%d.%m") if it.out else "", "image": it.image,
              "color1": it.color1} for it in best]
    png = render_returns(cards, LAST_SHOP.get("vbuck"))
    if len(best) == 1:
        it = best[0]
        cap = f"<b>{it.name}</b> снова в магазине спустя {it.last_days} дн. · {it.price} В-баксов"
    else:
        cap = "<b>Давно не было в магазине</b>\n" + "\n".join(f"{it.name} — {it.last_days} дн." for it in best)
    if dry:
        Path("out").mkdir(exist_ok=True)
        Path("out/returns.png").write_bytes(png)
        return True
    kb = json.dumps({"inline_keyboard": [[{"text": "Открыть в магазине", "url": APP_LINK}]]})

    def data():
        f = aiohttp.FormData()
        f.add_field("chat_id", chat)
        f.add_field("caption", cap)
        f.add_field("parse_mode", "HTML")
        f.add_field("reply_markup", kb)
        f.add_field("photo", png, filename="returns.png", content_type="image/png")
        return f
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        await tg(session, token, "sendPhoto", data)
    return True


async def build_shop_posts(data: dict, watermark: str) -> list[tuple[list[bytes], str]]:
    """магазин: альбом новинок + альбом остального"""
    items, vb_url = parse_shop(data)
    vbuck = await download_images(items, vb_url)
    LAST_SHOP["items"], LAST_SHOP["vbuck"] = items, vbuck   # для сторис
    new = [i for i in items if i.is_new]
    back = sorted([i for i in items if i.returned and not i.is_new], key=lambda i: -(i.last_days or 0))
    rest = [i for i in items if not i.is_new and not i.returned]
    date = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    posts = []
    head = f"{E('shop')} <b>Магазин Fortnite — {date}</b>"

    def add(imgs, caption):
        for i in range(0, len(imgs), ALBUM_MAX):
            posts.append((imgs[i:i + ALBUM_MAX], caption if i == 0 else ""))

    # подпись только у первого альбома, остальные без текста
    first_caption = head
    for group, title, cols in [
        (by_category(new) if new else [], "Новинки магазина", 4),
        ([("Вернулись после перерыва", back)] if back else [], "Вернулись в магазин", 4),
        (by_category(rest) if rest else [], "Магазин Fortnite", 5),
    ]:
        if group:
            add(render_sections(group, title, vbuck, watermark, cols=cols), first_caption or "")
            first_caption = None
    return posts


STATE = Path("state.json")
ALERTS_DEADLINE_MIN = 2 * 60 + 15   # 02:15 utc (05:15 мск): после этого алерты постим, даже если магазин ещё не вышел


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


async def send_posts(posts, token, chat, dry, prefix) -> None:
    if dry:
        out = Path("out")
        out.mkdir(exist_ok=True)
        for n, (imgs, cap) in enumerate(posts, 1):
            for i, png in enumerate(imgs, 1):
                (out / f"{prefix}{n}_{i}.png").write_bytes(png)
            print(f"--- {prefix} пост {n} ({len(imgs)} картинок)\n{cap}")
        return
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
        for n, (imgs, cap) in enumerate(posts, 1):
            if n > 1:
                await asyncio.sleep(PAUSE_BETWEEN)
            await send_album(session, token, chat, imgs, cap)
            print(f"{prefix}: пост {n}/{len(posts)} отправлен ({len(imgs)} картинок)")


async def wait_for_fresh_shop(st: dict, wait_min: int) -> None:
    """ранний запуск: стартуем до полуночи utc и ждём, пока выйдет новый магазин,
    чтобы запостить сразу, а не ждать опоздавший запуск гитхаба"""
    now = datetime.now(timezone.utc)
    if now.hour == 23:  # ещё вчера — спим до 00:00:20 utc
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=20, microsecond=0)
        secs = (midnight - now).total_seconds()
        print(f"жду полуночи utc: {int(secs)} с")
        await asyncio.sleep(secs)
    if st.get("shop") == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        return  # сегодня магазин уже был — ждать нечего
    deadline = datetime.now(timezone.utc) + timedelta(minutes=wait_min)
    while datetime.now(timezone.utc) < deadline:
        try:
            data = await fetch_shop()
            h = (data.get("data") or {}).get("hash")
            if shop_is_today(data) and h != st.get("shop_hash"):
                print("магазин обновился — постим")
                return
        except Exception as e:
            print(f"проверка магазина: {type(e).__name__}: {e}")
        await asyncio.sleep(30)
    print("магазин так и не обновился за время ожидания — дальше подхватят обычные запуски")


async def main() -> None:
    """запускается несколько раз за ночь. каждый раз постит только то, что ещё не постил сегодня и что уже обновилось.
    FORCE=1 (ручной запуск) — постит всё сразу, не глядя на память"""
    token = os.getenv("BOT_TOKEN", "")
    chat = os.getenv("CHANNEL_ID", "")
    raw_auth = os.getenv("EPIC_DEVICE_AUTH", "")
    dry = os.getenv("DRY_RUN") == "1"
    force = os.getenv("FORCE") == "1"
    if not raw_auth or (not dry and (not token or not chat)):
        sys.exit("не хватает секретов: BOT_TOKEN, CHANNEL_ID, EPIC_DEVICE_AUTH")

    st = load_state()
    wait_min = int(os.getenv("WAIT_MIN", "0") or 0)
    if wait_min and not force:
        await wait_for_fresh_shop(st, wait_min)

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    watermark = os.getenv("WATERMARK") or (chat if chat.startswith("@") else "")
    sent_something = False
    story_vbucks = None

    # 1) магазин
    if os.getenv("SKIP_SHOP") != "1" and (force or st.get("shop") != today):
        try:
            data = await fetch_shop()
            Path("shop.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            h = (data.get("data") or {}).get("hash")
            fresh = shop_is_today(data) and (force or h != st.get("shop_hash"))
            if fresh:
                posts = await build_shop_posts(data, watermark)
                await send_posts(posts, token, chat, dry, "shop")
                st.update(shop=today, shop_hash=h)
                save_state(st)
                sent_something = True
            else:
                print("магазин ещё не обновился (идёт обнова или fortnite-api не успел) — попробую в следующий запуск")
        except Exception as e:  # магазин не должен ломать алерты
            print(f"магазин не получился: {type(e).__name__}: {e}")

    # 2) алерты: после магазина, или когда магазин слишком задерживается
    minutes = now.hour * 60 + now.minute
    alerts_turn = force or st.get("shop") == today or minutes >= ALERTS_DEADLINE_MIN or os.getenv("SKIP_SHOP") == "1"
    if (force or st.get("alerts") != today) and alerts_turn:
        raw = await EpicClient(device_auth=json.loads(raw_auth)).world_info()
        Path("world_info.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        alerts = parse_world_info(raw)
        print(f"алертов всего: {len(alerts)}")
        if force or is_fresh(alerts):
            if sent_something and not dry:
                await asyncio.sleep(PAUSE_BETWEEN)
            await send_posts(build_posts(alerts, watermark), token, chat, dry, "alerts")
            st["alerts"] = today
            save_state(st)
            story_vbucks = vbucks_total(alerts)
        else:
            print("алерты ещё вчерашние — попробую в следующий запуск")
    elif st.get("alerts") == today:
        print("алерты сегодня уже были")
    else:
        print("жду магазин, алерты выйдут после него")
    # 2.5) «давно не было» — после выпуска, один раз в день
    if LAST_SHOP.get("items") and (force or st.get("returns") != today):
        try:
            if not dry:
                await asyncio.sleep(PAUSE_BETWEEN)
            await send_returns(token, chat, dry)
            st["returns"] = today
            save_state(st)
        except Exception as e:
            print(f"пост «давно не было» не получился: {type(e).__name__}: {e}")
    # 3) сторис для админа: после магазина, один раз в день
    story_chat = os.getenv("STORY_CHAT_ID", "")
    if story_chat and LAST_SHOP.get("items") and (force or st.get("story") != today):
        try:
            if await send_story(token, story_chat, story_vbucks, dry):
                st["story"] = today
                save_state(st)
                print("сторис отправлена админу")
        except Exception as e:  # сторис не должна ломать выпуск
            print(f"сторис не получилась: {type(e).__name__}: {e}")
    print("готово")


if __name__ == "__main__":
    asyncio.run(main())
