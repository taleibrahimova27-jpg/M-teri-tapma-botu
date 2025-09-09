import os, csv, io, time
from datetime import datetime, timezone
import feedparser
import requests
import json

# -------- Helpers --------
def env(name, default=""):
    v = os.getenv(name, default)
    return v.strip() if isinstance(v, str) else v

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def split_keywords(s):
    # "ayaqqabı/paltar/phone" -> ["ayaqqabı","paltar","phone"]
    return [k.strip() for k in s.split("/") if k.strip()]

def contains_kw(title, kws):
    lt = (title or "").lower()
    for k in kws:
        if k.lower() in lt:
            return True
    return False

def fetch_rss(url, platform, kws, max_items=200):
    try:
        d = feedparser.parse(url)
        items = []
        for e in d.entries[:max_items]:
            title = getattr(e, "title", "")
            link  = getattr(e, "link", "")
            summ  = getattr(e, "summary", "")
            pub   = getattr(e, "published", "") or getattr(e, "updated", "")
            ts    = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            if kws and not contains_kw(f"{title} {summ}", kws):
                continue
            items.append({
                "platform": platform,
                "title": title,
                "url": link,
                "summary": summ,
                "published": pub,
                "timestamp": time.mktime(ts) if ts else 0,
            })
        print(f"{platform}: {len(items)} nəticə toplandı.")
        return items
    except Exception as e:
        print(f"{platform}: RSS alınmadı -> {e}")
        return []

def tg_send(token, chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        ok = r.ok and r.json().get("ok", False)
        if not ok:
            print("Telegram cavabı:", r.text)
        return ok
    except Exception as e:
        print("Telegram xətası:", e)
        return False

def write_csv(path, rows):
    header = ["platform", "title", "url", "summary", "published", "collected_at"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r["platform"], r["title"], r["url"], r["summary"], r["published"], now_iso()])
    print(f"CSV yazıldı: {path} ({len(rows)} sətir)")

def write_google_sheets(rows):
    sa_json = env("SHEETS_JSON")
    ssid    = env("SHEETS_SPREADSHEET_ID")
    wsname  = env("SHEETS_WORKSHEET", "Sheet1")
    if not (sa_json and ssid):
        print("Sheets açarları yoxdur, CSV ilə kifayətlənirəm.")
        return False
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(ssid)
        try:
            ws = sh.worksheet(wsname)
        except Exception:
            ws = sh.add_worksheet(title=wsname, rows="1000", cols="6")

        header = ["platform","title","url","summary","published","collected_at"]
        if ws.row_count == 0 or (ws.cell(1,1).value or "").strip() != "platform":
            ws.resize(1, 6)
            ws.update("A1:F1", [header])

        values = []
        for r in rows:
            values.append([
                r["platform"], r["title"], r["url"], r["summary"], r["published"], now_iso()
            ])

        # limit 900 sətirlik toplu yazı
        values = values[:900]
        if values:
            ws.append_rows(values, value_input_option="RAW")
        print(f"Sheets yazıldı: {len(values)} sətir.")
        return True
    except Exception as e:
        print("Sheets xətası:", e)
        return False

# -------- Config --------
TOKEN   = env("TELEGRAM_BOT_TOKEN")
CHAT_ID = env("TELEGRAM_CHAT_ID")
KW_RAW  = env("KEYWORDS", "")
PLAT_RAW= env("ACTIVE_PLATFORMS", "reddit,youtube,hackernews")
DAILY_LIMIT = int(env("DAILY_LIMIT", "200"))
RSSHUB = env("RSSHUB_BASE", "https://rsshub.app")

KEYWORDS = split_keywords(KW_RAW)
PLATFORMS = [p.strip().lower() for p in PLAT_RAW.split(",") if p.strip()]

print(f"ENV check: TOK={'OK' if TOKEN else 'MISSING'} CID={'OK' if CHAT_ID else 'MISSING'}")
print(f"Platforms={PLATFORMS} | KW={KEYWORDS} | LIMIT={DAILY_LIMIT}")

# -------- Platform fetchers (RSS-only) --------
def fetch_reddit():
    items = []
    for kw in KEYWORDS or ["news"]:
        url = f"https://www.reddit.com/search.rss?q={requests.utils.quote(kw)}&sort=new"
        items += fetch_rss(url, "reddit", KEYWORDS, max_items=DAILY_LIMIT)
    return items

def fetch_youtube():
    items = []
    for kw in KEYWORDS or ["news"]:
        url = f"https://www.youtube.com/feeds/videos.xml?search_query={requests.utils.quote(kw)}"
        items += fetch_rss(url, "youtube", KEYWORDS, max_items=DAILY_LIMIT)
    return items

def fetch_hackernews():
    items = []
    for kw in KEYWORDS or ["news"]:
        url = f"https://hnrss.org/newest?q={requests.utils.quote(kw)}"
        items += fetch_rss(url, "hackernews", KEYWORDS, max_items=DAILY_LIMIT)
    return items

def fetch_producthunt():
    # RSSHub: producthunt/today
    url = f"{RSSHUB}/producthunt/today"
    return fetch_rss(url, "producthunt", KEYWORDS, max_items=DAILY_LIMIT)

def fetch_instagram():
    # RSSHub tələb edir: user adı lazımdır -> SECRET INSTAGRAM_USERS="user1,user2"
    users = [u.strip() for u in env("INSTAGRAM_USERS","").split(",") if u.strip()]
    if not users:
        print("instagram: istifadəçi siyahısı (INSTAGRAM_USERS) yoxdur, atlanır.")
        return []
    items = []
    for u in users:
        url = f"{RSSHUB}/instagram/user/{u}"
        items += fetch_rss(url, "instagram", KEYWORDS, max_items=DAILY_LIMIT)
    return items

def fetch_tiktok():
    users = [u.strip() for u in env("TIKTOK_USERS","").split(",") if u.strip()]
    if not users:
        print("tiktok: istifadəçi siyahısı (TIKTOK_USERS) yoxdur, atlanır.")
        return []
    items = []
    for u in users:
        url = f"{RSSHUB}/tiktok/user/{u}"
        items += fetch_rss(url, "tiktok", KEYWORDS, max_items=DAILY_LIMIT)
    return items

def fetch_threads():
    users = [u.strip() for u in env("THREADS_USERS","").split(",") if u.strip()]
    if not users:
        print("threads: istifadəçi siyahısı (THREADS_USERS) yoxdur, atlanır.")
        return []
    items = []
    for u in users:
        url = f"{RSSHUB}/threads/user/{u}"
        items += fetch_rss(url, "threads", KEYWORDS, max_items=DAILY_LIMIT)
    return items

FETCHERS = {
    "reddit": fetch_reddit,
    "youtube": fetch_youtube,
    "hackernews": fetch_hackernews,
    "producthunt": fetch_producthunt,
    "instagram": fetch_instagram,
    "tiktok": fetch_tiktok,
    "threads": fetch_threads,
}

def main():
    all_items = []
    for p in PLATFORMS:
        fn = FETCHERS.get(p)
        if fn:
            all_items += fn()
        else:
            print(f"{p}: dəstəklənmir, atlanır.")

    # tarixə görə azalan sırala
    all_items.sort(key=lambda r: r.get("timestamp", 0), reverse=True)

    # Telegram-a TOP 20
    top20 = all_items[:20]
    if TOKEN and CHAT_ID and top20:
        lines = ["🧾 TOP 20 tapıntı:"]
        for i, r in enumerate(top20, 1):
            title = (r["title"] or "")[:120]
            lines.append(f"{i}. [{r['platform']}] {title}\n{r['url']}")
        msg = "\n\n".join(lines)
        tg_send(TOKEN, CHAT_ID, msg)
    elif not top20:
        print("Uyğun nəticə yoxdur, Telegrama göndərmirəm.")
    else:
        print("Telegram token/chat_id yoxdur, mesajı ötürürəm.")

    # Sheets və ya CSV (max 900 sətir)
    bulk = all_items[:900]
    wrote_sheets = write_google_sheets(bulk)
    if not wrote_sheets:
        write_csv("results.csv", bulk)

if __name__ == "__main__":
    main()
