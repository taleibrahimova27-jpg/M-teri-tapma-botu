# .github/workflows/main.py
import os, sys, json, time
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
ACTIVE    = os.getenv("ACTIVE_PLATFORMS", "")
KEYWORDS  = os.getenv("KEYWORDS", "")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "50"))

print(f"ENV check: TOK={'OK' if BOT_TOKEN else 'MISSING'} CID={'OK' if CHAT_ID else 'MISSING'} "
      f"PLATFORMS={ACTIVE} KW={KEYWORDS} LIMIT={DAILY_LIMIT}", flush=True)

def tg(text):
    if not (BOT_TOKEN and CHAT_ID):
        print("Telegram disabled: missing token/chat_id", flush=True); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
        )
        print(f"TG status: {r.status_code} | {r.text[:180]}", flush=True)
    except Exception as e:
        print(f"TG error: {e}", flush=True)

# PING — hər run-da bir dəfə göndərilir
tg("✅ Bot başladı (GitHub Actions)")

# --- BURADA SƏNİN TOPLAMA/AYIRMA KODUN GEDİR ---
# nümunə olaraq sadəcə log edirik
platforms = [p.strip() for p in ACTIVE.split(",") if p.strip()]
print("Aktiv platformalar:", platforms, flush=True)

# … buraya toplayıcıları çağırırsan …
# tapılan n nəticə üçün misal:
found_counts = {"reddit": 0, "youtube": 0, "hackernews": 0, "producthunt": 0, "instagram": 0, "tiktok": 0, "threads": 0}

summary_lines = [f"{k}: {v}" for k, v in found_counts.items() if k in platforms or not platforms]
summary = "Nəticə xülasəsi:\n" + "\n".join(summary_lines)
print(summary, flush=True)

# Bitdikdə də mütləq bildiriş
tg("✅ Bot bitdi.\n" + summary)
