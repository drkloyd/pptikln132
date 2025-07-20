import os
import json
import logging
import uuid
import re
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (ApplicationBuilder, CommandHandler,
                          ContextTypes, MessageHandler, filters)
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
url = os.getenv("COUPON_URL")
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
    if not text:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


async def get_coupon():
    headers = {
        "Content-Type": "application/json",
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
        response = requests.post(url, headers=headers, data=json.dumps(data))
        json_data = response.json()
        reward = json_data["reward_info"]["reward"]
        coupon = reward.get("coupon_code")
        campaign = reward.get("campaign_name", "Bilinmeyen Ödül")
        return f" 🎁 Kupon:🎁 {coupon} | Ödül: {campaign}" if coupon else None
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
    last_msg = update.message.text if update.message else ""

    max_rights = MAX_PRIORITY if username in priority_users else MAX_NORMAL

    if uid not in user_data:
        user_data[uid] = {
            "id": uid,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": lang,
            "daily_count": 0,
            "total_count": 0,
            "messages": [last_msg] if last_msg else []
        }
    else:
        user_data[uid].update({
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": lang,
        })
        if last_msg:
            user_data[uid].setdefault("messages", []).append(last_msg)

    hak_kaldi = max_rights - user_data[uid]["daily_count"]
    if hak_kaldi <= 0:
        await update.message.reply_text(f"👋 {first_name}, günlük hakkın doldu! ({max_rights} kupon)")
        save_user_data(user_data)
        return

    await update.message.reply_text(f"🎯 {hak_kaldi} kupon çekiliyor...")

    basari = 0
    for _ in range(hak_kaldi):
        result = await get_coupon()
        if result:
            basari += 1
            user_data[uid]["daily_count"] += 1
            user_data[uid]["total_count"] += 1
            save_user_data(user_data)
            await update.message.reply_text(result)
        else:
            await update.message.reply_text("❌ Kupon alınamadı. Limit dolmuş olabilir veya sunucu problemi var.")
            break

    if basari == 0:
        await update.message.reply_text("❌ Hiç kupon alınamadı.")
    else:
        await update.message.reply_text(f"✅ Toplam {basari} kupon başarıyla alındı.")


async def loglar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut yalnızca admin tarafından kullanılabilir.")
        return
    log_text = "\n".join([
        f"🆔 {d['id']} | 👤 {d['first_name']} {d['last_name']} | @{d['username']}\nMesajlar: {d.get('messages', [])}\n" for d in user_data.values()
    ])
    await update.message.reply_text(log_text[:4000])


async def aktif_kullanicilar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut yalnızca admin tarafından kullanılabilir.")
        return
    aktif = [u for u in user_data.values() if u['daily_count'] > 0]
    text = f"🔍 Bugün kupon çeken {len(aktif)} aktif kullanıcı var."
    await update.message.reply_text(text)


async def istatistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut yalnızca admin tarafından kullanılabilir.")
        return
    toplam_kullanici = len(user_data)
    toplam_kupon = sum(u["total_count"] for u in user_data.values())
    text = f"📊 Toplam Kullanıcı: {toplam_kullanici}\n🎟️ Toplam Kupon Alımı: {toplam_kupon}"
    await update.message.reply_text(text)


def reset_daily_counts():
    for uid in user_data:
        user_data[uid]["daily_count"] = 0
    save_user_data(user_data)
    print(f"[{datetime.now()}] ✅ Günlük haklar sıfırlandı.")


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

    app.run_polling()
