# -*- coding: utf-8 -*-
import os, re, html, time
from urllib.parse import quote_plus
import requests
import feedparser

UA = {"User-Agent": "Mozilla/5.0 (LeadsBot/1.0; +https://github.com/)"}

def log(msg):
    print(str(msg), flush=True)

def clean_text(s, max_len=140):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s

def fetch_rss(url, max_items=50):
    try:
        resp = requests.get(url, headers=UA, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log(f"RSS error: {url} -> {e}")
        return []
    feed = feedparser.parse(resp.content)
    items = []
    for e in feed.get("entries", [])[: max(0, int(max_items))]:
        title = clean_text(e.get("title", ""))
        link = e.get("link", "")
        if title and link:
            items.append({"title": title, "url": link})
    return items

# ---- Platform axtarışları (RSS əsaslı) ----
def search_reddit(kw, max_items=50):
    url = f"https://www.reddit.com/search.rss?q={quote_plus(kw)}&sort=new"
    return fetch_rss(url, max_items)

def search_youtube(kw, max_items=50):
    url = f"https://www.youtube.com/feeds/videos.xml?search_query={quote_plus(kw)}"
    return fetch_rss(url, max_items)

def search_hackernews(kw, max_items=50):
    url = f"https://hnrss.org/newest?q={quote_plus(kw)}"
    return fetch_rss(url, max_items)

def search_producthunt(kw, max_items=50):
    url = f"https://rsshub.app/producthunt/today?search={quote_plus(kw)}"
    items = fetch_rss(url, max_items)
    if not items:
        log("producthunt: uyğun RSS ya boşdur, ya da məhduddur.")
    return items

def skip_platform_msg(name):
    msg = f"{name}: rəsmi RSS yoxdur, atlanır."
    log(msg)
    return []

PLATFORM_FUNCS = {
    "reddit":       search_reddit,
    "youtube":      search_youtube,
    "hackernews":   search_hackernews,
    "producthunt":  search_producthunt,
    "instagram":    lambda *args, **kwargs: skip_platform_msg("instagram"),
    "tiktok":       lambda *args, **kwargs: skip_platform_msg("tiktok"),
    "threads":      lambda *args, **kwargs: skip_platform_msg("threads"),
}

# ---- Telegram ----
def send_telegram(text):
    tok  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        log("Telegram token/chat_id yoxdur, mesajı keçdim.")
        return False
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            log(f"Telegram error: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        log(f"Telegram exception: {e}")
        return False

def parse_env_list(val, default=[]):
    if not val:
        return default
    parts = re.split(r"[,\|;/\n]+", val)
    return [p.strip() for p in parts if p.strip()]

if __name__ == "__main__":
    ACTIVE_PLATFORMS = parse_env_list(
        os.getenv("ACTIVE_PLATFORMS", "reddit,youtube,hackernews,producthunt,instagram,tiktok,threads").lower()
    )
    KEYWORDS = parse_env_list(os.getenv("KEYWORDS", "startup;saaS;ai"))
    try:
        DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "50"))
    except:
        DAILY_LIMIT = 50

    tok_ok = "OK" if os.getenv("TELEGRAM_BOT_TOKEN") else "MISSING"
    cid_ok = "OK" if os.getenv("TELEGRAM_CHAT_ID") else "MISSING"
    log(f"ENV check: TOK={tok_ok} CID={cid_ok} PLATFORMS={ACTIVE_PLATFORMS} KW={','.join(KEYWORDS)} LIMIT={DAILY_LIMIT}")

    # ➜ BOT START PING (həmişə göndərməyə cəhd edir)
    send_telegram("🔔 Bot işə düşdü. Axtarış başlayır…")

    total_msgs = 0
    total_found = 0

    for kw in KEYWORDS:
        kw = kw.strip()
        if not kw:
            continue
        for plat in ACTIVE_PLATFORMS:
            fn = PLATFORM_FUNCS.get(plat)
            if not fn:
                log(f"{plat}: funksiya tapılmadı, atlandı.")
                continue

            try:
                items = fn(kw, max_items=DAILY_LIMIT)
            except TypeError:
                items = fn(kw)
            except Exception as e:
                log(f"{plat}: axtarış xətası -> {e}")
                items = []

            log(f"{plat}: {len(items)} nəticə toplandı.")
            total_found += len(items)

            if not items:
                continue

            lines = [f"🔎 <b>{plat}</b> • <i>{html.escape(kw)}</i>"]
            for it in items[: min(10, DAILY_LIMIT)]:
                title = clean_text(it.get("title", ""), 120)
                url = it.get("url", "")
                if title and url:
                    lines.append(f"• <a href=\"{html.escape(url)}\">{html.escape(title)}</a>")
            msg = "\n".join(lines)

            if send_telegram(msg):
                total_msgs += 1
                time.sleep(0.5)

    # ➜ XÜLASƏ – həmişə göndər
    summary = f"✅ Bitdi. Tapılan nəticə: {total_found}. Göndərilən TG mesajı: {total_msgs}."
    if total_found == 0:
        summary += " Heç nə tapılmadısa, ehtimal: açar sözlər dar seçilib və ya platformalarda uyğun RSS yoxdur."
    send_telegram(summary)
    log(summary)
