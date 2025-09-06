# .github/workflows/main.py
import os, time, json, requests, re
from datetime import datetime, timezone
from urllib.parse import quote_plus
import feedparser

# ------- Secrets / Config -------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ACTIVE_PLATFORMS = [p.strip().lower() for p in os.getenv("ACTIVE_PLATFORMS","").split(",") if p.strip()]
KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS","").split(",") if k.strip()]
try:
    DAILY_LIMIT = int(os.getenv("DAILY_LIMIT","20"))
except: DAILY_LIMIT = 20

UA = {"User-Agent":"leads-bot/1.0 (+github actions)"}

# ------- Utils -------
def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram token/chat_id yoxdur, mesaji kecdim.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }, timeout=30)
    ok = r.ok and r.json().get("ok", False)
    if not ok:
        print("TG error:", r.text)
    return ok

def clean(text: str) -> str:
    if not text: return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text

def from_feed(url: str, keyword: str = None, platform: str = "", limit: int = 15):
    """Generic RSS reader + optional keyword filter (title+summary)."""
    out = []
    try:
        feed = feedparser.parse(url)
        for e in feed.entries[:limit]:
            title = clean(getattr(e, "title", ""))
            summary = clean(getattr(e, "summary", "")) or clean(getattr(e, "description", ""))
            link = getattr(e, "link", "")
            if keyword:
                hay = f"{title} {summary}".lower()
                if keyword.lower() not in hay:
                    continue
            out.append({
                "platform": platform,
                "title": title or "(no title)",
                "username": clean(getattr(e, "author", "")),
                "url": link,
            })
    except Exception as ex:
        print(f"RSS error for {platform}:", ex)
    return out

# ------- Per-platform fetchers (API-siz mümkün olanlar) -------
def fetch_reddit(keyword, limit=10):
    # Reddit-in rəsmi RSS axtarışı
    q = quote_plus(keyword)
    url = f"https://www.reddit.com/search.rss?q={q}&sort=new&t=week"
    return from_feed(url, None, "reddit", limit)

def fetch_youtube(keyword, limit=10):
    # YouTube axtarış RSS (rəsmi)
    q = quote_plus(keyword)
    url = f"https://www.youtube.com/feeds/videos.xml?search_query={q}"
    return from_feed(url, None, "youtube", limit)

def fetch_hackernews(keyword, limit=10):
    # HN – Algolia public API (açar lazım deyil)
    url = "https://hn.algolia.com/api/v1/search"
    params = {"query": keyword, "tags": "story", "hitsPerPage": min(limit, 50)}
    items = []
    try:
        r = requests.get(url, params=params, headers=UA, timeout=30)
        r.raise_for_status()
        for hit in r.json().get("hits", []):
            items.append({
                "platform": "hackernews",
                "title": clean(hit.get("title","")),
                "username": clean(hit.get("author","")),
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            })
    except Exception as ex:
        print("HN error:", ex)
    return items[:limit]

def fetch_producthunt(keyword, limit=10):
    # PH-in ümumi RSS feed-i, başlıqda keyword filteri
    url = "https://www.producthunt.com/feed"
    return from_feed(url, keyword, "producthunt", limit)

# Placeholder-lar (bloklanırsa sükutla boş qaytarırıq)
def fetch_instagram(keyword, limit=10):
    print("instagram: rəsmi RSS yoxdur, atlanır.")
    return []

def fetch_tiktok(keyword, limit=10):
    print("tiktok: rəsmi RSS yoxdur, atlanır.")
    return []

def fetch_threads(keyword, limit=10):
    print("threads: rəsmi RSS yoxdur, atlanır.")
    return []

FETCHERS = {
    "reddit": fetch_reddit,
    "youtube": fetch_youtube,
    "hackernews": fetch_hackernews,
    "producthunt": fetch_producthunt,
    "instagram": fetch_instagram,
    "tiktok": fetch_tiktok,
    "threads": fetch_threads,
}

# ------- Orchestrator -------
def main():
    if not KEYWORDS:
        send_telegram("⚠️ KEYWORDS boşdur. Secrets bölməsində KEYWORDS əlavə et.")
        return

    plats = ACTIVE_PLATFORMS or list(FETCHERS.keys())
    results, seen = [], set()

    for kw in KEYWORDS:
        for plat in plats:
            fn = FETCHERS.get(plat)
            if not fn:
                print("Naməlum platforma:", plat); continue
            left = max(0, DAILY_LIMIT - len(results))
            if left == 0: break
            try:
                batch = fn(kw, min(5, left))
            except Exception as ex:
                print(f"{plat} fetch error:", ex)
                batch = []
            for it in batch:
                url = it.get("url","")
                if not url or url in seen: 
                    continue
                seen.add(url)
                results.append(it)
            time.sleep(0.8)  # rate-limit üçün kiçik pauza
        if len(results) >= DAILY_LIMIT: 
            break

    if not results:
        send_telegram("ℹ️ Heç nə tapılmadı (platformalar məhdud ola bilər).")
        return

    # İlk 12 nəticəni yığcam xülasə şəklində göndərək
    top = results[:12]
    lines = [f"🔎 {len(results)} nəticə tapıldı (ilk {len(top)} göstərilir)"]
    for i, it in enumerate(top, 1):
        title = it['title'][:120]
        user  = f" — @{it['username']}" if it.get("username") else ""
        lines.append(f"{i}. [{it['platform']}] {title}{user}\n{it['url']}")
    lines.append(f"\n⏱ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    send_telegram("\n\n".join(lines))

if __name__ == "__main__":
    main()
