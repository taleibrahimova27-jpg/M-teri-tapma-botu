import os, json, time, datetime
import requests, feedparser
from urllib.parse import quote_plus

# ===== Helpers =====
def log(msg):
    print(msg, flush=True)

def getenv(name, default=""):
    val = os.getenv(name)
    return val if val is not None and str(val).strip() != "" else default

def to_list(csv):
    return [x.strip() for x in csv.split(",") if x.strip()]

TELEGRAM_BOT_TOKEN   = getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID     = getenv("TELEGRAM_CHAT_ID")
ACTIVE_PLATFORMS     = [p.lower() for p in to_list(getenv("ACTIVE_PLATFORMS", "hackernews,reddit,youtube,instagram,tiktok,producthunt"))]
KEYWORDS             = to_list(getenv("KEYWORDS", "ai,tech,startup"))
DAILY_LIMIT          = int(getenv("DAILY_LIMIT", "900"))
TOP_N_TELEGRAM       = int(getenv("TOP_N_TELEGRAM", "20"))
RSSHUB_BASE          = getenv("RSSHUB_BASE", "").rstrip("/")
SPREADSHEET_ID       = getenv("SHEETS_SPREADSHEET_ID")
SHEETS_TAB           = getenv("SHEETS_TAB", "data")
GCP_SA_KEY_RAW       = getenv("GCP_SA_KEY", "")

# ===== Telegram =====
def tg_send(text):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True, "parse_mode": "HTML"}
        r = requests.post(url, data=data, timeout=20)
        return r.ok
    except Exception as e:
        log(f"Telegram error: {e}")
        return False

def tg_notify_start():
    tg_send("✅ GitHub Actions bot başladı. Axtarışa keçirəm…")

def tg_notify_done():
    tg_send("🏁 GitHub Actions bot bitdi.")

# ===== Fetchers (RSS) =====
def fetch_hackernews(keyword, max_items=50):
    url = f"https://hnrss.org/newest?q={quote_plus(keyword)}"
    return parse_rss(url, "hackernews", keyword, max_items)

def fetch_reddit(keyword, max_items=50):
    url = f"https://www.reddit.com/search.rss?q={quote_plus(keyword)}&sort=new"
    headers = {"User-Agent": "Mozilla/5.0 (GitHubAction; +https://github.com/)"}
    return parse_rss(url, "reddit", keyword, max_items, headers=headers)

def fetch_via_rsshub(route, platform, keyword, max_items=50):
    if not RSSHUB_BASE:
        log(f"{platform}: RSSHub BASE yoxdur, atlanır.")
        return []
    url = f"{RSSHUB_BASE}{route}"
    return parse_rss(url, platform, keyword, max_items)

def fetch_youtube(keyword, max_items=50):
    # RSSHub: /youtube/keyword/:keyword
    return fetch_via_rsshub(f"/youtube/keyword/{quote_plus(keyword)}", "youtube", keyword, max_items)

def fetch_instagram(keyword, max_items=50):
    # RSSHub: /instagram/tag/:tag
    return fetch_via_rsshub(f"/instagram/tag/{quote_plus(keyword)}", "instagram", keyword, max_items)

def fetch_tiktok(keyword, max_items=50):
    # RSSHub: /tiktok/keyword/:keyword
    return fetch_via_rsshub(f"/tiktok/keyword/{quote_plus(keyword)}", "tiktok", keyword, max_items)

def fetch_producthunt(keyword, max_items=50):
    # RSSHub: /producthunt/today?search=xxx (bəzi instanslarda /producthunt/topics/:topic da olur)
    route = f"/producthunt/today?search={quote_plus(keyword)}"
    return fetch_via_rsshub(route, "producthunt", keyword, max_items)

def parse_rss(url, platform, keyword, max_items, headers=None):
    try:
        if headers:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
        else:
            feed = feedparser.parse(url)

        items = []
        for e in feed.entries[:max_items]:
            title = e.get("title", "").strip()
            link  = e.get("link", "").strip()
            if not (title and link):
                continue
            items.append({
                "time": datetime.datetime.utcnow().isoformat(timespec="seconds"),
                "platform": platform,
                "keyword": keyword,
                "title": title,
                "url": link
            })
        log(f"{platform}({keyword}): {len(items)} nəticə toplandı.")
        return items
    except Exception as ex:
        log(f"{platform} xətası ({keyword}): {ex}")
        return []

FETCHERS = {
    "hackernews": fetch_hackernews,
    "reddit": fetch_reddit,
    "youtube": fetch_youtube,
    "instagram": fetch_instagram,
    "tiktok": fetch_tiktok,
    "producthunt": fetch_producthunt,
}

# ===== Sheets =====
def sheets_write(rows):
    """
    rows: list of dicts with keys: time, platform, keyword, title, url
    """
    if not (SPREADSHEET_ID and GCP_SA_KEY_RAW):
        log("Sheets config yoxdur, yazma atlanır.")
        return False
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        # Service account JSON mətni secret-dən gəlir:
        info = json.loads(GCP_SA_KEY_RAW)
        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        service = build("sheets", "v4", credentials=creds)
        sheet = service.spreadsheets()

        values = [["time","platform","keyword","title","url"]]
        for r in rows:
            values.append([r["time"], r["platform"], r["keyword"], r["title"], r["url"]])

        body = {"values": values}
        rng = f"{SHEETS_TAB}!A1"
        # İlk sətirdə başlıq yoxdursa, `RAW` yazırıq; varsa append də istifadə edə bilərik:
        # Burada sadə yol: hər run overwrite (istəsən APPEND-ə çevirərik)
        sheet.values().clear(spreadsheetId=SPREADSHEET_ID, range=f"{SHEETS_TAB}!A:Z").execute()
        sheet.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=rng,
            valueInputOption="RAW",
            body=body
        ).execute()
        log(f"Sheets: {len(rows)} sətir yazıldı.")
        return True
    except Exception as e:
        log(f"Sheets xətası: {e}")
        return False

# ===== Main flow =====
def main():
    tg_notify_start()
    all_items = []
    per_keyword_limit = max(5, DAILY_LIMIT // max(1, len(KEYWORDS)))

    for kw in KEYWORDS:
        for platform in ACTIVE_PLATFORMS:
            fn = FETCHERS.get(platform)
            if not fn:
                log(f"{platform}: tanınmır, atlanır.")
                continue
            items = fn(kw, max_items=per_keyword_limit)
            all_items.extend(items)

    # Unikal link-lər olsun
    seen = set()
    uniq = []
    for it in all_items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        uniq.append(it)

    # Sheets-ə hamısını yaz
    sheets_write(uniq)

    # Telegram-a TOP N
    top_n = min(TOP_N_TELEGRAM, len(uniq))
    if top_n > 0 and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        base_msg = []
        for it in uniq[:top_n]:
            line = f"• <b>{escape_html(it['title'])}</b>\n🔗 {it['url']}\n#{it['platform']}"
            base_msg.append(line)
        # Telegram 4096 char limit – ehtiyatla bölək
        send_chunks("\n\n", base_msg)
    elif top_n == 0:
        tg_send("ℹ️ Heç nə tapılmadı (RSS mənbələrində uyğun nəticə yoxdur).")

    tg_notify_done()

def escape_html(s: str) -> str:
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def send_chunks(sep, parts):
    buf = ""
    for p in parts:
        if len(buf) + len(p) + len(sep) > 3800:
            tg_send(buf)
            time.sleep(0.7)
            buf = p
        else:
            buf = p if not buf else buf + sep + p
    if buf:
        tg_send(buf)

if __name__ == "__main__":
    main()
