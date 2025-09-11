import os, json, time, html, re
import requests
import feedparser
from datetime import datetime, timezone

# -------- Settings from env --------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
SHEET_ID           = os.getenv("SHEETS_SPREADSHEET_ID")
SHEET_TAB          = os.getenv("SHEETS_TAB", "leads")
KEYWORDS_RAW       = os.getenv("KEYWORDS", "iphone,ai,saas")
DAILY_LIMIT        = int(os.getenv("DAILY_LIMIT", "900"))
TOP_N_TELEGRAM     = int(os.getenv("TOP_N_TELEGRAM", "20"))
ACTIVE_PLATFORMS   = [s.strip().lower() for s in os.getenv("ACTIVE_PLATFORMS", "hackernews,producthunt,reddit,youtube,instagram,tiktok").split(",") if s.strip()]
RSSHUB_BASE        = os.getenv("RSSHUB_BASE", "https://rsshub.app").rstrip("/")

GCP_SA_PATH        = os.getenv("GCP_SA_PATH", "gcp_sa.json")

KEYWORDS = [k.strip() for k in re.split(r"[,\n;]", KEYWORDS_RAW) if k.strip()]
UTC_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")


# -------- Helpers --------
def tg_send(msg: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
        }
        requests.post(url, data=payload, timeout=20)
    except Exception:
        pass


def fetch_rss(url: str):
    try:
        d = feedparser.parse(url)
        return d.entries or []
    except Exception:
        return []


def norm_item(platform, topic, title, url, author="", source=""):
    return {
        "platform": platform,
        "topic": topic,
        "title": title.strip() if title else "",
        "url": url.strip() if url else "",
        "username": author.strip() if author else "",
        "source": source.strip() if source else "",
        "ts": datetime.utcnow().isoformat()
    }


# -------- Platform fetchers (RSSHub əsas) --------
def fetch_hackernews(topic):
    # HackerNews – search RSSHub
    url = f"{RSSHUB_BASE}/hackernews/search/{requests.utils.quote(topic)}"
    items = []
    for e in fetch_rss(url)[:50]:
        title = e.get("title", "")
        link  = e.get("link", "")
        items.append(norm_item("hackernews", topic, title, link, source="hn"))
    return items

def fetch_producthunt(topic):
    # Product Hunt – search
    url = f"{RSSHUB_BASE}/producthunt/search/{requests.utils.quote(topic)}"
    items = []
    for e in fetch_rss(url)[:50]:
        title = e.get("title", "")
        link  = e.get("link", "")
        items.append(norm_item("producthunt", topic, title, link, source="ph"))
    return items

def fetch_reddit(topic):
    # Reddit search
    url = f"{RSSHUB_BASE}/reddit/search/{requests.utils.quote(topic)}"
    items = []
    for e in fetch_rss(url)[:80]:
        title = e.get("title", "")
        link  = e.get("link", "")
        author = (e.get("author") or "").replace("/u/", "").replace("u/", "")
        items.append(norm_item("reddit", topic, title, link, author, "reddit"))
    return items

def fetch_youtube(topic):
    # YouTube search
    url = f"{RSSHUB_BASE}/youtube/search/{requests.utils.quote(topic)}"
    items = []
    for e in fetch_rss(url)[:80]:
        title = e.get("title", "")
        link  = e.get("link", "")
        author = e.get("author", "")
        items.append(norm_item("youtube", topic, title, link, author, "yt"))
    return items

def fetch_instagram(topic):
    # Instagram hashtag
    url = f"{RSSHUB_BASE}/instagram/tag/{requests.utils.quote(topic)}"
    items = []
    for e in fetch_rss(url)[:80]:
        title = e.get("title", "")
        link  = e.get("link", "")
        author = e.get("author", "")
        items.append(norm_item("instagram", topic, title, link, author, "ig"))
    return items

def fetch_tiktok(topic):
    # TikTok search/hashtag (RSSHub)
    url = f"{RSSHUB_BASE}/tiktok/search/{requests.utils.quote(topic)}"
    items = []
    for e in fetch_rss(url)[:80]:
        title = e.get("title", "")
        link  = e.get("link", "")
        author = e.get("author", "")
        items.append(norm_item("tiktok", topic, title, link, author, "tt"))
    return items


FETCHERS = {
    "hackernews": fetch_hackernews,
    "producthunt": fetch_producthunt,
    "reddit": fetch_reddit,
    "youtube": fetch_youtube,
    "instagram": fetch_instagram,
    "tiktok": fetch_tiktok,
}


# -------- Google Sheets --------
def gsheets_client():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        GCP_SA_PATH,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds).spreadsheets()


def sheet_get_existing_urls(svc):
    # URL sütunu: E (1-based: column 5) -> range "leads!E2:E"
    rng = f"{SHEET_TAB}!E2:E"
    res = svc.values().get(spreadsheetId=SHEET_ID, range=rng).execute()
    vals = res.get("values", [])
    return set(v[0] for v in vals if v)


def sheet_init_headers_if_empty(svc):
    rng = f"{SHEET_TAB}!A1:H1"
    res = svc.values().get(spreadsheetId=SHEET_ID, range=rng).execute()
    if not res.get("values"):
        headers = [["platform","topic","username","profile_url","title","url","source","ts"]]
        svc.values().update(
            spreadsheetId=SHEET_ID, range=rng, valueInputOption="RAW",
            body={"values": headers}).execute()


def sheet_append_rows(svc, rows):
    if not rows:
        return
    rng = f"{SHEET_TAB}!A2"
    svc.values().append(
        spreadsheetId=SHEET_ID, range=rng, valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": rows}).execute()


# -------- Pipeline --------
def run_pipeline():
    tg_send(f"✅ GitHub Actions bot başladı. Axtarışa keçirəm…\n<b>Zaman:</b> {UTC_NOW}\n<b>Aktiv:</b> {', '.join(ACTIVE_PLATFORMS)}\n<b>Mövzular:</b> {', '.join(KEYWORDS)}")

    # 1) Topla
    collected = []
    for topic in KEYWORDS:
        for platform in ACTIVE_PLATFORMS:
            fn = FETCHERS.get(platform)
            if not fn:
                continue
            try:
                items = fn(topic)
                collected.extend(items)
            except Exception as e:
                tg_send(f"⚠️ {platform} üçün xəta: {html.escape(str(e))}")

    # 2) Limit & dedup (URL üzrə)
    # – Sheets-də olan URL-ləri oxu
    svc = gsheets_client()
    sheet_init_headers_if_empty(svc)
    existing = sheet_get_existing_urls(svc)

    unique = []
    seen = set(existing)
    for it in collected:
        url = it["url"]
        if not url or url in seen:
            continue
        unique.append(it)
        seen.add(url)
        if len(unique) >= DAILY_LIMIT:
            break

    # 3) Sheets-ə yaz
    rows = []
    for it in unique:
        rows.append([
            it["platform"],
            it["topic"],
            it.get("username",""),
            "",  # profile_url (opsional)
            it["title"],
            it["url"],
            it["source"],
            it["ts"],
        ])
    sheet_append_rows(svc, rows)

    # 4) Telegram-a TOP N
    top = unique[:TOP_N_TELEGRAM]
    if top and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        parts = []
        for it in top:
            t = html.escape(it["title"])
            u = it["url"]
            parts.append(f"• <b>{t}</b>\n🔗 {u}\n#{it['platform']} #{it['topic']}")
        tg_send("\n\n".join(parts))

    tg_send(f"🏁 GitHub Actions bot bitdi.\nToplandı: <b>{len(unique)}</b> (Sheets-ə yazıldı).")


if __name__ == "__main__":
    run_pipeline()
