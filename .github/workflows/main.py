import os
import time
import html
import textwrap
from urllib.parse import quote_plus
import requests
import feedparser

# ------------ Env & defaults ------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ACTIVE_PLATFORMS = (os.getenv("ACTIVE_PLATFORMS", "reddit,hn,youtube")
                    .lower().replace(" ", ""))
# keywords "ayaqqabı/paltar/..." kimi bölünür
KEYWORDS_RAW = os.getenv("KEYWORDS", "ayaqqabı/telefon").strip()
KEYWORDS = [k for k in KEYWORDS_RAW.split("/") if k]
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "50"))

if not BOT_TOKEN or not CHAT_ID:
    print(f"ENV check: TOK={'OK' if BOT_TOKEN else 'MISSING'} "
          f"CID={'OK' if CHAT_ID else 'MISSING'} "
          f"PLATFORMS={ACTIVE_PLATFORMS} KW={KEYWORDS_RAW} LIMIT={DAILY_LIMIT}")
    raise SystemExit(1)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


# ------------ Helpers ------------
def send_telegram(text: str):
    """Split-safe Telegram sender (HTML)."""
    text = text.strip()
    if not text:
        return
    # Telegram message limit ~4096
    chunks = [text[i:i+3500] for i in range(0, len(text), 3500)]
    for chunk in chunks:
        resp = requests.post(
            TG_API,
            data={
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print("Telegram error:", resp.status_code, resp.text)
        time.sleep(0.6)


def fmt_item(title: str, link: str, src: str) -> str:
    title = html.escape(title or "").strip()
    link = link.strip()
    return f"• <b>{src}</b>: <a href=\"{link}\">{title}</a>"


# ------------ Fetchers (RSS) ------------
def fetch_reddit(keyword: str, max_items: int):
    url = f"https://www.reddit.com/search.rss?q={quote_plus(keyword)}&sort=new"
    d = feedparser.parse(url)
    out = []
    for e in d.entries[:max_items]:
        title = getattr(e, "title", "")
        link = getattr(e, "link", "")
        if title and link:
            out.append(("reddit", title, link))
    return out


def fetch_hn(keyword: str, max_items: int):
    # hnrss.org – Algolia search
    url = f"https://hnrss.org/newest?q={quote_plus(keyword)}"
    d = feedparser.parse(url)
    out = []
    for e in d.entries[:max_items]:
        title = getattr(e, "title", "")
        link = getattr(e, "link", "")
        if title and link:
            out.append(("hn", title, link))
    return out


def fetch_youtube(keyword: str, max_items: int):
    # YouTube search RSS (rəsmi olmasa da işləyir)
    url = f"https://www.youtube.com/feeds/videos.xml?search_query={quote_plus(keyword)}"
    d = feedparser.parse(url)
    out = []
    for e in d.entries[:max_items]:
        title = getattr(e, "title", "")
        link = getattr(e, "link", "")
        if title and link:
            out.append(("youtube", title, link))
    return out


# Placeholder-lar (hazırda RSS yoxdur)
def fetch_placeholder(name: str, *_args, **_kw):
    # Burada heç nə qaytarmırıq – sadəcə atlayırıq
    print(f"{name}: rəsmi RSS yoxdur, atlanır.")
    return []


FETCHERS = {
    "reddit": fetch_reddit,
    "hn": fetch_hn,            # Hacker News
    "hackernews": fetch_hn,
    "youtube": fetch_youtube,
    "producthunt": lambda kw, n: fetch_placeholder("producthunt"),
    "instagram": lambda kw, n: fetch_placeholder("instagram"),
    "tiktok": lambda kw, n: fetch_placeholder("tiktok"),
    "threads": lambda kw, n: fetch_placeholder("threads"),
}

# Hansılar aktivdirsə, sırala
PLATFORMS = [p for p in ACTIVE_PLATFORMS.split(",") if p in FETCHERS]


# ------------ Main ------------
def main():
    # Sürətli “ping”
    send_telegram("✅ <b>GitHub Actions bot</b> başladı. Axtarışa keçirəm…")

    total_sent = 0
    report_lines = []
    seen_links = set()

    for platform in PLATFORMS:
        fn = FETCHERS[platform]
        platform_count = 0

        for kw in KEYWORDS:
            if total_sent >= DAILY_LIMIT:
                break
            max_per_kw = max(1, min(10, DAILY_LIMIT - total_sent))
            try:
                items = fn(kw, max_per_kw)
            except Exception as e:
                print(f"{platform}/{kw} fetch error: {e}")
                continue

            for (_src, title, link) in items:
                if total_sent >= DAILY_LIMIT:
                    break
                if link in seen_links:
                    continue
                seen_links.add(link)
                report_lines.append(fmt_item(title, link, platform))
                total_sent += 1
                platform_count += 1

        print(f"{platform}: {platform_count} nəticə toplandı.")

    if report_lines:
        header = f"🔎 <b>Axtarış tamamlandı</b>\n" \
                 f"Platformalar: <code>{', '.join(PLATFORMS) or '—'}</code>\n" \
                 f"Açar sözlər: <code>{', '.join(KEYWORDS) or '—'}</code>\n" \
                 f"Limit: <code>{DAILY_LIMIT}</code>\n\n"
        # Çox uzundursa bölüb göndər
        payload = header + "\n".join(report_lines)
        for chunk in textwrap.wrap(payload, 3500, break_long_words=False, break_on_hyphens=False):
            send_telegram(chunk)
    else:
        send_telegram("ℹ️ Heç nə tapılmadı (RSS mənbələrində uyğun nəticə yoxdur).")


if __name__ == "__main__":
    main()
