# bot.py
import os
import sqlite3
import datetime
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "Worlds_Support")  # without @

DB_FILE = os.path.join(os.path.dirname(__file__), "users.db")

# ---------- Health server for Render (keeps web service happy) ----------
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

# ---------- DB helpers ----------
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
    cur.execute("INSERT OR IGNORE INTO users(chat_id, username) VALUES (?, ?)", (chat_id, username))
    cur.execute("UPDATE users SET username=? WHERE chat_id=?", (username, chat_id))
    conn.commit(); conn.close()

def set_last_image(chat_id, image_path):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_image=? WHERE chat_id=?", (image_path, chat_id))
    conn.commit(); conn.close()

def activate_user(chat_id, days=30):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.datetime.utcnow()
    expires = now + datetime.timedelta(days=int(days))
    cur.execute("INSERT OR IGNORE INTO users(chat_id, username) VALUES (?, ?)", (chat_id, "unknown"))
    cur.execute("UPDATE users SET is_active=1, activated_at=?, expires_at=? WHERE chat_id=?", (now.isoformat(), expires.isoformat(), chat_id))
    conn.commit(); conn.close()
    return expires

def deactivate_user(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active=0, activated_at=NULL, expires_at=NULL WHERE chat_id=?", (chat_id,))
    conn.commit(); conn.close()

def get_active_users():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, username, expires_at FROM users WHERE is_active=1")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_user(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, username, is_active, activated_at, expires_at, last_image FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    conn.close()
    return row

# ---------- Telegram handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    username = user.username or f"{user.first_name or ''} {user.last_name or ''}".strip()
    upsert_user(chat_id, username)
    msg = f"👋 Welcome {username}!\n\nUpload your chart screenshot and then send /analyze (if active)."
    await update.message.reply_text(msg)

async def echo_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    os.makedirs("images", exist_ok=True)
    file_path = f"images/{chat_id}_{int(datetime.datetime.utcnow().timestamp())}.jpg"
    await file.download_to_drive(file_path)
    set_last_image(chat_id, file_path)
    await update.message.reply_text("📸 Screenshot saved! Use /analyze to run analysis (if active).")

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    row = get_user(chat_id)
    if not row:
        await update.message.reply_text("You are not registered. Send /start first.")
        return
    is_active = row[2]
    last_image = row[5]
    if not is_active:
        # clickable admin mention: @username
        admin_link = f"@{ADMIN_USERNAME}"
        msg = (
            f"Your access is inactive. Contact admin {admin_link} to activate.\n\n"
            "Steps:\n"
            "1) Message the admin and send your chat id (send `/myid` to get it).\n"
            "2) Admin will activate your account after payment.\n\n"
            "To get your chat id, send /myid"
        )
        await update.message.reply_text(msg)
        return
    if not last_image:
        await update.message.reply_text("No screenshot found. Upload one first.")
        return

    # Placeholder analysis — replace with real OpenAI Vision integration later
    await update.message.reply_text(f"📊 Analysis placeholder for image: {last_image}\n(Integration with OpenAI Vision coming next).")

# small helper for user to get their chat id
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Your chat id: {chat_id}")

# ---------- Admin commands ----------
def is_admin(user_id):
    return user_id in ADMIN_IDS

async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if not is_admin(caller):
        await update.message.reply_text("You are not authorized to use this command.")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: /activate <chat_id> [days]. Example: /activate 123456789 30")
        return
    target = int(args[0])
    days = int(args[1]) if len(args) >= 2 else 30
    expires = activate_user(target, days)
    # Notify admin
    await update.message.reply_text(f"Activated {target} until {expires.isoformat()}")
    # Notify user
    try:
        await context.bot.send_message(chat_id=target, text=f"✅ Your account has been activated by admin until {expires.date().isoformat()}. You can now use /analyze.")
    except Exception as e:
        await update.message.reply_text(f"Note: could not send message to user {target}. They might not have started the bot. ({e})")

async def cmd_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if not is_admin(caller):
        await update.message.reply_text("You are not authorized to use this command.")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: /deactivate <chat_id>")
        return
    target = int(args[0])
    deactivate_user(target)
    await update.message.reply_text(f"Deactivated {target}")
    try:
        await context.bot.send_message(chat_id=target, text="⚠️ Your account has been deactivated by admin.")
    except:
        pass

async def cmd_list_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if not is_admin(caller):
        await update.message.reply_text("You are not authorized to use this command.")
        return
    rows = get_active_users()
    if not rows:
        await update.message.reply_text("No active users currently.")
        return
    lines = ["Active users:"]
    for chat_id, username, expires_at in rows:
        expires = expires_at.split("T")[0] if expires_at else "N/A"
        lines.append(f"- {username or 'unknown'} ({chat_id}) — expires {expires}")
    await update.message.reply_text("\n".join(lines))

# alias
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_list_active(update, context)

# ---------- main ----------
def main():
    init_db()
    # start health server in background
    Thread(target=run_health_server, daemon=True).start()

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not set in environment")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # user commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(MessageHandler(filters.PHOTO, echo_image))
    app.add_handler(CommandHandler("analyze", analyze))
    # admin
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("deactivate", cmd_deactivate))
    app.add_handler(CommandHandler("list_active", cmd_list_active))
    app.add_handler(CommandHandler("stats", cmd_stats))

    print("🤖 Bot started successfully — webhook active!")
webhook_url = f"https://gold-analyzer-bot.onrender.com/{BOT_TOKEN}"
app.run_webhook(
    listen="0.0.0.0",
    port=int(os.getenv("PORT", 8000)),
    url_path=BOT_TOKEN,
    webhook_url=webhook_url
)

if __name__ == "__main__":
    main()
