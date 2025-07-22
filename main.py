import os
import json
import logging
import uuid
import re
import requests
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
)
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
COUPON_URL = os.getenv("COUPON_URL")
USER_DATA_FILE = os.getenv("USER_DATA_FILE", "kullanici_sayac.json")
priority_users = set(os.getenv("PRIORITY_USERS", "").split(","))

MAX_NORMAL = 5
MAX_PRIORITY = 20

def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_user_data(data):
    with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

user_data = load_user_data()

def escape_markdown(text):
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text or "")

async def get_coupon():
    headers = {
        "Accept": "*/*",
        "Accept-Language": "tr,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://tiklagelsin.game.core.tiklaeslestir.zuzzuu.com",
        "Referer": "https://tiklagelsin.game.core.tiklaeslestir.zuzzuu.com/",
        "User-Agent": "Mozilla/5.0"
    }

    data = {
        "game_name": "tikla-eslestir",
        "event_name": "oyun_tamamlandi",
        "user_id": "",
        "session_id": str(uuid.uuid4()),
        "user_segment": "",
        "user_name": ""
    }

    try:
        response = requests.post(COUPON_URL, headers=headers, data=json.dumps(data), timeout=10)
        response.raise_for_status()
        reward = response.json().get("reward_info", {}).get("reward", {})
        coupon = reward.get("coupon_code")
        reward_name = reward.get("campaign_name", "Belirtilmemiş Ödül")
        if coupon:
            return f"🎁 Kupon: {coupon} | Ödül: {reward_name}"
    except Exception as e:
        logging.error(f"Kupon alınırken hata: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    username = user.username or f"id_{uid}"
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    lang = user.language_code or ""
    last_msg = update.message.text or ""

    max_hak = MAX_PRIORITY if username in priority_users else MAX_NORMAL

    if uid not in user_data:
        user_data[uid] = {
            "id": uid,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": lang,
            "daily_count": 0,
            "total_count": 0,
            "messages": [],
            "used_start": False
        }

    user_data[uid]["messages"].append({
        "text": last_msg,
        "date": datetime.utcnow().isoformat()
    })

    if user_data[uid].get("used_start", False):
        await update.message.reply_text("🛑 /start komutu zaten kullanıldı. Yarın tekrar deneyebilirsin.")
        save_user_data(user_data)
        return

    kalan = max_hak - user_data[uid]["daily_count"]
    if kalan <= 0:
        await update.message.reply_text(f"🚫 Günlük limit doldu! ({max_hak} kupon hakkı)")
        user_data[uid]["used_start"] = True
        save_user_data(user_data)
        return

    await update.message.reply_text(f"👋 Merhaba {first_name}, {kalan} kupon çekiliyor...")

    kuponlar = []
    for _ in range(kalan):
        result = await get_coupon()
        if result:
            kuponlar.append(result)
            user_data[uid]["daily_count"] += 1
            user_data[uid]["total_count"] += 1
        else:
            break

    if kuponlar:
        await update.message.reply_text("🎉 Kuponlar:" + "\n".join(kuponlar))
    else:
        await update.message.reply_text("❌ Kupon alınamadı.")

    user_data[uid]["used_start"] = True
    save_user_data(user_data)

async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)

    if uid not in user_data:
        user_data[uid] = {
            "id": uid,
            "username": user.username or f"id_{uid}",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "language_code": user.language_code or "",
            "daily_count": 0,
            "total_count": 0,
            "messages": [],
            "used_start": False
        }

    user_data[uid]["messages"].append({
        "text": update.message.text or "",
        "date": datetime.utcnow().isoformat()
    })

    save_user_data(user_data)

async def loglar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut sadece adminler içindir.")
        return

    lines = []
    for uid, info in user_data.items():
        lines.append(f"🆔 {uid} | 👤 {info.get('first_name')} {info.get('last_name')} | @{info.get('username')}")
        for msg in info.get("messages", []):
            date = msg.get("date", "")
            text = escape_markdown(msg.get("text", ""))
            lines.append(f"  - [{date}] {text}")
        lines.append("")

    text = "\n".join(lines)

    if not text.strip():
        await update.message.reply_text("📭 Henüz log verisi yok.")
        return

    await update.message.reply_text(text[:4000], parse_mode=ParseMode.MARKDOWN)

async def istatistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut sadece adminler içindir.")
        return

    toplam_kullanici = len(user_data)
    toplam_mesaj = sum(len(u.get("messages", [])) for u in user_data.values())
    await update.message.reply_text(f"📊 Toplam kullanıcı: {toplam_kullanici}\n💬 Toplam mesaj: {toplam_mesaj}")

def reset_daily_counts():
    for uid in user_data:
        user_data[uid]["daily_count"] = 0
        user_data[uid]["used_start"] = False
    save_user_data(user_data)
    print(f"[{datetime.now()}] Günlük haklar sıfırlandı.")

scheduler = BackgroundScheduler(timezone="Europe/Istanbul")
scheduler.add_job(reset_daily_counts, "cron", hour=10, minute=10)
scheduler.start()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("loglar", loglar))
    app.add_handler(CommandHandler("istatistik", istatistik))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), log_message))

    app.run_polling()
