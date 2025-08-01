import os
import logging
import asyncio
import sqlite3
from pathlib import Path
from datetime import datetime
from uuid import uuid4

import httpx  # requests yerine
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler # Asenkron uyumlu scheduler
from dotenv import load_dotenv
from aiohttp import web # Asenkron web sunucusu

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
COUPON_URL = os.getenv("COUPON_URL")
PRIORITY_USERS = set(os.getenv("PRIORITY_USERS", "").split(","))
BANNED_USERNAMES = set(os.getenv("BANNED_USERNAMES", "").split(","))

MAX_NORMAL = 5
MAX_PRIORITY = 20

# Kalıcı disk üzerine veritabanı yolu (Render için)
# DATA_PATH = Path(os.getenv("RENDER_DISK_MOUNT_PATH", "/app/data")) <-- ESKİ SATIR
DATA_PATH = Path("data") # ⬅️ YENİ SATIR: Bu, projenin ana klasöründeki 'data' klasörünü işaret eder.
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
        con.commit()
    logger.info("Veritabanı başarıyla başlatıldı.")

async def get_or_create_user(user_id: str, username: str, first_name: str):
    """Veritabanından kullanıcıyı getirir veya oluşturur."""
    async with httpx.AsyncClient() as client: # Using async context for db operations in future if needed
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
        "Content-Type": "application/json",
        "Origin": "https://tiklagelsin.game.core.tiklaeslestir.zuzzuu.com",
        "Referer": "https://tiklagelsin.game.core.tiklaeslestir.zuzzuu.com/",
        "User-Agent": "Mozilla/5.0"
    }
    data = {"session_id": str(uuid4())}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(COUPON_URL, headers=headers, json=data)
            response.raise_for_status()
            reward_data = response.json().get("reward_info", {}).get("reward", {})
            coupon = reward_data.get("coupon_code")
            reward_name = reward_data.get("campaign_name", "Bilinmeyen Ödül")
            if coupon:
                return f"🎁 Kupon: `{coupon}` | Ödül: {reward_name}"
        except httpx.RequestError as e:
            logger.error(f"Kupon API'sine ulaşılamadı: {e}")
        except Exception as e:
            logger.error(f"Kupon alınırken beklenmedik bir hata oluştu: {e}")
    return None

def reset_daily_counts():
    """Tüm kullanıcıların günlük kupon hakkını sıfırlar."""
    with sqlite3.connect(DB_FILE) as con:
        cur = con.cursor()
        cur.execute("UPDATE users SET daily_count = 0, used_start = 0")
        con.commit()
    logger.info(f"[{datetime.now()}] Günlük haklar başarıyla sıfırlandı.")


# --- Telegram Komut Handler'ları ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    username = user.username or f"id_{user_id}"
    
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
        
        # Veritabanını güncelle
        with sqlite3.connect(DB_FILE) as con:
            cur = con.cursor()
            cur.execute(
                "UPDATE users SET daily_count = daily_count + ?, total_count = total_count + ?, used_start = 1 WHERE id = ?",
                (kupon_sayisi, kupon_sayisi, user_id)
            )
            con.commit()
    else:
        await update.message.reply_text("❌ Maalesef şu an kupon alınamadı. Lütfen daha sonra tekrar dene.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin için istatistikleri gösterir."""
    if str(update.effective_user.id) != ADMIN_ID:
        return
    
    with sqlite3.connect(DB_FILE) as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(id), SUM(total_count) FROM users")
        user_count, total_coupons = cur.fetchone()
    
    await update.message.reply_text(
        f"📊 **Bot İstatistikleri**\n\n"
        f"👤 Toplam Kullanıcı: {user_count or 0}\n"
        f"🎟️ Toplam Alınan Kupon: {total_coupons or 0}"
    )

async def health_check(request):
    """Render'ın uygulamanın canlı olup olmadığını kontrol etmesi için."""
    logger.info("Health check endpoint'i cagirildi.")
    return web.Response(text="OK", status=200)

async def main():
    """Botu ve web sunucusunu başlatır."""
    init_db() # Veritabanını hazırla

    # Telegram Botunu kur
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("istatistik", stats))

    # Zamanlayıcıyı kur (günlük sıfırlama için)
    scheduler = AsyncIOScheduler(timezone="Europe/Istanbul")
    scheduler.add_job(reset_daily_counts, "cron", hour=0, minute=5)
    
    # Aiohttp web sunucusunu kur (health check için)
    app = web.Application()
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 10000)))

    # Her şeyi birlikte başlat
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    scheduler.start()
    await site.start()
    
    logger.info("Bot ve web sunucusu başarıyla başlatıldı!")
    
    # Uygulama kapatılana kadar çalışır
    await asyncio.Event().wait()
    
    # Kapanış işlemleri
    await application.updater.stop()
    await application.stop()
    await runner.cleanup()
    scheduler.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot kapatılıyor.")
