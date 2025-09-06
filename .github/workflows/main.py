import os
import json
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ACTIVE_PLATFORMS = [p.strip().lower() for p in os.getenv("ACTIVE_PLATFORMS", "").split(",") if p.strip()]
KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()]
try:
    DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "20"))
except ValueError:
    DAILY_LIMIT = 20

def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN və/və ya TELEGRAM_CHAT_ID təyin edilməyib.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=15)
    ok = r.ok and r.json().get("ok", False)
    if not ok:
        print("Telegram cavabı:", r.text)
    return ok

def main():
    # Burada real axtarış funksiyalarını tədricən əlavə edəcəksən.
    # İndi isə yalnız test mesajı göndərək ki, workflow problemsiz işləsin.
    summary = {
        "time": datetime.utcnow().isoformat() + "Z",
        "active_platforms": ACTIVE_PLATFORMS,
        "keywords": KEYWORDS,
        "daily_limit": DAILY_LIMIT,
        "status": "ok"
    }
    text = "✅ Bot işləyir.\n\n" + "```json\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n```"
    send_telegram(text)

if __name__ == "__main__":
    main()
