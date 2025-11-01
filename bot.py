# add at top
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    port = int(os.getenv("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()

# before app.run_polling() start the health server in background
if __name__ == "__main__":
    Thread(target=run_health_server, daemon=True).start()
    main()  # your existing main() that runs the bot


import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from utils import upsert_user, set_last_image
import datetime

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    upsert_user(chat_id, username)
    await update.message.reply_text("👋 Welcome! Upload your chart screenshot and then type /analyze (if active).")

async def echo_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_path = f"images/{chat_id}_{datetime.datetime.now().timestamp()}.jpg"
    os.makedirs("images", exist_ok=True)
    await file.download_to_drive(file_path)
    set_last_image(chat_id, file_path)
    await update.message.reply_text("📸 Screenshot saved! Type /analyze to get analysis (if active).")

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Analysis feature coming next... (OpenAI Vision integration step pending)")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, echo_image))
    app.add_handler(CommandHandler("analyze", analyze))
    print("🤖 Bot started successfully — polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
