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
    if not text:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# Kupon alma fonksiyonu (senin verdiğin requests kodundan dönüştürüldü)
async def get_coupon():
    headers = {
        "Accept": "*/*",
        "Accept-Language": "tr,en;q=0.9,en-GB;q=0.8,en-US;q=0.7",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Origin": "https://tiklagelsin.game.core.tiklaeslestir.zuzzuu.com",
        "Referer": "https://tiklagelsin.game.core.tiklaeslestir.zuzzuu.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
        "sec-ch-ua": '"Chromium";v="136", "Microsoft Edge";v="136", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"'
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
        json_data = response.json()
        reward = json_data.get("reward_info", {}).get("reward", {})
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
            "messages": []
        }

    if last_msg:
        user_data[uid]["messages"].append({
            "text": last_msg,
            "date": datetime.utcnow().isoformat()
        })

    hak_kaldi = max_rights - user_data[uid]["daily_count"]
    if hak_kaldi <= 0:
        await update.message.reply_text(f"👋 {first_name}, günlük hakkın doldu! ({max_rights} kupon)")
        save_user_data(user_data)
        return

    cekilecek = min(hak_kaldi, 5)  # En fazla 5 kupon çekecek

    await update.message.reply_text(f"Merhaba {first_name}! {cekilecek} kupon hakkın var, çekiliyor...")

    kuponlar = []
    basari = 0
    for _ in range(cekilecek):
        result = await get_coupon()
        if result:
            basari += 1
            kuponlar.append(result)
            user_data[uid]["daily_count"] += 1
            user_data[uid]["total_count"] += 1
        else:
            break

    if basari == 0:
        await update.message.reply_text("❌ Hiç kupon alınamadı veya limit dolmuş olabilir.")
    else:
        # Tüm kuponları tek mesajda topluca gönderiyoruz
        await update.message.reply_text("🎉 Kuponlar:\n" + "\n".join(kuponlar))

    save_user_data(user_data)

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

async def loglar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut sadece adminler içindir.")
        return

    lines = []
    for uid, info in user_data.items():
        lines.append(f"🆔 {uid} | 👤 {info.get('first_name')} {info.get('last_name')} | @{info.get('username')}")
        last_messages = info.get("messages", [])[-3:]
        for msg in last_messages:
            text_esc = escape_markdown(msg.get("text", ""))
            date = msg.get("date", "")
            lines.append(f"  - [{date}] {text_esc}")
        lines.append("")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(devamı var)"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def istatistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut sadece adminler içindir.")
        return
    toplam_kullanici = len(user_data)
    toplam_mesaj = sum(len(u.get("messages", [])) for u in user_data.values())
    await update.message.reply_text(f"📊 Toplam kullanıcı: {toplam_kullanici}\n💬 Toplam mesaj sayısı: {toplam_mesaj}")

async def aktif_kullanicilar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("Bu komut sadece adminler içindir.")
        return
    aktif = [u for u in user_data.values() if len(u.get("messages", [])) > 0]
    await update.message.reply_text(f"🔍 Mesaj atmış aktif kullanıcı sayısı: {len(aktif)}")

def reset_daily_counts():
    for uid in user_data:
        user_data[uid]["daily_count"] = 0
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
    app.add_handler(CommandHandler("aktifkullanicilar", aktif_kullanicilar))

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), log_message))

    app.run_polling()
