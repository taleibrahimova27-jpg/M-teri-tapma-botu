import os
import requests
from datetime import datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import telegram

# ========= Secrets / Config =========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS", "test").split(",") if k.strip()]

ACTIVE_PLATFORMS = [p.strip().lower() for p in os.getenv(
    "ACTIVE_PLATFORMS",
    "reddit,youtube,hackernews,producthunt"
).replace("treads", "threads").split(",") if p.strip()]

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "20"))  # hər platforma x keyword üçün limit

# Optional API keys (yoxdursa bu platformalar SKIP ediləcək)
INSTAGRAM_RAPIDAPI_KEY = os.getenv("INSTAGRAM_RAPIDAPI_KEY")
TIKTOK_RAPIDAPI_KEY = os.getenv("TIKTOK_RAPIDAPI_KEY")
THREADS_TOKEN = os.getenv("THREADS_TOKEN")

# Telegram bot
bot = telegram.Bot(token=TELEGRAM_TOKEN)

UA = {"User-Agent": "Mozilla/5.0 (LeadFinderBot/1.0)"}


# ========= Helper =========
def chunked_send(text: str, chat_id: str):
    """Telegram 4096 limiti üçün mesajı parçalayıb göndərir"""
    if not text:
        return
    maxlen = 3800
    for i in range(0, len(text), maxlen):
        bot.send_message(chat_id=chat_id, text=text[i:i+maxlen])


def normalize_lead(platform, topic, username, profile_link, content_url):
    return {
        "platform": platform,
        "topic": topic,
        "username": username or "",
        "profile_link": profile_link or "",
        "content_url": content_url or "",
    }


# ========= Platforms =========
def fetch_reddit(keyword, limit):
    url = f"https://www.reddit.com/search.json?q={quote_plus(keyword)}&limit={limit}"
    leads = []
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        data = r.json()
        for post in data.get("data", {}).get("children", []):
            d = post.get("data", {})
            user = d.get("author")
            profile = f"https://reddit.com/u/{user}" if user else ""
            content = "https://reddit.com" + d.get("permalink", "")
            leads.append(normalize_lead("reddit", keyword, user, profile, content))
    except Exception as e:
        print("Reddit error:", e)
    return leads


def fetch_youtube(keyword, limit):
    """YouTube Search RSS (API-siz)"""
    url = f"https://www.youtube.com/feeds/videos.xml?search_query={quote_plus(keyword)}"
    leads = []
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"yt": "http://www.youtube.com/xml/schemas/2015", "atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)[:limit]
        for e in entries:
            title = (e.find("atom:title", ns).text or "").strip()
            link_el = e.find("atom:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            author_el = e.find("atom:author/atom:name", ns)
            channel = author_el.text if author_el is not None else ""
            channel_link_el = e.find("yt:channelId", ns)
            chan_id = channel_link_el.text if channel_link_el is not None else ""
            profile = f"https://www.youtube.com/channel/{chan_id}" if chan_id else ""
            leads.append(normalize_lead("youtube", keyword, channel, profile, link))
    except Exception as e:
        print("YouTube RSS error:", e)
    return leads


def fetch_hackernews(keyword, limit):
    """HN Algolia API (publik)"""
    url = f"https://hn.algolia.com/api/v1/search?query={quote_plus(keyword)}&tags=story&hitsPerPage={limit}"
    leads = []
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        for hit in r.json().get("hits", []):
            title = hit.get("title")
            author = hit.get("author")
            url2 = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            profile = f"https://news.ycombinator.com/user?id={author}" if author else ""
            leads.append(normalize_lead("hackernews", keyword, author, profile, url2))
    except Exception as e:
        print("HN error:", e)
    return leads


def fetch_producthunt(keyword, limit):
    """Product Hunt RSS (API-siz) – başlıqda filter"""
    url = "https://www.producthunt.com/feed"
    leads = []
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for item in root.findall("./channel/item")[: 200]:
            title = (item.findtext("title") or "")
            link = (item.findtext("link") or "")
            if keyword.lower() in title.lower():
                leads.append(normalize_lead("producthunt", keyword, "", "", link))
            if len(leads) >= limit:
                break
    except Exception as e:
        print("PH RSS error:", e)
    return leads


def fetch_instagram(keyword, limit):
    """Opsional: RapidAPI ilə axtarış – AÇAR yoxdursa SKIP"""
    if not INSTAGRAM_RAPIDAPI_KEY:
        return []
    # nümunə endpoint (RapidAPI provider dəyişə bilər)
    url = "https://instagram-data1.p.rapidapi.com/search"
    params = {"query": keyword, "count": str(limit)}
    headers = {
        "X-RapidAPI-Key": INSTAGRAM_RAPIDAPI_KEY,
        "X-RapidAPI-Host": "instagram-data1.p.rapidapi.com"
    }
    leads = []
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        js = r.json()
        for u in (js.get("users") or [])[:limit]:
            uname = u.get("username") or u.get("user", {}).get("username")
            profile = f"https://instagram.com/{uname}" if uname else ""
            leads.append(normalize_lead("instagram", keyword, uname, profile, profile))
    except Exception as e:
        print("Instagram error:", e)
    return leads


def fetch_tiktok(keyword, limit):
    """Opsional: RapidAPI – AÇAR yoxdursa SKIP"""
    if not TIKTOK_RAPIDAPI_KEY:
        return []
    url = "https://tiktok-scraper7.p.rapidapi.com/search/user"
    params = {"keywords": keyword, "count": str(limit)}
    headers = {
        "X-RapidAPI-Key": TIKTOK_RAPIDAPI_KEY,
        "X-RapidAPI-Host": "tiktok-scraper7.p.rapidapi.com"
    }
    leads = []
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        js = r.json()
        for u in (js.get("data") or [])[:limit]:
            uname = u.get("uniqueId")
            profile = f"https://www.tiktok.com/@{uname}" if uname else ""
            leads.append(normalize_lead("tiktok", keyword, uname, profile, profile))
    except Exception as e:
        print("TikTok error:", e)
    return leads


def fetch_threads(keyword, limit):
    """Opsional: Threads üçün token lazımdır (əks halda SKIP)"""
    if not THREADS_TOKEN:
        return []
    # Placeholder – real endpoint/token provayderindən asılıdır.
    # İstəyin varsa sonradan konkret API-ni bağlayarıq.
    return []


PLATFORM_FUNCS = {
    "reddit": fetch_reddit,
    "youtube": fetch_youtube,
    "hackernews": fetch_hackernews,
    "producthunt": fetch_producthunt,
    "instagram": fetch_instagram,
    "tiktok": fetch_tiktok,
    "threads": fetch_threads,
}


# ========= Run =========
def run_bot():
    all_leads = []
    seen_urls = set()

    for kw in KEYWORDS:
        for plat in ACTIVE_PLATFORMS:
            fn = PLATFORM_FUNCS.get(plat)
            if not fn:
                print(f"Skip unknown platform: {plat}")
                continue
            leads = fn(kw, DAILY_LIMIT)
            for L in leads:
                u = L["content_url"]
                if u and u in seen_urls:
                    continue
                seen_urls.add(u)
                all_leads.append(L)

    # TOP 20-ni Teleqrama
    if not all_leads:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚠ Heç bir nəticə tapılmadı.")
        print(f"[{datetime.now()}] 0 result")
        return

    header = f"📊 Yeni nəticələr ({len(all_leads)}) — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    body = ""
    for lead in all_leads[:20]:
        body += (
            f"🔹 {lead['platform']} | {lead['topic']}\n"
            f"👤 {lead['username']}\n"
            f"🔗 {lead['profile_link']}\n"
            f"📌 {lead['content_url']}\n\n"
        )
    chunked_send(header + body, TELEGRAM_CHAT_ID)

    print(f"[{datetime.now()}] Sent {min(20, len(all_leads))}/{len(all_leads)} to Telegram")


if __name__ == "__main__":
    run_bot()
