import os
import json
import logging
import uuid
import re
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
USER_DATA_FILE = os.getenv("USER_DATA_FILE", "kullanici_sayac.json")

MAX_NORMAL = 5
MAX_PRIORITY = 20

# Veriyi dosyadan yükle
def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# Veriyi dosyaya kaydet
def save_user_data(data):
    with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

user_data = load_user_data()

def escape_markdown(text):
    if not text:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    username = user.username or f"id_{uid}"
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    lang = user.language_code or ""
    last_msg = update.message.text if update.message else ""

    if uid not in user_data:
        user_data[uid] = {
            "id": uid,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": lang,
            "daily_count": 0,
            "total_count": 0,
            "messages": []
        }

    # Her start mesajını kaydet
    if last_msg:
        user_data[uid]["messages"].append({
            "text": last_msg,
            "date": datetime.utcnow().isoformat()
        })

    save_user_data(user_data)
    await update.message.reply_text(f"Merhaba {first_name}! Hoş geldin.")

# Her kullanıcıdan gelen mesajı kaydet
async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    username = user.username or f"id_{uid}"
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    lang = user.language_code or ""
    text = update.message.text or ""

    if uid not in user_data:
        user_data[uid] = {
            "id": uid,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": lang,
            "daily_count": 0,
            "total_count": 0,
            "messages": []
        }

    user_data[uid]["messages"].append({
        "text": text,
        "date": datetime.utcnow().isoformat()
    })
    save_user_data(user_data)

# /loglar komutu (sadece admin)
async def loglar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut sadece adminler içindir.")
        return

    lines = []
    for uid, info in user_data.items():
        lines.append(f"🆔 {uid} | 👤 {info.get('first_name')} {info.get('last_name')} | @{info.get('username')}")
        # Son 3 mesajı gösterelim
        last_messages = info.get("messages", [])[-3:]
        for msg in last_messages:
            text_esc = escape_markdown(msg.get("text", ""))
            date = msg.get("date", "")
            lines.append(f"  - [{date}] {text_esc}")
        lines.append("")  # boş satır

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(devamı var)"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# /istatistik komutu (admin)
async def istatistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut sadece adminler içindir.")
        return
    toplam_kullanici = len(user_data)
    toplam_mesaj = sum(len(u.get("messages", [])) for u in user_data.values())
    await update.message.reply_text(f"📊 Toplam kullanıcı: {toplam_kullanici}\n💬 Toplam mesaj sayısı: {toplam_mesaj}")

# /aktifkullanicilar komutu (admin)
async def aktif_kullanicilar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut sadece adminler içindir.")
        return
    aktif = [u for u in user_data.values() if len(u.get("messages", [])) > 0]
    await update.message.reply_text(f"🔍 Mesaj atmış aktif kullanıcı sayısı: {len(aktif)}")

# Günlük sayaç sıfırlama (eğer istersen)
def reset_daily_counts():
    for uid in user_data:
        user_data[uid]["daily_count"] = 0
    save_user_data(user_data)
    print(f"[{datetime.now()}] Günlük sayaclar sıfırlandı.")

scheduler = BackgroundScheduler(timezone="Europe/Istanbul")
scheduler.add_job(reset_daily_counts, "cron", hour=10, minute=10)
scheduler.start()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("loglar", loglar))
    app.add_handler(CommandHandler("istatistik", istatistik))
    app.add_handler(CommandHandler("aktifkullanicilar", aktif_kullanicilar))

    # Bütün gelen mesajları yakala ve logla
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), log_message))

    app.run_polling()
