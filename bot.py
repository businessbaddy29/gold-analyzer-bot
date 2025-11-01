# bot.py
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# load env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# ---- Simple health HTTP server so Render sees an open port ----
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    port = int(os.getenv("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()

# ---- DB helper functions (minimal) ----
import sqlite3
DB_FILE = os.path.join(os.path.dirname(__file__), "users.db")
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            is_active INTEGER DEFAULT 0,
            activated_at TEXT,
            expires_at TEXT,
            last_image TEXT
        )
    """)
    conn.commit()
    conn.close()

def upsert_user(chat_id, username):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users(chat_id, username) VALUES(?, ?)", (chat_id, username))
    conn.commit(); conn.close()

def set_last_image(chat_id, image_path):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_image=? WHERE chat_id=?", (image_path, chat_id))
    conn.commit(); conn.close()

# ---- Telegram handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name
    upsert_user(chat_id, username)
    await update.message.reply_text("👋 Welcome! Upload your chart screenshot and then /analyze (if active).")

async def echo_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # save image
    photo = update.message.photo[-1]
    file = await photo.get_file()
    os.makedirs("images", exist_ok=True)
    file_path = f"images/{chat_id}_{int(datetime.datetime.utcnow().timestamp())}.jpg"
    await file.download_to_drive(file_path)
    set_last_image(chat_id, file_path)
    await update.message.reply_text("📸 Screenshot saved! Use /analyze to run analysis (if active).")

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT is_active, last_image FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await update.message.reply_text("You are not registered. Send /start first.")
        return
    is_active, last_image = row
    if not is_active:
        await update.message.reply_text("Your access is inactive. Contact admin.")
        return
    if not last_image:
        await update.message.reply_text("No screenshot found. Upload one first.")
        return
    # Placeholder response — we will integrate OpenAI vision later
    await update.message.reply_text(f"📊 Analysis placeholder.\nSaved image: {last_image}\n(Next step: OpenAI Vision integration)")

# ---- main that launches both health server and bot ----
def main():
    init_db()
    # Start health server in background thread so Render sees an open port
    Thread(target=run_health_server, daemon=True).start()

    # Build Telegram app
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not set in environment")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, echo_image))
    app.add_handler(CommandHandler("analyze", analyze))

    print("🤖 Bot started successfully — polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
