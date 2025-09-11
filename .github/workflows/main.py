import os, json, time, hashlib, datetime, logging, urllib.parse
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger()

# ========= Config =========
KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS","iphone,samsung").split(",") if k.strip()]
ACTIVE_PLATFORMS = [p.strip() for p in os.getenv("ACTIVE_PLATFORMS","hackernews,youtube,reddit").split(",") if p.strip()]
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT","600"))
TOP_N_TELEGRAM = int(os.getenv("TOP_N_TELEGRAM","20"))

# Sheets
SHEETS_SPREADSHEET_ID = os.environ["SHEETS_SPREADSHEET_ID"]
SHEETS_TAB = os.getenv("SHEETS_TAB","leads")
GCP_SA_PATH = os.getenv("GCP_SA_PATH","sa.json")

# Telegram (optional)
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID","")

# API keys (optional but recommended)
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY","").strip()
APIFY_TOKEN = os.getenv("APIFY_TOKEN","").strip()

# ======== Helpers ========
def ts_now():
    return datetime.datetime.utcnow().isoformat()

def uid_from(*parts):
    h = hashlib.sha256(("||".join(parts)).encode()).hexdigest()[:12]
    return h

def telegram_send(text):
    if not TG_TOKEN or not TG_CHAT: 
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True})
    except Exception as e:
        log.warning("TG error: %s", e)

# ========= Google Sheets =========
from google.oauth2 import service_account
from googleapiclient.discovery import build

def get_sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        GCP_SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets","v4", credentials=creds)

def sheet_append_rows(rows):
    if not rows: 
        return 0
    svc = get_sheets_service()
    body = {"values": rows}
    svc.spreadsheets().values().append(
        spreadsheetId=SHEETS_SPREADSHEET_ID,
        range=f"{SHEETS_TAB}!A:Z",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()
    return len(rows)

# ========= Fetchers (No-VPS, No-Login) =========
# 1) HackerNews — rəsmi Algolia API (key tələb etmir)
def fetch_hn(keyword):
    url = "https://hn.algolia.com/api/v1/search"
    params = {"query": keyword, "tags":"story", "hitsPerPage": 50}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("hits", [])
    out = []
    for h in data:
        title = h.get("title","")
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        uid = uid_from("hn", keyword, url or title)
        out.append(["hackernews", keyword, title[:100], url, "", url, 0.80, ts_now(), uid])
    return out

# 2) Reddit — public JSON (user-agent ilə)
def fetch_reddit(keyword):
    headers = {"User-Agent":"Mozilla/5.0 (bot by github actions)"}
    params = {"q": keyword, "limit": 50, "sort":"new", "t":"week"}
    r = requests.get("https://www.reddit.com/search.json", params=params, headers=headers, timeout=25)
    r.raise_for_status()
    data = r.json().get("data",{}).get("children",[])
    out=[]
    for item in data:
        d = item.get("data",{})
        title = d.get("title","")
        author = d.get("author","")
        url = "https://www.reddit.com"+ d.get("permalink","")
        uidv = uid_from("reddit", keyword, url)
        out.append(["reddit", keyword, author, f"https://www.reddit.com/user/{author}", "", url, 0.70, ts_now(), uidv])
    return out

# 3) YouTube — rəsmi Data API (API key lazımdır, GCP-də 1 dəqiqəyə yaradır)
def fetch_youtube(keyword):
    if not YOUTUBE_API_KEY:
        return []
    base = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part":"snippet",
        "q": keyword,
        "type":"video",
        "maxResults": 50,
        "order":"date",
        "key": YOUTUBE_API_KEY
    }
    r = requests.get(base, params=params, timeout=25)
    r.raise_for_status()
    items = r.json().get("items",[])
    out=[]
    for it in items:
        sn = it.get("snippet",{})
        vid = it.get("id",{}).get("videoId")
        if not vid: 
            continue
        title = sn.get("title","")
        channel = sn.get("channelTitle","")
        url = f"https://www.youtube.com/watch?v={vid}"
        uidv = uid_from("youtube", keyword, vid)
        out.append(["youtube", keyword, channel, "", "", url, 0.75, ts_now(), uidv])
    return out

# 4) Instagram — Apify actor (pulsuz kreditlərlə; token verilsə işləyir)
def fetch_instagram_hashtag(keyword):
    if not APIFY_TOKEN:
        return []
    endpoint = (
      f"https://api.apify.com/v2/acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items"
      f"?token={APIFY_TOKEN}"
    )
    payload = {
        "hashtags": [keyword],
        "resultsLimit": 30
    }
    r = requests.post(endpoint, json=payload, timeout=60)
    if r.status_code != 200:
        log.warning("instagram fail %s", r.text[:200])
        return []
    items = r.json() if isinstance(r.json(), list) else []
    out=[]
    for it in items:
        user = it.get("ownerUsername","")
        post = it.get("url","")
        uidv = uid_from("instagram", keyword, post or user)
        out.append(["instagram", keyword, user, f"https://instagram.com/{user}", "", post, 0.65, ts_now(), uidv])
    return out

# 5) TikTok — Apify actor (pulsuz kreditlərlə; token verilsə işləyir)
def fetch_tiktok_hashtag(keyword):
    if not APIFY_TOKEN:
        return []
    endpoint = (
      f"https://api.apify.com/v2/acts/apify~tiktok-hashtag-scraper/run-sync-get-dataset-items"
      f"?token={APIFY_TOKEN}"
    )
    payload = {
        "hashtags": [keyword],
        "resultsLimit": 30
    }
    r = requests.post(endpoint, json=payload, timeout=60)
    if r.status_code != 200:
        log.warning("tiktok fail %s", r.text[:200])
        return []
    items = r.json() if isinstance(r.json(), list) else []
    out=[]
    for it in items:
        user = it.get("authorMeta",{}).get("name","")
        post = it.get("webVideoUrl") or it.get("url","")
        uidv = uid_from("tiktok", keyword, post or user)
        out.append(["tiktok", keyword, user, f"https://www.tiktok.com/@{user}", "", post, 0.65, ts_now(), uidv])
    return out

# 6) Fallback RSSHub (saxlayırıq, amma yalnız ehtiyat)
def fetch_rsshub(platform, keyword):
    bases = {
        "instagram": f"https://rsshub.app/instagram/tag/{urllib.parse.quote(keyword)}",
        "tiktok": f"https://rsshub.app/tiktok/keyword/{urllib.parse.quote(keyword)}",
        "reddit": f"https://rsshub.app/reddit/search/{urllib.parse.quote(keyword)}",
        "youtube": f"https://rsshub.app/youtube/search/{urllib.parse.quote(keyword)}",
        "hackernews": f"https://rsshub.app/hackernews/keyword/{urllib.parse.quote(keyword)}",
    }
    url = bases.get(platform)
    if not url:
        return []
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        # RSS parse-ını sadələşdiririk – boş buraxırıq; əsas məqsəd primary mənbələrdir.
        return []
    except Exception as e:
        log.warning("rsshub fail %s %s", platform, e)
        return []

# Platform -> function xəritəsi
FETCHERS = {
    "hackernews": fetch_hn,
    "youtube": fetch_youtube,
    "reddit": fetch_reddit,
    "instagram": fetch_instagram_hashtag,  # APIFY ilə
    "tiktok": fetch_tiktok_hashtag,        # APIFY ilə
}

def run_once():
    total_rows = []
    sent_for_tg = []

    for kw in KEYWORDS:
        for plat in ACTIVE_PLATFORMS:
            fn = FETCHERS.get(plat)
            rows = []
            try:
                if fn:
                    rows = fn(kw)
                else:
                    rows = fetch_rsshub(plat, kw)
            except Exception as e:
                telegram_send(f"ℹ️ {plat} xətası ({kw}): {e}")
                continue

            # limit
            for r in rows:
                if len(total_rows) >= DAILY_LIMIT:
                    break
                total_rows.append(r)

            # TOP N to Telegram
            sent_for_tg.extend(rows[:TOP_N_TELEGRAM])

            log.info("fetched %s/%s: %d", plat, kw, len(rows))
            time.sleep(1.5)

    # Sheets yaz
    written = sheet_append_rows(total_rows)

    # Telegram-a toplu mesaj (opsional)
    if TG_TOKEN and TG_CHAT and sent_for_tg:
        lines = []
        for r in sent_for_tg[:TOP_N_TELEGRAM]:
            platform, topic, username, profile_url, _, content_url, score, *_ = r
            line = f"• [{platform}] {username or topic}\n🔗 {content_url}"
            lines.append(line)
        telegram_send("\n\n".join(lines))

    log.info("done. written_to_sheets=%s", written)

def main():
    telegram_send("✅ Bot başladı. Axtarışa keçirəm…")
    run_once()
    telegram_send("🏁 Bot bitdi.")

if __name__ == "__main__":
    main()
