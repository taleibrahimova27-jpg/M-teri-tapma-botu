# main.py
# Multi-platform lead collector -> Sheets + Telegram (TOP N)
import os, time, hashlib, requests, html
from datetime import datetime, timezone
from urllib.parse import quote

# --------- Config from secrets ---------
KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS", "iphone").split(",") if k.strip()]
ACTIVE_PLATFORMS = [p.strip().lower() for p in os.getenv("ACTIVE_PLATFORMS", "hackernews").split(",") if p.strip()]
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "300"))

# Telegram (optional)
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
TOP_N_TELEGRAM = int(os.getenv("TOP_N_TELEGRAM", "20"))

# Sheets
SPREADSHEET_ID = os.getenv("SHEETS_SPREADSHEET_ID", "")
SHEETS_TAB     = os.getenv("SHEETS_TAB", "leads")

# RSSHub
RSSHUB_BASE = os.getenv("RSSHUB_BASE", "https://rsshub.app").rstrip("/")

# GCP SA key path (Actions writes it)
GCP_KEY_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GCP_SA_KEY_PATH", "")

# --------- Helpers ---------
def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z")

def short_uid(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:10]

def tg_send(text: str):
    if not (TG_TOKEN and TG_CHAT): return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True},
            timeout=20
        )
    except Exception:
        pass

def parse_rss_items(xml_text: str):
    """Very simple RSS parser: returns list of dicts with title/link/author if present."""
    items = []
    parts = xml_text.split("<item>")[1:]
    for part in parts:
        try:
            title = part.split("<title>")[1].split("</title>")[0]
        except Exception:
            title = ""
        try:
            link = part.split("<link>")[1].split("</link>")[0]
        except Exception:
            # some feeds use <guid> as link
            try:
                link = part.split("<guid>")[1].split("</guid>")[0]
            except Exception:
                continue
        # author variants
        author = ""
        for tag in ["<author>", "<dc:creator>"]:
            if tag in part:
                try:
                    author = part.split(tag)[1].split(f"</{tag.strip('<>')}>")[0]
                    break
                except Exception:
                    pass
        items.append({"title": html.unescape(title.strip()), "link": link.strip(), "author": html.unescape(author.strip())})
    return items

def to_row(platform, topic, title, link, author=""):
    # Map exactly to your sheet headers:
    # platform, topic, username, profile_url, dm_url, content_url, intent_score, ts, uid
    username = author or ""
    profile_url = ""
    dm_url = ""
    content_url = link
    intent_score = "1"  # placeholder — istəsən sonradan skorlama əlavə edərik
    ts = iso_now()
    uid = short_uid(f"{platform}|{content_url}")
    return {
        "platform": platform,
        "topic": topic,
        "username": username,
        "profile_url": profile_url,
        "dm_url": dm_url,
        "content_url": content_url,
        "intent_score": intent_score,
        "ts": ts,
        "uid": uid,
        "title": title
    }

# --------- Platform fetchers (RSSHub) ---------
def fetch_hackernews(topic):
    url = f"{RSSHUB_BASE}/hackernews/keyword/{quote(topic)}"
    r = requests.get(url, timeout=30); r.raise_for_status()
    return [to_row("hackernews", topic, it["title"], it["link"], it.get("author","")) for it in parse_rss_items(r.text)]

def fetch_reddit(topic):
    # Reddit search via RSSHub
    url = f"{RSSHUB_BASE}/reddit/search/{quote(topic)}"
    r = requests.get(url, timeout=30); r.raise_for_status()
    return [to_row("reddit", topic, it["title"], it["link"], it.get("author","")) for it in parse_rss_items(r.text)]

def fetch_youtube(topic):
    # YouTube search via RSSHub
    url = f"{RSSHUB_BASE}/youtube/search/{quote(topic)}"
    r = requests.get(url, timeout=30); r.raise_for_status()
    return [to_row("youtube", topic, it["title"], it["link"], it.get("author","")) for it in parse_rss_items(r.text)]

def fetch_tiktok(topic):
    # TikTok keyword via RSSHub (public)
    url = f"{RSSHUB_BASE}/tiktok/keyword/{quote(topic)}"
    r = requests.get(url, timeout=30); r.raise_for_status()
    return [to_row("tiktok", topic, it["title"], it["link"], it.get("author","")) for it in parse_rss_items(r.text)]

def fetch_instagram(topic):
    # Instagram tag via RSSHub (public tags)
    url = f"{RSSHUB_BASE}/instagram/tag/{quote(topic)}"
    r = requests.get(url, timeout=30); r.raise_for_status()
    return [to_row("instagram", topic, it["title"], it["link"], it.get("author","")) for it in parse_rss_items(r.text)]

FETCHERS = {
    "hackernews": fetch_hackernews,
    "reddit":     fetch_reddit,
    "youtube":    fetch_youtube,
    "tiktok":     fetch_tiktok,
    "instagram":  fetch_instagram,
}

# --------- Google Sheets client ----------
def get_sheets_service():
    if not (SPREADSHEET_ID and GCP_KEY_PATH):
        return None
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(GCP_KEY_PATH, scopes=scopes)
    return build("sheets", "v4", credentials=creds)

def sheet_append_rows(rows):
    svc = get_sheets_service()
    if not svc or not rows: return
    body = {"values": rows}
    svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEETS_TAB}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

# --------- Main ----------
def main():
    collected = []
    for kw in KEYWORDS:
        for plat in ACTIVE_PLATFORMS:
            fn = FETCHERS.get(plat)
            if not fn: continue
            try:
                items = fn(kw)
                collected.extend(items)
            except Exception as e:
                tg_send(f"ℹ️ {plat} xətası ({kw}): {e}")
        if len(collected) >= DAILY_LIMIT:
            break

    # de-dup by content_url
    seen, unique = set(), []
    for it in collected:
        cu = it["content_url"]
        if cu in seen: continue
        seen.add(cu)
        unique.append(it)
        if len(unique) >= DAILY_LIMIT: break

    # Sheets rows (exact column order)
    rows = [[
        it["platform"],
        it["topic"],
        it["username"],
        it["profile_url"],
        it["dm_url"],
        it["content_url"],
        it["intent_score"],
        it["ts"],
        it["uid"],
    ] for it in unique]

    sheet_append_rows(rows)

    # Telegram TOP N
    if TG_TOKEN and TG_CHAT and TOP_N_TELEGRAM > 0:
        top = unique[:TOP_N_TELEGRAM]
        if top:
            tg_send("✅ GitHub Actions bot başladı. Axtarışa keçdim…")
            for it in top:
                line = f"• {it.get('title','')}\n🔗 {it['content_url']}\n#{it['platform']}"
                tg_send(line)
            tg_send("🏁 Axtarış tamamlandı.")

if __name__ == "__main__":
    main()
