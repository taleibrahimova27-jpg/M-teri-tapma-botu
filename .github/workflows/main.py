import os
import time
import json
import random
import logging
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------
# ENV & Konfiq
# ---------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SPREADSHEET_ID = os.getenv("SHEETS_SPREADSHEET_ID", "")
SHEETS_TAB = os.getenv("SHEETS_TAB", "leads")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "250"))         # bu run üçün maksimum neçə lead yazılsın
TOP_N_TELEGRAM = int(os.getenv("TOP_N_TELEGRAM", "20"))    # Telegrama yalnız top N gedir
ACTIVE_PLATFORMS = [x.strip().lower() for x in os.getenv("ACTIVE_PLATFORMS", "hackernews,reddit,youtube,instagram,tiktok").split(",") if x.strip()]
KEYWORDS = [x.strip() for x in os.getenv("KEYWORDS", "sale,iphone,ai").split(",") if x.strip()]

# RSSHub bazaları (429 olanda rotasiya + backoff üçün)
RSSHUB_BASES = [
    os.getenv("RSSHUB_BASE", "https://rsshub.app").rstrip("/"),
    "https://rsshub.rssforever.com",
    "https://rsshub.moeyy.cn"
]

# Hər platforma üçün RSSHub path-ları.
# Səndə hansılar işləyirdisə elə həminlər qalır. (PH aktiv deyilsə, sadəcə `ACTIVE_PLATFORMS`-a yazma)
PLATFORM_PATHS = {
    "hackernews": lambda kw: f"/hackernews/keyword/{kw}",
    "reddit":     lambda kw: f"/reddit/search/{kw}",
    "youtube":    lambda kw: f"/youtube/search/{kw}",
    "instagram":  lambda kw: f"/instagram/tag/{kw}",
    "tiktok":     lambda kw: f"/tiktok/keyword/{kw}",
    # "producthunt": lambda kw: f"/producthunt/topics/{kw}",  # istəsən sonra aktivləşdirərsən
}

# Sheet üçün tələb olunan başlıqlar (sənin dediyin sırada)
SHEET_HEADERS = [
    "platform", "topic", "username", "profile_url",
    "dm_url", "content_url", "intent_score", "ts", "uid"
]

# ---------------------------
# Util: Telegram
# ---------------------------
def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=20)
    except Exception:
        pass

# ---------------------------
# Util: Fetch with retries
# ---------------------------
def fetch_json(path_or_url: str, max_retries: int = 5):
    """
    429/5xx üçün exponential backoff + jitter + node rotasiyası.
    JSON qaytarmağa çalışır; JSON deyilsə str qaytara bilər (bu halda atlayacağıq).
    """
    if path_or_url.startswith("http"):
        candidates = [path_or_url]
    else:
        candidates = [f"{base}{path_or_url if path_or_url.startswith('/') else '/'+path_or_url}" for base in RSSHUB_BASES]

    last_err = None
    for attempt in range(1, max_retries + 1):
        url = candidates[(attempt - 1) % len(candidates)]
        try:
            resp = requests.get(url, headers={"User-Agent": "LeadBot/1.0"}, timeout=25)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} {resp.reason}")
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return None
        except Exception as e:
            last_err = e
            sleep_s = min(60, 1.5 * (2 ** (attempt - 1))) + random.uniform(0, 1.5)
            logging.warning(f"Fetch fail {attempt}/{max_retries} {url}: {e}. sleep {sleep_s:.1f}s")
            time.sleep(sleep_s)
    raise RuntimeError(f"Fetch failed after {max_retries} tries. Last error: {last_err}")

# ---------------------------
# Sheets
# ---------------------------
def get_sheets_service():
    # Workflow saxi: /tmp/sa.json-a yazırıq; GOOGLE_APPLICATION_CREDENTIALS də oraya göstərilib
    sa_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    creds = service_account.Credentials.from_service_account_file(sa_path, scopes=[
        "https://www.googleapis.com/auth/spreadsheets"
    ])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def sheet_ensure_headers(service):
    """Sətir-1 başlıqları yoxla, boşdursa yaz."""
    rng = f"{SHEETS_TAB}!A1:I1"
    try:
        res = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=rng).execute()
        values = res.get("values", [])
        if not values or values[0] != SHEET_HEADERS:
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=rng,
                valueInputOption="RAW",
                body={"values": [SHEET_HEADERS]},
            ).execute()
    except Exception as e:
        logging.error(f"sheet_ensure_headers error: {e}")

def sheet_append_rows(service, rows: List[List[Any]]):
    if not rows: 
        return 0
    rng = f"{SHEETS_TAB}!A2"
    body = {"values": rows}
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=rng,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()
    return len(rows)

# ---------------------------
# Scoring & Normalization
# ---------------------------
def intent_score(title: str, topic: str) -> int:
    """Sadə skor: topic sözü varsa + yüksək; 'buy', 'need', 'help', 'idea' və s. sözlər əlavə bal verir."""
    t = (title or "").lower()
    score = 0
    if topic.lower() in t:
        score += 50
    for w in ["buy", "need", "looking", "how", "help", "sell", "sale", "idea", "problem", "beta", "launch"]:
        if w in t:
            score += 10
    return min(100, score)

def uid_from(url: str) -> str:
    base = (url or "") + str(datetime.utcnow().timestamp())
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:16]

def normalize_items(platform: str, topic: str, payload) -> List[Dict[str, Any]]:
    """
    RSSHub JSON fərqli ola bilər. Ən geniş yayılmış format: {"items": [{"title","link","author"}...]}
    Burada ondan istifadə edirik. Tapılmasa, boş qaytarırıq.
    """
    out = []
    if not payload:
        return out
    items = payload.get("items") if isinstance(payload, dict) else None
    if not items:
        return out

    for it in items:
        title = it.get("title") or ""
        link = it.get("link") or ""
        author = ""
        # bəzi feedlərdə 'author' obyekt, bəzilərində str olur
        if isinstance(it.get("author"), dict):
            author = it["author"].get("name", "")
        elif isinstance(it.get("author"), str):
            author = it["author"]

        profile_url = ""
        dm_url = ""
        username = author or ""

        # platform spesifik sadə mapping (istəsən sonra zənginləşdirərik)
        if platform in ("instagram", "tiktok"):
            # çox vaxt author username olur
            profile_url = f"https://www.{platform}.com/{username}" if username else ""
        elif platform == "youtube":
            # link video linkidir; profile üçün sadə placeholder
            profile_url = ""
        elif platform == "reddit":
            # redditdə tez-tez author "u/..." olur
            if username and not username.startswith("http"):
                profile_url = f"https://reddit.com/user/{username.replace('u/','')}"
        elif platform == "hackernews":
            profile_url = ""

        row = {
            "platform": platform,
            "topic": topic,
            "username": username,
            "profile_url": profile_url,
            "dm_url": dm_url,
            "content_url": link,
            "intent_score": intent_score(title, topic),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "uid": uid_from(link or title),
        }
        out.append(row)
    return out

# ---------------------------
# Main crawl
# ---------------------------
def crawl() -> Dict[str, Any]:
    total_rows: List[List[Any]] = []
    top_candidates: List[Dict[str, Any]] = []
    errors: List[str] = []

    for platform in ACTIVE_PLATFORMS:
        if platform not in PLATFORM_PATHS:
            # dəstəklənmir — atla (məs: producthunt-ı aktiv etməmisənsə)
            continue

        for kw in KEYWORDS:
            path = PLATFORM_PATHS[platform](kw)
            try:
                payload = fetch_json(path)
                items = normalize_items(platform, kw, payload)
                # limit (bu run üçün)
                for it in items:
                    # Sheets üçün sənin başlıqlarına uyğun sıraya çevir:
                    total_rows.append([
                        it["platform"], it["topic"], it["username"], it["profile_url"],
                        it["dm_url"], it["content_url"], it["intent_score"], it["ts"], it["uid"]
                    ])
                    top_candidates.append(it)
                    if len(total_rows) >= DAILY_LIMIT:
                        break
                # sorğular arasında jitter
                time.sleep(random.uniform(1.5, 3.0))
            except Exception as e:
                errors.append(f"{platform}/{kw} -> {e}")
            if len(total_rows) >= DAILY_LIMIT:
                break
        if len(total_rows) >= DAILY_LIMIT:
            break

    # Top N (intent_score-a görə)
    top_candidates.sort(key=lambda x: x.get("intent_score", 0), reverse=True)
    top_20 = top_candidates[:TOP_N_TELEGRAM]

    return {
        "rows": total_rows,
        "top": top_20,
        "errors": errors
    }

# ---------------------------
# Telegram format
# ---------------------------
def format_top_for_tg(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "Top nəticə yoxdur."
    lines = []
    for it in items:
        p = it["platform"]
        kw = it["topic"]
        sc = it["intent_score"]
        url = it["content_url"] or ""
        lines.append(f"• [{p}] ({kw})  score:{sc}\n🔗 {url}")
    return "\n\n".join(lines)

# ---------------------------
# ENTRY
# ---------------------------
def main():
    # Sheets servisini hazırla və başlıqları yoxla
    svc = get_sheets_service()
    sheet_ensure_headers(svc)

    result = crawl()

    # hamısını Sheet-ə yaz
    written = 0
    if result["rows"]:
        # birdən çox batch də ola bilər, amma burada hamısını bir dəfəyə göndərəcəyik
        written = sheet_append_rows(svc, result["rows"])

    # Telegram: top N
    if result["top"]:
        tg_send("🔎 Top nəticələr:\n\n" + format_top_for_tg(result["top"]))

    # Telegram: xülasə
    summary = f"🟢 Run tamamlandı.\nYazıldı (Sheets): {written}\nTop Telegram: {len(result['top'])}"
    if result["errors"]:
        preview = "\n".join("• " + e[:120] for e in result["errors"][:3])
        summary += f"\nℹ️ Xəta sayı: {len(result['errors'])}\n{preview}"
    tg_send(summary)

if __name__ == "__main__":
    main()
