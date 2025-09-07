# .github/workflows/main.py
import os, time, requests, feedparser
from html import unescape

TOK = os.getenv("TELEGRAM_BOT_TOKEN", "")
CID = os.getenv("TELEGRAM_CHAT_ID", "")
PLATFORMS = [p.strip().lower() for p in os.getenv("ACTIVE_PLATFORMS", "").split(",") if p.strip()]
KW = [k.strip().lower() for k in os.getenv("KEYWORDS", "").split("/") if k.strip()]
try:
    LIMIT = int(os.getenv("DAILY_LIMIT", "50"))
except:
    LIMIT = 50

def tg(text: str):
    if not TOK or not CID:
        print("Telegram token/chat_id yoxdur, mesajı keçdim.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOK}/sendMessage",
            data={
                "chat_id": CID,
                "text": text,
                "disable_web_page_preview": True,
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        print("TG:", r.status_code, r.text[:120])
    except Exception as e:
        print("TG error:", e)

def match_kw(title: str) -> bool:
    if not KW:
        return True
    t = (title or "").lower()
    return any(k in t for k in KW)

def fetch_reddit(max_items=50):
    url = "https://www.reddit.com/r/all/.rss"
    feed = feedparser.parse(url)
    out = []
    for e in feed.entries:
        title = unescape(e.get("title", ""))
        link = e.get("link", "")
        if match_kw(title):
            out.append(("reddit", title, link))
        if len(out) >= max_items:
            break
    print(f"reddit: {len(out)} nəticə toplandı.")
    return out

def fetch_hackernews(max_items=50):
    url = "https://hnrss.org/newest"
    feed = feedparser.parse(url)
    out = []
    for e in feed.entries:
        title = unescape(e.get("title", ""))
        link = e.get("link", "")
        if match_kw(title):
            out.append(("hackernews", title, link))
        if len(out) >= max_items:
            break
    print(f"hackernews: {len(out)} nəticə toplandı.")
    return out

def fetch_youtube(max_items=50):
    # Nümunə kanal feed (API-siz)
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCVHFbqXqoYvEWM1Ddxl0QDg"
    feed = feedparser.parse(url)
    out = []
    for e in feed.entries:
        title = unescape(e.get("title", ""))
        link = e.get("link", "")
        if match_kw(title):
            out.append(("youtube", title, link))
        if len(out) >= max_items:
            break
    print(f"youtube: {len(out)} nəticə toplandı.")
    return out

def fetch_placeholder(name: str, max_items=0):
    print(f"{name}: rəsmi RSS yoxdur, atlanır.")
    return []

# HAMISI EYNİ İMZA İLƏ (max_items) ÇAĞIRILSIN DEYƏ LAMBDALAR
FETCHERS = {
    "reddit":        (lambda max_items=50: fetch_reddit(max_items)),
    "hackernews":    (lambda max_items=50: fetch_hackernews(max_items)),
    "youtube":       (lambda max_items=50: fetch_youtube(max_items)),
    "producthunt":   (lambda max_items=50: fetch_placeholder("producthunt", max_items)),
    "instagram":     (lambda max_items=50: fetch_placeholder("instagram", max_items)),
    "tiktok":        (lambda max_items=50: fetch_placeholder("tiktok", max_items)),
    "threads":       (lambda max_items=50: fetch_placeholder("threads", max_items)),
}

def main():
    tg("🚀 Bot başladı. Aktiv platformalar: <b>%s</b> | limit: <b>%s</b>" %
       (", ".join(PLATFORMS) if PLATFORMS else "hamısı (default)", LIMIT))

    items_all = []
    active = PLATFORMS or list(FETCHERS.keys())
    for p in active:
        fn = FETCHERS.get(p, lambda max_items=50, name=p: fetch_placeholder(name, max_items))
        try:
            items = fn(max_items=LIMIT)
        except Exception as e:
            print(f"{p}: fetch xətası:", e)
            items = []
        items_all.extend(items)

    sent = 0
    for src, title, link in items_all[:LIMIT]:
        tg(f"🔎 <b>{src}</b>\n{title}\n{link}")
        sent += 1
        time.sleep(0.3)

    tg(f"✅ Bitdi. Göndərilən: <b>{sent}</b> xəbər.")

if __name__ == "__main__":
    main()
