import os, json, time, re
from datetime import datetime, timezone
from urllib.parse import quote_plus
import requests, feedparser

# ---- Sheets bậtıqsa modul gətirək ----
SHEETS_ENABLED = bool(os.getenv("GCP_SA_KEY") and os.getenv("SHEETS_SPREADSHEET_ID"))
if SHEETS_ENABLED:
    import gspread
    from google.oauth2.service_account import Credentials

# ------------- ENV -------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ACTIVE_PLATFORMS = [p.strip().lower() for p in os.getenv("ACTIVE_PLATFORMS", "reddit,hackernews,youtube,instagram,tiktok,producthunt").split(",") if p.strip()]
KEYWORDS        = [k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()]

DAILY_LIMIT     = int(os.getenv("DAILY_LIMIT", "900"))          # Sheets üçün üst limit
TOP_N_TELEGRAM  = int(os.getenv("TOP_N_TELEGRAM", "20"))        # TG üçün top N

SHEETS_SPREADSHEET_ID = os.getenv("SHEETS_SPREADSHEET_ID","").strip()
SHEETS_TAB            = os.getenv("SHEETS_TAB","data").strip()

# ------------- Helpers -------------
def ts_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z")

def tmsg(text: str, disable_web_page_preview: bool=True):
    """Telegram göndərişi — token/chat yoxdursa sakitcə çıx."""
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true" if disable_web_page_preview else "false",
            },
            timeout=20,
        )
    except Exception:
        pass

def fmt_item(i):
    t = i.get("title","").strip()
    l = i.get("link","").strip()
    src = i.get("source","").strip()
    kw  = i.get("keyword","")
    kw_tag = f" #{kw}" if kw else ""
    return f"• {t}\n🔗 <a href=\"{l}\">{l}</a>\n#{src}{kw_tag}"

def dedup(items):
    seen = set(); out = []
    for it in items:
        key = it.get("link","")
        if key and key not in seen:
            seen.add(key); out.append(it)
    return out

# ------------- RSS mənbələri -------------
def fetch_reddit(keyword, limit=100):
    url = f"https://www.reddit.com/search.rss?q={quote_plus(keyword)}&sort=new&limit={limit}"
    d = feedparser.parse(url)
    items = []
    for e in d.entries[:limit]:
        items.append({
            "source":"reddit",
            "title": re.sub(r"\s+"," ", getattr(e,"title","")).strip(),
            "link": getattr(e,"link",""),
            "keyword": keyword,
            "published": getattr(e, "published_parsed", None)
        })
    return items

def fetch_hackernews(keyword, limit=100):
    url = f"https://hnrss.org/newest?q={quote_plus(keyword)}"
    d = feedparser.parse(url)
    items = []
    for e in d.entries[:limit]:
        items.append({
            "source":"hackernews",
            "title": re.sub(r"\s+"," ", getattr(e,"title","")).strip(),
            "link": getattr(e,"link",""),
            "keyword": keyword,
            "published": getattr(e, "published_parsed", None)
        })
    return items

def fetch_youtube(keyword, limit=100):
    url = f"https://www.youtube.com/feeds/videos.xml?search_query={quote_plus(keyword)}"
    d = feedparser.parse(url)
    items = []
    for e in d.entries[:limit]:
        items.append({
            "source":"youtube",
            "title": re.sub(r"\s+"," ", getattr(e,"title","")).strip(),
            "link": getattr(e,"link",""),
            "keyword": keyword,
            "published": getattr(e, "published_parsed", None)
        })
    return items

# ------------- API-siz “site search” (DDG HTML) -------------
DDG_HTML = "https://duckduckgo.com/html/?q={query}"

def ddg_site_search(domain: str, keyword: str, limit: int = 50):
    q = f"site:{domain} {keyword}"
    url = DDG_HTML.format(query=quote_plus(q))
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=20)
        html = r.text
    except Exception:
        return []
    anchors = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, flags=re.I)
    items = []
    for href, title_html in anchors:
        if domain not in href:
            continue
        title = re.sub(r"<[^>]+>", "", title_html)
        title = re.sub(r"\s+", " ", title).strip()
        items.append({"title": title or href, "link": href})
        if len(items) >= limit: break
    return items

def fetch_instagram(keyword, limit=60):
    raw = ddg_site_search("instagram.com", keyword, limit=limit)
    return [{"source":"instagram", "title":x["title"], "link":x["link"], "keyword":keyword} for x in raw]

def fetch_tiktok(keyword, limit=60):
    raw = ddg_site_search("tiktok.com", keyword, limit=limit)
    return [{"source":"tiktok", "title":x["title"], "link":x["link"], "keyword":keyword} for x in raw]

def fetch_producthunt(keyword, limit=60):
    raw = ddg_site_search("producthunt.com", keyword, limit=limit)
    return [{"source":"producthunt", "title":x["title"], "link":x["link"], "keyword":keyword} for x in raw]

FETCHERS = {
    "reddit": fetch_reddit,
    "hackernews": fetch_hackernews,
    "youtube": fetch_youtube,
    "instagram": fetch_instagram,
    "tiktok": fetch_tiktok,
    "producthunt": fetch_producthunt,
}

# ------------- Scoring (TOP 20 üçün) -------------
SRC_WEIGHT = {
    "producthunt": 3.0,
    "hackernews":  3.0,
    "youtube":     2.0,
    "reddit":      1.5,
    "instagram":   1.0,
    "tiktok":      1.0,
}
def score(item):
    base = SRC_WEIGHT.get(item.get("source",""), 1.0)
    title = item.get("title","")
    # qısa & konkret başlığa kiçik bonus
    length_bonus = 0.4 if 20 <= len(title) <= 90 else 0.0
    # recency (RSS-lərdə varsa)
    rec = item.get("published")
    rec_bonus = 0.5 if rec else 0.0
    return base + length_bonus + rec_bonus

# ------------- Sheets -------------
def sheet_client():
    data = json.loads(os.getenv("GCP_SA_KEY"))
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(data, scopes=scopes)
    return gspread.authorize(creds)

def sheet_write(rows):
    if not SHEETS_ENABLED or not rows:
        return
    gc = sheet_client()
    sh = gc.open_by_key(SHEETS_SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEETS_TAB)
    except Exception:
        ws = sh.add_worksheet(title=SHEETS_TAB, rows=2000, cols=6)
    existing = ws.get_all_values()
    if not existing:
        ws.append_row(["timestamp_utc","source","keyword","title","link"], value_input_option="RAW")
    ws.append_rows(rows, value_input_option="RAW")

# ------------- MAIN -------------
def main():
    if BOT_TOKEN and CHAT_ID:
        tmsg("✅ GitHub Actions bot başladı. Axtarışa keçirəm…")

    if not KEYWORDS:
        tmsg("ℹ️ Heç bir <b>KEYWORDS</b> verilməyib, axtarış atlandı.")
        return

    collected = []
    per_kw_cap  = max(1, DAILY_LIMIT // max(1,len(KEYWORDS)))
    per_src_cap = max(1, per_kw_cap // max(1,len(ACTIVE_PLATFORMS)))

    for kw in KEYWORDS:
        for src in ACTIVE_PLATFORMS:
            fn = FETCHERS.get(src)
            if not fn: continue
            try:
                items = fn(kw, limit=per_src_cap * 3)
            except Exception:
                items = []
            items = items[:per_src_cap]
            for it in items:
                it["keyword"] = kw
            collected.extend(items)
            time.sleep(0.35)

    collected = dedup(collected)
    # Sheets üçün maksimum 900
    to_sheet = collected[:min(DAILY_LIMIT, 900)]

    # TOP 20-ni hesabla və Telegrama göndər
    top_items = sorted(collected, key=score, reverse=True)[:TOP_N_TELEGRAM]

    # Telegram (yalnız top N)
    for it in top_items:
        tmsg(fmt_item(it))
        time.sleep(0.15)

    # Sheets yaz
    rows = [[ts_now_iso(), it.get("source",""), it.get("keyword",""), it.get("title",""), it.get("link","")] for it in to_sheet]
    if rows:
        try:
            sheet_write(rows)
        except Exception as e:
            tmsg(f"⚠️ Sheets yazma xətası: {e}")

    if BOT_TOKEN and CHAT_ID:
        tmsg(f"🟢 Axtarış tamamlandı. TG: {len(top_items)} xəbər, Sheets: {len(rows)} sətir.")
        tmsg("🏁 GitHub Actions bot bitdi.")

if __name__ == "__main__":
    main()
