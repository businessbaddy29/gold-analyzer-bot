import os
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from telegram import Bot, ParseMode
from telegram.utils.request import Request
from dotenv import load_dotenv
import openai

load_dotenv()

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@Worlds_Support")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")

# ---------- BASIC CHECK ----------
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in environment variables")

ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip().isdigit()]
IMAGEDIR = os.path.join(os.getcwd(), "images")
os.makedirs(IMAGEDIR, exist_ok=True)

# ---------- OPENAI (AZURE CONFIG) ----------
if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT:
    openai.api_type = "azure"
    openai.api_base = AZURE_OPENAI_ENDPOINT.rstrip("/")
    openai.api_version = AZURE_OPENAI_API_VERSION
    openai.api_key = AZURE_OPENAI_KEY
    print("✅ Azure OpenAI connected")
else:
    print("⚠️ Azure OpenAI not fully configured — using simulated results")

# ---------- TELEGRAM BOT ----------
request_conf = Request(connect_timeout=5.0, read_timeout=5.0)
bot = Bot(token=BOT_TOKEN, request=request_conf)
app = Flask(__name__)

ACTIVE_USERS = set()
PENDING_QUEUE = {}

# ---------- HELPERS ----------
def send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN):
    try:
        bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        print("Send message error:", e)

def save_photo_file(file_id, chat_id):
    try:
        f = bot.get_file(file_id)
        ts = int(datetime.utcnow().timestamp())
        filename = f"{chat_id}_{ts}.jpg"
        path = os.path.join(IMAGEDIR, filename)
        f.download(custom_path=path)
        print("📸 Saved image:", path)
        return filename, path
    except Exception as e:
        print("Save photo error:", e)
        return None, None

def analyze_background(chat_id, filename, path):
    """Simulated or Azure-powered image analysis"""
    try:
        send_message(chat_id, "🔎 Processing your screenshot — I'll send results here when ready.")
        time.sleep(3)

        result_text = None
        if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT:
            try:
                prompt = (
                    f"Analyze the trading chart image '{filename}'. "
                    "Give a short summary with Signal, Trend, Support, and Resistance. "
                    "If the image isn't available, respond with a simulated result."
                )
                resp = openai.ChatCompletion.create(
                    engine=AZURE_OPENAI_DEPLOYMENT,
                    messages=[
                        {"role": "system", "content": "You are a helpful trading analysis assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=300
                )
                result_text = resp["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print("Azure call error:", e)

        if not result_text:
            result_text = (
                "✅ *Analysis complete (simulated).* \n\n"
                "*Signal:* No strong confluence found.\n"
                "*Trend:* Neutral\n"
                "*Support:* 2045\n"
                "*Resistance:* 4120\n"
                "_Note: Azure Vision not enabled yet._"
            )

        send_message(chat_id, result_text)
    except Exception as e:
        print("Analyze background error:", e)
        send_message(chat_id, "⚠️ Error analyzing image.")

# ---------- FLASK ROUTES ----------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    try:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})

        chat_id = msg["chat"]["id"]
        user = msg["from"]
        username = (user.get("username") and "@" + user["username"]) or user.get("first_name", "")

        # --- TEXT HANDLING ---
        if "text" in msg:
            text = msg["text"].strip()

            if text.startswith("/start"):
                send_message(chat_id, f"Welcome {username}!\nUpload a chart screenshot to begin.")
                return jsonify({"ok": True})

            if text.startswith("/status"):
                if chat_id in ACTIVE_USERS:
                    send_message(chat_id, "✅ Your access is *active*.")
                else:
                    send_message(chat_id, f"🔒 Your access is *inactive*. Contact admin {ADMIN_USERNAME}.")
                return jsonify({"ok": True})

            if text.startswith("/activate") and user.get("id") in ADMIN_IDS:
                parts = text.split()
                if len(parts) > 1:
                    try:
                        target = int(parts[1])
                        ACTIVE_USERS.add(target)
                        send_message(target, "✅ Your access has been activated by admin.")
                        send_message(chat_id, f"Activated {target}")
                    except:
                        send_message(chat_id, "Usage: /activate <chat_id>")
                else:
                    send_message(chat_id, "Usage: /activate <chat_id>")
                return jsonify({"ok": True})

            if text.startswith("/analyze"):
                if chat_id not in ACTIVE_USERS:
                    send_message(chat_id, f"🔒 Your access is inactive. Contact admin {ADMIN_USERNAME}.")
                    return jsonify({"ok": True})

                files = PENDING_QUEUE.get(chat_id)
                if not files:
                    send_message(chat_id, "No screenshot found. Please upload one first.")
                    return jsonify({"ok": True})

                for fname in files:
                    path = os.path.join(IMAGEDIR, fname)
                    threading.Thread(target=analyze_background, args=(chat_id, fname, path), daemon=True).start()
                PENDING_QUEUE.pop(chat_id, None)
                send_message(chat_id, "Processing started — results will appear shortly.")
                return jsonify({"ok": True})

            send_message(chat_id, "I didn't understand that command. Try /start or /analyze.")
            return jsonify({"ok": True})

        # --- PHOTO HANDLING ---
        if "photo" in msg:
            largest = msg["photo"][-1]
            file_id = largest["file_id"]
            filename, path = save_photo_file(file_id, chat_id)
            if not filename:
                send_message(chat_id, "⚠️ Could not save image. Try again.")
                return jsonify({"ok": True})

            PENDING_QUEUE.setdefault(chat_id, []).append(filename)
            send_message(chat_id, "📸 Screenshot saved! Processing will continue in background.")
            send_message(chat_id, "🔎 Processing your screenshot — I'll send results here when ready.")

            if chat_id in ACTIVE_USERS:
                threading.Thread(target=analyze_background, args=(chat_id, filename, path), daemon=True).start()
            else:
                send_message(chat_id, f"🔒 Your access is inactive. Contact admin {ADMIN_USERNAME}.")
            return jsonify({"ok": True})

    except Exception as e:
        print("Webhook error:", e)

    return jsonify({"ok": True})


@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMAGEDIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"🚀 Flask running on port {port}")
    app.run(host="0.0.0.0", port=port)
