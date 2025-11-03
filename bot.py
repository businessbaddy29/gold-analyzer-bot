# bot.py
import os
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from telegram import Bot, ParseMode
from telegram.utils.request import Request
from dotenv import load_dotenv

load_dotenv()

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Telegram bot token
ADMIN_IDS = os.getenv("ADMIN_IDS", "")  # comma separated telegram user ids, e.g. "12345678,9876543"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@Worlds_Support")  # admin mentions
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")  # e.g. https://gold-analyzer-bot.onrender.com
# Azure/OpenAI envs
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")  # e.g. https://samee-mhjab3yd-eastus2.cognitiveservices.azure.com/
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")  # e.g. gpt-4o-mini
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")

# ---------- basic checks ----------
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in env")

ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip().isdigit()]
IMAGEDIR = os.path.join(os.getcwd(), "images")
os.makedirs(IMAGEDIR, exist_ok=True)

# ---------- openai client setup (for Azure) ----------
import openai
if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT:
    openai.api_type = "azure"
    openai.api_base = AZURE_OPENAI_ENDPOINT.rstrip("/")
    openai.api_version = AZURE_OPENAI_API_VERSION
    openai.api_key = AZURE_OPENAI_KEY
else:
    # We'll still run; analysis will be simulated if Azure not configured
    print("Azure OpenAI not fully configured — running simulated analysis.")

# ---------- telegram bot ----------
request_conf = Request(connect_timeout=5.0, read_timeout=5.0)
bot = Bot(token=BOT_TOKEN, request=request_conf)
app = Flask(__name__)

# Simple in-memory activation store (for persistence later you can move to DB/file)
ACTIVE_USERS = set()  # chat_ids who are active
PENDING_QUEUE = {}  # chat_id -> list of filenames (if multiple)

# ---------- helper functions ----------
def send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN):
    try:
        bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        print("Error send_message:", e)

def save_photo_file(file_id, chat_id):
    try:
        f = bot.get_file(file_id)
        ts = int(datetime.utcnow().timestamp())
        filename = f"{chat_id}_{ts}.jpg"
        local_path = os.path.join(IMAGEDIR, filename)
        f.download(custom_path=local_path)
        print("Saved image:", local_path)
        return filename, local_path
    except Exception as e:
        print("Error save_photo_file:", e)
        return None, None

def analyze_image_background(chat_id, filename, local_path):
    """
    Background worker for processing the image.
    Right now does a simulated analysis. Replace inside with real Azure vision / model calls.
    """
    try:
        # update user: we already sent "saved" message. Now say processing:
        send_message(chat_id, "🔎 Processing your screenshot — I'll send results here when ready.")
        # Simulate processing time
        time.sleep(3)

        # If Azure configured, call it here. Example: use openai.ChatCompletion.create with a prompt referencing the image.
        result_text = None
        if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT:
            # Try a simple chat completion referencing the filename (if you host image publicly you can pass URL).
            # NOTE: To analyze image properly you'll need a model/deployment that supports vision; 
            # if not available this will just produce text.
            try:
                prompt = (
                    "You are a trading screenshot analyzer. The user uploaded an image file named "
                    f"'{filename}'. Provide a short simulated analysis: Trend, Signal, Support, Resistance.\n"
                    "If you cannot access the image, reply that this is a simulated result."
                )
                resp = openai.ChatCompletion.create(
                    engine=AZURE_OPENAI_DEPLOYMENT,
                    messages=[
                        {"role": "system", "content": "You are an assistant that analyzes trading screenshots."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=300
                )
                result_text = resp["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print("Azure call error:", e)
                result_text = None

        if not result_text:
            # fallback simulated analysis
            result_text = (
                "✅ *Analysis complete (simulated).* \n\n"
                "*Signal:* No strong confluence found.\n"
                "*Trend:* Neutral\n"
                "*Support:* 2045\n"
                "*Resistance:* 4120\n"
                "_Note: OpenAI image integration not enabled yet._"
            )
        # send final result
        send_message(chat_id, result_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print("Error in analyze_image_background:", e)
        send_message(chat_id, "⚠️ Error during processing. Try again later.")

# ---------- routes ----------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    # Basic handling: messages with text, photos
    try:
        # message may be in update['message']
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})
        chat_id = msg["chat"]["id"]
        user = msg["from"]
        user_name = (user.get("username") and "@" + user["username"]) or f'{user.get("first_name","")}'
        # handle text commands
        if "text" in msg:
            text = msg["text"].strip()
            # /start
            if text.startswith("/start"):
                welcome = (
                    f"Welcome {user_name}!\n\n"
                    "Upload your chart screenshot and then send /analyze (if active).\n\n"
                    "Commands:\n"
                    "/analyze - run analysis if your access is active\n"
                    "/status - check your access\n"
                )
                send_message(chat_id, welcome)
                return jsonify({"ok": True})
            if text.startswith("/status"):
                if chat_id in ACTIVE_USERS:
                    send_message(chat_id, "✅ Your access is *active*.", parse_mode=ParseMode.MARKDOWN)
                else:
                    send_message(chat_id, f"🔒 Your access is *inactive*. Contact admin {ADMIN_USERNAME}.", parse_mode=ParseMode.MARKDOWN)
                return jsonify({"ok": True})
            # admin-only activations: /activate <chat_id>
            if text.startswith("/activate") and user.get("id") in ADMIN_IDS:
                parts = text.split()
                if len(parts) >= 2:
                    target = parts[1]
                    try:
                        target_id = int(target)
                        ACTIVE_USERS.add(target_id)
                        send_message(target_id, "✅ Your access has been *activated* by admin.", parse_mode=ParseMode.MARKDOWN)
                        send_message(chat_id, f"Activated {target_id}")
                    except:
                        send_message(chat_id, "Usage: /activate <chat_id>")
                else:
                    send_message(chat_id, "Usage: /activate <chat_id>")
                return jsonify({"ok": True})
            # other text: if /analyze, only if active
            if text.startswith("/analyze"):
                if chat_id not in ACTIVE_USERS:
                    send_message(chat_id, f"🔒 Your access is inactive. Contact admin {ADMIN_USERNAME}.")
                    return jsonify({"ok": True})
                # If there is pending file -> process
                files = PENDING_QUEUE.get(chat_id)
                if not files:
                    send_message(chat_id, "Upload a screenshot first. Then send /analyze.")
                    return jsonify({"ok": True})
                # process each file
                for fname in files:
                    local_path = os.path.join(IMAGEDIR, fname)
                    # process in background
                    threading.Thread(target=analyze_image_background, args=(chat_id, fname, local_path), daemon=True).start()
                # clear queue
                PENDING_QUEUE.pop(chat_id, None)
                send_message(chat_id, "Processing started — you'll receive results here when ready.")
                return jsonify({"ok": True})
            # default text response
            send_message(chat_id, "I didn't understand. Upload screenshot and send /analyze, or use /start.")
            return jsonify({"ok": True})

        # handle photo
        if "photo" in msg:
            # Save largest photo
            photos = msg["photo"]
            largest = photos[-1]
            file_id = largest["file_id"]
            filename, local_path = save_photo_file(file_id, msg["chat"]["id"])
            if not filename:
                send_message(chat_id, "⚠️ Could not save the photo. Try again.")
                return jsonify({"ok": True})

            # Put in pending queue
            PENDING_QUEUE.setdefault(chat_id, []).append(filename)

            # Reply immediate messages
            send_message(chat_id, "📸 Screenshot saved! Processing will continue in background — you will receive results here shortly.")
            send_message(chat_id, "🔎 Processing your screenshot — I'll send results here when ready.")
            # start background processing automatically only if user active
            if chat_id in ACTIVE_USERS:
                threading.Thread(target=analyze_image_background, args=(chat_id, filename, local_path), daemon=True).start()
            else:
                # if not active, inform with admin contact
                send_message(chat_id, f"🔒 Your access is inactive. Contact admin {ADMIN_USERNAME}.")
            return jsonify({"ok": True})

    except Exception as e:
        print("Webhook handling error:", e)
    return jsonify({"ok": True})

# optional: serve saved images (only if you configured static serving and RENDER_EXTERNAL_URL mapping)
@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMAGEDIR, filename)

# ---------- startup ----------
if __name__ == "__main__":
    # port will be provided by Render via $PORT normally
    port = int(os.environ.get("PORT", "10000"))
    print("Starting Flask app on port", port)
    app.run(host="0.0.0.0", port=port)
