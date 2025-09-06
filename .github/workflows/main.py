import os
import requests
import telegram
from datetime import datetime

# Secrets (GitHub → Repository secrets)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
KEYWORDS = os.getenv("KEYWORDS", "test, demo").split(",")
ACTIVE_PLATFORMS = os.getenv("ACTIVE_PLATFORMS", "reddit,youtube").split(",")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "20"))

# Telegram bot
bot = telegram.Bot(token=TELEGRAM_TOKEN)

def fetch_reddit(keyword, limit=5):
    """Reddit-də keyword axtarır və nəticələri qaytarır"""
    url = f"https://www.reddit.com/search.json?q={keyword}&limit={limit}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        results = []
        for post in data["data"]["children"]:
            results.append({
                "platform": "reddit",
                "topic": keyword,
                "username": post["data"].get("author"),
                "profile_link": f"https://reddit.com/u/{post['data'].get('author')}",
                "content_url": "https://reddit.com" + post["data"].get("permalink")
            })
        return results
    except Exception as e:
        print(f"Reddit error: {e}")
        return []

def send_to_telegram(leads):
    """Toplanmış nəticələri Teleqrama göndərir"""
    if not leads:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚠ Heç bir nəticə tapılmadı.")
        return

    message = "📊 Yeni nəticələr:\n\n"
    for lead in leads[:20]:  # yalnız top 20 göndəririk
        message += (
            f"🔹 Platforma: {lead['platform']}\n"
            f"👤 User: {lead['username']}\n"
            f"🔗 Profil: {lead['profile_link']}\n"
            f"📌 Content: {lead['content_url']}\n\n"
        )

    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

def run_bot():
    all_leads = []
    for keyword in KEYWORDS:
        keyword = keyword.strip()
        if "reddit" in ACTIVE_PLATFORMS:
            all_leads.extend(fetch_reddit(keyword, limit=DAILY_LIMIT))

    # burda əlavə olaraq digər platformaları qoşa bilərik (YouTube, Twitter və s.)

    # nəticələri Teleqrama at
    send_to_telegram(all_leads)

    print(f"[{datetime.now()}] {len(all_leads)} nəticə tapıldı və Teleqrama göndərildi.")

if __name__ == "__main__":
    run_bot()
