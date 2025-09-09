# -*- coding: utf-8 -*-
import os
import re
import sys
import time
import json
import html
import textwrap
from typing import List, Dict

import requests
import feedparser
from bs4 import BeautifulSoup


# ------------- Konfiq -------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ACTIVE_PLATFORMS = os.getenv("ACTIVE_PLATFORMS", "reddit,youtube,hackernews").lower()
ACTIVE_PLATFORMS = [p.strip() for p in ACTIVE_PLATFORMS.split(",") if p.strip()]

KEYWORDS_RAW = os.getenv("KEYWORDS", "")
# vergül və ya yeni sətirə görə parçala
KEYWORDS = [k.strip() for k in re.split(r"[,\n]+", KEYWORDS_RAW) if k.strip()]
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "50"))

# RSS mənbələri (açarlar platforma adları ilə eyni olmalıdır)
FEEDS: Dict[str, List[str]] = {
    "reddit": [
        # Populyar “new” axını (ürək sözləri ilə filtrləyəcəyik)
        "https://www.reddit.com/r/all/new/.rss",
        # İstəsən bura əlavə alt-reddit RSS-ləri də ata bilərsən
    ],
    "youtube": [
        # Trend/keyword RSS yoxdur; amma YouTube search RSS işləyir:
        # Aşağıda run vaxtı hər açar söz üçün ayrıca feed quracağıq.
        # Placeholder — boş saxlayırıq, dinamik qurulacaq
    ],
    "hackernews": [
        "https://hnrss.org/newest"
    ],
}

# ------------- Yardımçılar -------------
def log(msg: str):
    print(msg, flush=True)

def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        log("Telegram token/chat_id yoxdur, mesaj atlanır.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, data=payload, timeout=20)
        if r.status_code != 200:
            log(f"Telegram ERROR {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"Telegram istisna: {e}")

def norm(s: str) -> str:
    return html.unescape(s or "").strip()

def text_matches(s: str, keywords: List[str]) -> bool:
    s_low = s.lower()
    return any(k.lower() in s_low for k in keywords)

def clean_summary(summary_html: str) -> str:
    if not summary_html:
        return ""
    txt = BeautifulSoup(summary_html, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", txt).strip()

def limit_and_dedup(items: List[dict], limit: int) -> List[dict]:
    seen = set()
    out = []
    for it in items:
        u = it.get("link") or it.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(it)
        if len(out) >= limit:
            break
    return out

# ------------- Toplayıcılar -------------
def fetch_rss(url: str) -> feedparser.FeedParserDict:
    headers = {
        "User-Agent": "Mozilla/5.0 (GitHubActions RSS bot)"
    }
    # feedparser öz-özünə yükləyir; headers üçün requests-lə çəkib feedparser.parse(content) də edə bilərdik
    return feedparser.parse(url)

def collect_from_feeds(platform: str, feeds: List[str], keywords: List[str], limit: int) -> List[dict]:
    results = []
    for url in feeds:
        try:
            d = fetch_rss(url)
            for e in d.entries:
                title = norm(getattr(e, "title", ""))
                link  = norm(getattr(e, "link", ""))
                summ  = clean_summary(getattr(e, "summary", ""))
                blob  = f"{title}\n{summ}"

                if keywords and not text_matches(blob, keywords):
                    continue

                results.append({
                    "platform": platform,
                    "title": title or "(no title)",
                    "link": link,
                    "summary": summ,
                })
        except Exception as ex:
            log(f"{platform}: feed xətası: {ex}")
    # təkrarlananları at və limiti tətbiq et
    return limit_and_dedup(results, limit)

def collect_reddit(keywords: List[str], limit: int) -> List[dict]:
    log("reddit: yüklənir…")
    return collect_from_feeds("reddit", FEEDS["reddit"], keywords, limit)

def collect_hn(keywords: List[str], limit: int) -> List[dict]:
    log("hackernews: yüklənir…")
    return collect_from_feeds("hackernews", FEEDS["hackernews"], keywords, limit)

def collect_youtube(keywords: List[str], limit: int) -> List[dict]:
    log("youtube: yüklənir…")
    # YouTube search RSS: https://www.youtube.com/feeds/videos.xml?search_query=QUERY
    # Hər açar söz üçün ayrı feed
    items = []
    kws = keywords if keywords else [""]
    for kw in kws:
        q = requests.utils.quote(kw)
        url = f"https://www.youtube.com/feeds/videos.xml?search_query={q}"
        try:
            d = fetch_rss(url)
            for e in d.entries:
                title = norm(getattr(e, "title", ""))
                link  = norm(getattr(e, "link", ""))
                summ  = clean_summary(getattr(e, "summary", ""))
                blob  = f"{title}\n{summ}"
                # əgər keywords boş deyilsə, yenə də yoxla (çünki youtube feed məhz həmin keyword-la gəlir,
                # amma yenə də təhlükəsizlik üçün)
                if keywords and not text_matches(blob, keywords):
                    continue

                items.append({
                    "platform": "youtube",
                    "title": title or "(no title)",
                    "link": link,
                    "summary": summ,
                })
        except Exception as ex:
            log(f"youtube: feed xətası: {ex}")

    return limit_and_dedup(items, limit)

COLLECTORS = {
    "reddit": collect_reddit,
    "youtube": collect_youtube,
    "hackernews": collect_hn,
}

# ------------- Mesaj formatı -------------
def format_item(it: dict) -> str:
    title = it["title"]
    link  = it["link"]
    plat  = it["platform"]
    # başlığı qısa saxla
    title = (title[:200] + "…") if len(title) > 200 else title
    return f"• <b>{html.escape(title)}</b>\n🔗 {link}\n#{plat}"

def send_batch(platform: str, items: List[dict]):
    if not items:
        send_telegram(f"ℹ️ <b>{platform}</b> üçün uyğun nəticə tapılmadı.")
        return
    header = f"✅ <b>{platform}</b> — {len(items)} nəticə"
    send_telegram(header)
    for it in items:
        send_telegram(format_item(it))
        time.sleep(0.7)  # flood-limitdən qaçmaq üçün bir az gecikmə

# ------------- Main -------------
def main():
    log(f"ENV: platforms={ACTIVE_PLATFORMS} keywords={KEYWORDS} limit={DAILY_LIMIT}")

    if not BOT_TOKEN or not CHAT_ID:
        log("Telegram SECRET-lər yoxdur. Dayandırılır.")
        sys.exit(0)

    # Limit platformalar arasında paylansın (təxmini bərabər)
    per_platform = max(1, DAILY_LIMIT // max(1, len(ACTIVE_PLATFORMS)))

    for plat in ACTIVE_PLATFORMS:
        fn = COLLECTORS.get(plat)
        if not fn:
            log(f"{plat}: tanınmır, atlanır.")
            continue
        try:
            items = fn(KEYWORDS, per_platform)
            log(f"{plat}: {len(items)} nəticə toplandı.")
            send_batch(plat, items)
        except Exception as e:
            log(f"{plat}: collector xətası: {e}")
            send_telegram(f"⚠️ <b>{plat}</b> üçün xətaya düşdüm: {html.escape(str(e))}")

    send_telegram("🟢 Axtarış tamamlandı.")

if __name__ == "__main__":
    main()
