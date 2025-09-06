import os, sys, time, urllib.parse
import requests
import feedparser
from bs4 import BeautifulSoup

# ---------- Helpers ----------
def env(name, default=""):
    v = os.getenv(name)
    return v if v is not None and v != "" else default

def split_list(s):
    if not s:
        return []
    # ; və , hər ikisini dəstəklə
    raw = [x.strip() for x in s.replace(";", ",").split(",")]
    return [x for x in raw if x]

def tg_send(token, chat_id, text):
    try:
        if not token or not chat_id:
            print("Telegram token/chat_id yoxdur, mesajı keçdim.")
            return False
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text}
        )
        if r.status_code != 200:
            print("Telegram error:", r.status_code, r.text[:200])
        return r.ok
    except Exception as e:
        print("Telegram exception:", e)
        return False

def fetch_rss(url, headers=None):
    try:
        if headers:
            res = requests.get(url, headers=headers, timeout=20)
            res.raise_for_status()
            return feedparser.parse(res.text)
        # feedparser özü yükləyə bilir, bəzən header lazım olmur
        return feedparser.parse(url)
    except Exception as e:
        print("RSS error:", url, e)
        return {"entries": []}

def clamp(n, lo, hi):
    return max(lo, min(hi, n))

# ---------- Env / Config ----------
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = env("TELEGRAM_CHAT_ID")
ACTIVE_PLATFORMS   = [p.lower() for p in split_list(env("ACTIVE_PLATFORMS"))]
KEYWORDS           = split_list(env("KEYWORDS"))
DAILY_LIMIT        = clamp(int(env("DAILY_LIMIT", "20") or 20), 1, 200)

print("ENV check:",
      "TOK=" + ("OK" if TELEGRAM_BOT_TOKEN else "MISSING"),
      "CID=" + ("OK" if TELEGRAM_CHAT_ID else "MISSING"),
      "PLATFORMS=" + ",".join(ACTIVE_PLATFORMS or ["(none)"]),
      "KW=" + ("/".join(KEYWORDS) if KEYWORDS else "(none)"),
      "LIMIT=" + str(DAILY_LIMIT))

if not ACTIVE_PLATFORMS:
    ACTIVE_PLATFORMS = ["reddit", "youtube", "hackernews", "producthunt"]

# ---------- Platform fetchers (RSS, API-siz) ----------

def search_reddit(keyword, max_items=50):
    q = urllib.parse.quote(keyword)
    url = f"https://www.reddit.com/search.rss?q={q}&sort=new"
    # Reddit bəzi hallarda UA istəyir
    headers = {"User-Agent": "Mozilla/5.0 (GitHubActions Bot)"}
    feed = fetch_rss(url, headers=headers)
    out = []
    for e in feed.get("entries", []):
        out.append(("reddit", e.get("title", "(no title)"), e.get("link")))
        if len(out) >= max_items: break
    return out

def search_youtube(keyword, max_items=50):
    q = urllib.parse.quote_plus(keyword)
    url = f"https://www.youtube.com/feeds/videos.xml?search_query={q}"
    feed = fetch_rss(url)
    out = []
    for e in feed.get("entries", []):
        link = e.get("link")
        title = e.get("title", "(no title)")
        out.append(("youtube", title, link))
        if len(out) >= max_items: break
    return out

def search_hackernews(keyword, max_items=50):
    # hnrss.org axtarışı dəstəkləyir
    q = urllib.parse.quote(keyword)
    url = f"https://hnrss.org/newest?q={q}"
    feed = fetch_rss(url)
    out = []
    for e in feed.get("entries", []):
        out.append(("hackernews", e.get("title", "(no title)"), e.get("link")))
        if len(out) >= max_items: break
    return out

def search_producthunt(keyword, max_items=50):
    # PH rəsmi axtarış RSS vermir; ümumi feed-i çəkib filtrləyirik
    url = "https://www.producthunt.com/feed"
    feed = fetch_rss(url)
    k = keyword.lower()
    out = []
    for e in feed.get("entries", []):
        title = e.get("title", "")
        if k in title.lower():
            out.append(("producthunt", title, e.get("link")))
            if len(out) >= max_items: break
    return out

def skip_platform_msg(name):
    msg = f"{name}: rəsmi RSS yoxdur, atlanır."
    print(msg)
    return []

PLATFORM_FUNCS = {
    "reddit": search_reddit,
    "youtube": search_youtube,
    "hackernews": search_hackernews,
    "producthunt": search_producthunt,
    # aşağıdakılar RSS-sizdir – skip
    "instagram": lambda kw, m=50: skip_platform_msg("instagram"),
    "tiktok":    lambda kw, m=50: skip_platform_msg("tiktok"),
    "threads":   lambda kw, m=50: skip_platform_msg("threads"),
}

# ---------- Run search ----------
all_results = []
for platform in ACTIVE_PLATFORMS:
    fn = PLATFORM_FUNCS.get(platform)
    if not fn:
        print(f"{platform}: tanınmır, keçdim.")
        continue

    found = []
    if KEYWORDS:
        for kw in KEYWORDS:
            items = fn(kw, max_items=DAILY_LIMIT)
            # “title + url” üzrə duplikatları aradan qaldıraq
            for t, title, link in [("kw", *x[1:]) for x in items]:
                key = (platform, title, link)
                if key not in [(p, ti, li) for p, ti, li in found]:
                    found.append((platform, title, link))
            # limitə çatdıqsa dayan
            if len(found) >= DAILY_LIMIT:
                break
    else:
        # keywords boşdursa – ümumi feed-dən götür (yalnız RSS olanlarda)
        if platform in ("reddit", "youtube", "hackernews", "producthunt"):
            found = fn("", max_items=DAILY_LIMIT)
        else:
            found = []

    print(f"{platform}: {len(found)} nəticə toplandı.")
    all_results.extend(found)

# Limit yekunda
all_results = all_results[:DAILY_LIMIT]
print("Cəmi nəticə:", len(all_results))

# ---------- Send to Telegram ----------
if all_results and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    lines = []
    for p, title, link in all_results:
        safe_title = BeautifulSoup(title, "html.parser").get_text(" ", strip=True)
        lines.append(f"• [{p}] {safe_title}\n{link}")
    message = "🔎 Tapılan leadlər:\n\n" + "\n\n".join(lines)
    tg_send(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
else:
    if not all_results:
        print("Göndəriləcək nəticə yoxdur.")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram məlumatları yoxdur.")

# ---------- Always: finish ping ----------
try:
    total_found = len(all_results)
except Exception:
    total_found = 0

tg_send(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        f"🟢 Run bitdi. Toplanan lead sayı: {total_found}")
