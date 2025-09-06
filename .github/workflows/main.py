import os
import sys
import time
import html
import urllib.parse
from datetime import datetime, timezone
from typing import List, Dict
import xml.etree.ElementTree as ET
import urllib.request
import json

# =========================
#  Helpers / Config readers
# =========================

def getenv_required(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        print(f"[ERROR] Missing required secret: {name}", file=sys.stderr)
        sys.exit(1)
    return v

TELEGRAM_TOKEN   = getenv_required("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = getenv_required("TELEGRAM_CHAT_ID")
KEYWORDS         = [k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()]
DAILY_LIMIT      = int(os.getenv("DAILY_LIMIT", "50"))
ACTIVE_PLATFORMS = [p.strip().lower() for p in os.getenv("ACTIVE_PLATFORMS", "reddit,youtube").split(",") if p.strip()]

USER_AGENT = "Mozilla/5.0 (compatible; LeadHunter/1.0; +github-actions)"

def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()

def iso_dt(s: str) -> str:
    # best-effort parse for common RSS/Atom date formats
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
    return s

# =========================
#  Platform fetchers
# =========================

def fetch_reddit(keyword: str, limit: int) -> List[Dict]:
    # Public RSS search (no API key)
    q = urllib.parse.quote(keyword)
    url = f"https://www.reddit.com/search.rss?q={q}&sort=new"
    try:
        data = http_get(url)
        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom", "rss": "http://purl.org/rss/1.0/"}  # best-effort
        items = []
        # Atom <entry>
        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = "".join(entry.findtext("{http://www.w3.org/2005/Atom}title", "")).strip()
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.attrib.get("href") if link_el is not None else ""
            author = entry.findtext("{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name", "") or "reddit user"
            published = entry.findtext("{http://www.w3.org/2005/Atom}updated", "") or entry.findtext("{http://www.w3.org/2005/Atom}published", "")
            items.append({
                "platform": "reddit",
                "topic": keyword,
                "username": author,
                "profile_url": "",
                "content_url": link,
                "title": title,
                "published_at": iso_dt(published),
                "score": 1
            })
        # RSS <item> (fallback)
        if not items:
            for it in root.findall(".//item"):
                title = it.findtext("title", "")
                link  = it.findtext("link", "")
                pub   = it.findtext("pubDate", "")
                items.append({
                    "platform": "reddit",
                    "topic": keyword,
                    "username": "reddit user",
                    "profile_url": "",
                    "content_url": link,
                    "title": title,
                    "published_at": iso_dt(pub),
                    "score": 1
                })
        return items[:limit]
    except Exception as e:
        print(f"[WARN] Reddit fetch failed for '{keyword}': {e}", file=sys.stderr)
        return []

def fetch_youtube(keyword: str, limit: int) -> List[Dict]:
    # Public Atom feed for search (no API key)
    q = urllib.parse.quote(keyword)
    url = f"https://www.youtube.com/feeds/videos.xml?search_query={q}"
    try:
        data = http_get(url)
        root = ET.fromstring(data)
        items = []
        for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
            title = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.attrib.get("href") if link_el is not None else ""
            author = entry.findtext("{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name", "") or "YouTube channel"
            pub   = entry.findtext("{http://www.w3.org/2005/Atom}published", "")
            items.append({
                "platform": "youtube",
                "topic": keyword,
                "username": author,
                "profile_url": "",
                "content_url": link,
                "title": title,
                "published_at": iso_dt(pub),
                "score": 1
            })
        return items[:limit]
    except Exception as e:
        print(f"[WARN] YouTube fetch failed for '{keyword}': {e}", file=sys.stderr)
        return []

PLATFORM_FETCHERS = {
    "reddit": fetch_reddit,
    "youtube": fetch_youtube,
    # "instagram": ... (deaktiv — API tələb edir)
    # "tiktok": ...    (deaktiv — API tələb edir)
    # "facebook": ...  (deaktiv — API tələb edir)
    # "x": ...         (deaktiv — API tələb edir)
}

# =========================
#  Telegram
# =========================

def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            _ = resp.read()
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}", file=sys.stderr)

# =========================
#  Runner
# =========================

def run() -> None:
    if not KEYWORDS:
        print("[ERROR] KEYWORDS secret is empty (comma separated list expected).", file=sys.stderr)
        sys.exit(1)

    active = [p for p in ACTIVE_PLATFORMS if p in PLATFORM_FETCHERS]
    if not active:
        print("[ERROR] ACTIVE_PLATFORMS has no supported platforms. Use at least: reddit,youtube", file=sys.stderr)
        sys.exit(1)

    per_platform = max(1, DAILY_LIMIT // max(1, len(active)))
    all_items: List[Dict] = []

    for kw in KEYWORDS:
        for platform in active:
            fetcher = PLATFORM_FETCHERS[platform]
            items = fetcher(kw, per_platform)
            all_items.extend(items)
            time.sleep(0.5)  # be kind

    # sort by time (desc) if possible
    def sort_key(it):
        return it.get("published_at", "")

    all_items.sort(key=sort_key, reverse=True)
    all_items = all_items[:DAILY_LIMIT]

    # Build Telegram top-20 preview
    top_for_tg = all_items[:20]
    if top_for_tg:
        lines = []
        for i, it in enumerate(top_for_tg, start=1):
            title = html.escape(it.get("title", "")[:90])
            link  = it.get("content_url", "")
            platform = it.get("platform", "")
            user = html.escape(it.get("username", ""))
            lines.append(f"{i}. <b>{platform}</b> — {user}\n<a href=\"{link}\">{title}</a>")
        msg = "📌 <b>TOP nəticələr</b>\n\n" + "\n\n".join(lines)
        tg_send(msg)
    else:
        tg_send("⚠️ Heç nə tapılmadı. Açar sözləri yoxlayın.")

    # Also print JSON to logs (optionally to be captured as artifact)
    print(json.dumps(all_items, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    run()
