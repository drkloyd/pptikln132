import os
import logging
import asyncio
import sqlite3
from pathlib import Path
from datetime import datetime
from uuid import uuid4

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from aiohttp import web

# --- Yapılandırma ve Kurulum ---
load_dotenv()

# Logger kurulumu
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Ortam değişkenleri
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
COUPON_URL = os.getenv("COUPON_URL", "https://tiklagelsin.game.api.zuzzuu.com/request_from_game/event_create/Mz2Ex38cykBsH6GjhpZ5fX7KJdSaet4nLFDAWQ9U")
PRIORITY_USERS = set(os.getenv("PRIORITY_USERS", "").split(","))
BANNED_USERNAMES = set(os.getenv("BANNED_USERNAMES", "").split(","))

MAX_NORMAL = 5
MAX_PRIORITY = 20

# Kalıcı disk üzerine veritabanı yolu
DATA_PATH = Path("data")
DB_FILE = DATA_PATH / "users.db"


def init_db():
    """Veritabanını ve tabloları oluşturur."""
    DATA_PATH.mkdir(exist_ok=True)
    with sqlite3.connect(DB_FILE) as con:
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                daily_count INTEGER DEFAULT 0,
                total_count INTEGER DEFAULT 0,
                used_start BOOLEAN DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                username TEXT,
                activity TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.commit()
    logger.info("Veritabanı başarıyla başlatıldı.")


async def get_or_create_user(user_id: str, username: str, first_name: str):
    """Veritabanından kullanıcıyı getirir veya oluşturur."""
    with sqlite3.connect(DB_FILE) as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        db_user = cur.fetchone()
        if not db_user:
            cur.execute(
                "INSERT INTO users (id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username, first_name)
            )
            con.commit()
            logger.info(f"Yeni kullanıcı eklendi: {username} ({user_id})")
            return {"id": user_id, "daily_count": 0, "used_start": False}
        return {"id": db_user[0], "daily_count": db_user[3], "used_start": bool(db_user[5])}


async def get_coupon() -> str | None:
    """Asenkron olarak kupon kodunu alır."""
    headers = {
        "Accept": "*/*",
        "Accept-Language": "tr,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://tiklagelsin.game.core.tiklaeslestir.zuzzuu.com",
        "Referer": "https://tiklagelsin.game.core.tiklaeslestir.zuzzuu.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    }
    data = {
        "game_name": "tikla-eslestir",
        "event_name": "oyun_tamamlandi",
        "user_id": "",
        "session_id": str(uuid4()),
        "user_segment": "",
        "user_name": ""
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(COUPON_URL, headers=headers, json=data)
            response.raise_for_status()
            reward_data = response.json().get("reward_info", {}).get("reward", {})
            coupon = reward_data.get("coupon_code")
            reward_name = reward_data.get("campaign_name", "Bilinmeyen Ödül")
            if coupon:
                return f"🎁 Kupon: `{coupon}` | Ödül: {reward_name}"
        except httpx.HTTPStatusError as e:
            logger.error(f"Kupon API'si hata döndü: Status {e.response.status_code}, Response: {e.response.text}")
        except Exception as e:
            logger.error(f"Kupon alınırken beklenmedik bir hata oluştu: {e}")
    return None


def reset_daily_counts():
    """Tüm kullanıcıların günlük kupon hakkını sıfırlar."""
    with sqlite3.connect(DB_FILE) as con:
        con.execute("UPDATE users SET daily_count = 0, used_start = 0")
        con.commit()
    logger.info(f"[{datetime.now()}] Günlük haklar başarıyla sıfırlandı.")


# --- Telegram Handler'ları ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    username = user.username or f"id_{user_id}"

    with sqlite3.connect(DB_FILE) as con:
        con.execute(
            "INSERT INTO activity_log (user_id, username, activity) VALUES (?, ?, ?)",
            (user_id, username, "/start komutu")
        )

    if username in BANNED_USERNAMES:
        await update.message.reply_text("🚫 Bu botu kullanmanız yasaklanmıştır.")
        return

    db_user = await get_or_create_user(user_id, username, user.first_name)
    if db_user.get("used_start", False):
        await update.message.reply_text("🛑 Bugünlük kuponlarını zaten aldın. Yarın tekrar deneyebilirsin.")
        return

    max_hak = MAX_PRIORITY if username in PRIORITY_USERS else MAX_NORMAL
    kalan_hak = max_hak - db_user.get("daily_count", 0)
    if kalan_hak <= 0:
        await update.message.reply_text(f"🚫 Günlük kupon limitin doldu! ({max_hak} hak)")
        return

    await update.message.reply_text(f"👋 Merhaba {user.first_name}, senin için {kalan_hak} adet kupon alınıyor...")
    tasks = [get_coupon() for _ in range(kalan_hak)]
    results = await asyncio.gather(*tasks)
    kuponlar = [res for res in results if res]

    if kuponlar:
        kupon_sayisi = len(kuponlar)
        message = "🎉 İşte kuponların:\n\n" + "\n".join(kuponlar)
        await update.message.reply_markdown(message)
        with sqlite3.connect(DB_FILE) as con:
            con.execute(
                "UPDATE users SET daily_count = daily_count + ?, total_count = total_count + ?, used_start = 1 WHERE id = ?",
                (kupon_sayisi, kupon_sayisi, user_id)
            )
    else:
        await update.message.reply_text("❌ Maalesef şu an kupon alınamadı. Lütfen daha sonra tekrar dene.")

async def log_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gelen normal mesajları loglar."""
    user = update.effective_user
    user_id = str(user.id)
    username = user.username or f"id_{user_id}"
    text = update.message.text
    
    with sqlite3.connect(DB_FILE) as con:
        con.execute(
            "INSERT INTO activity_log (user_id, username, activity) VALUES (?, ?, ?)",
            (user_id, username, f'Mesaj: "{text[:50]}"') # Mesajın ilk 50 karakterini logla
        )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin için istatistikleri gösterir."""
    if str(update.effective_user.id) != ADMIN_ID: return
    with sqlite3.connect(DB_FILE) as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(id), SUM(total_count) FROM users")
        user_count, total_coupons = cur.fetchone()
    await update.message.reply_text(
        f"📊 **Bot İstatistikleri**\n\n"
        f"👤 Toplam Kullanıcı: {user_count or 0}\n"
        f"🎟️ Toplam Alınan Kupon: {total_coupons or 0}"
    )

async def loglar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin için son aktiviteleri listeler."""
    if str(update.effective_user.id) != ADMIN_ID: return
    message_lines = ["📝 **Son 30 Bot Aktivitesi**\n"]
    with sqlite3.connect(DB_FILE) as con:
        cur = con.cursor()
        cur.execute("SELECT timestamp, username, activity FROM activity_log ORDER BY id DESC LIMIT 30")
        logs = cur.fetchall()
    if not logs:
        await update.message.reply_text("Henüz görüntülenecek bir aktivite kaydı yok.")
        return
    for log_entry in logs:
        timestamp = datetime.strptime(log_entry[0], '%Y-%m-%d %H:%M:%S').strftime('%d-%m %H:%M')
        username = log_entry[1] or "bilinmiyor"
        activity = log_entry[2]
        message_lines.append(f"`[{timestamp}]` @{username} - {activity}")
    await update.message.reply_text("\n".join(message_lines), parse_mode="Markdown")

# --- Ana Çalıştırma ve Web Sunucusu ---

async def health_check(request):
    """Render'ın uygulamanın canlı olup olmadığını kontrol etmesi için."""
    return web.Response(text="OK", status=200)

async def main():
    """Botu ve web sunucusunu başlatır."""
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()
    
    # Komut handler'ları
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("istatistik", stats))
    application.add_handler(CommandHandler("loglar", loglar))
    
    # Normal mesajları loglamak için MessageHandler
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), log_text_message))

    scheduler = AsyncIOScheduler(timezone="Europe/Istanbul")
    scheduler.add_job(reset_daily_counts, "cron", hour=0, minute=5)

    app = web.Application()
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 10000)))

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    scheduler.start()
    await site.start()

    logger.info("Bot ve web sunucusu başarıyla başlatıldı!")
    await asyncio.Event().wait()

    await application.updater.stop()
    await application.stop()
    await runner.cleanup()
    scheduler.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot kapatılıyor.")
