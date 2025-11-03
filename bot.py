# bot.py
import os
import json
import threading
import time
import logging
from pathlib import Path
from flask import Flask, request, jsonify
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gold-analyzer-bot")

# Environment variables (must exist in Render environment)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
BASE_URL = os.environ.get("BASE_URL")  # e.g. https://your-app.onrender.com
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")  # e.g. https://...cognitiveservices.azure.com/
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT")  # e.g. gpt-4o-mini
AZURE_OPENAI_KEY = os.environ.get("AZURE_OPENAI_KEY")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
ADMIN_IDS = os.environ.get("ADMIN_IDS", "")  # comma separated numeric chat ids
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "@admin")
ACTIVE_USERS_FILE = "active_users.json"

if not BOT_TOKEN or not BASE_URL or not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_DEPLOYMENT or not AZURE_OPENAI_KEY:
    logger.error("Missing required environment variables. Make sure BOT_TOKEN, BASE_URL, AZURE_OPENAI_... vars are set.")
    # don't exit - Flask will start but webhook won't be set

# Ensure storage folder for images
IMAGES_DIR = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)

# Active users persistence
def load_active_users():
    try:
        if Path(ACTIVE_USERS_FILE).exists():
            with open(ACTIVE_USERS_FILE, "r") as f:
                return set(json.load(f))
    except Exception as e:
        logger.exception("Failed to load active users:", e)
    return set()

def save_active_users(s):
    try:
        with open(ACTIVE_USERS_FILE, "w") as f:
            json.dump(list(s), f)
    except Exception as e:
        logger.exception("Failed to save active users:", e)

active_users = load_active_users()

# Parse admin ids
admins = set()
for part in ADMIN_IDS.split(","):
    part = part.strip()
    if part:
        try:
            admins.add(int(part))
        except:
            logger.warning("Invalid ADMIN_IDS entry: %s", part)


# Telegram helpers
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
def tg_send_message(chat_id, text, parse_mode=None, reply_to_message_id=None):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=15)
    try:
        r.raise_for_status()
    except Exception:
        logger.exception("tg_send_message failed: %s", r.text if r is not None else "no resp")
    return r.json()

def tg_set_webhook():
    if not BOT_TOKEN or not BASE_URL:
        logger.error("Cannot set webhook: BOT_TOKEN or BASE_URL missing.")
        return
    webhook_url = f"{BASE_URL}/{BOT_TOKEN}"
    r = requests.get(f"{TG_API}/setWebhook", params={"url": webhook_url}, timeout=20)
    try:
        r.raise_for_status()
        logger.info("setWebhook result: %s", r.json())
    except Exception:
        logger.exception("Failed to set webhook: %s", r.text if r is not None else "no resp")

# Azure OpenAI call
def azure_chat_completion(system_prompt, user_prompt, max_tokens=400):
    endpoint = AZURE_OPENAI_ENDPOINT.rstrip("/") + f"/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions"
    params = {"api-version": AZURE_OPENAI_API_VERSION}
    headers = {"api-key": AZURE_OPENAI_KEY, "Content-Type": "application/json"}
    data = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2
    }
    try:
        resp = requests.post(endpoint, params=params, headers=headers, json=data, timeout=30)
        resp.raise_for_status()
        j = resp.json()
        # The structure may be different across versions; try common result paths:
        # prefer choices[0].message.content
        text = None
        if "choices" in j and len(j["choices"])>0 and "message" in j["choices"][0]:
            text = j["choices"][0]["message"].get("content")
        elif "choices" in j and len(j["choices"])>0 and "text" in j["choices"][0]:
            text = j["choices"][0].get("text")
        else:
            text = str(j)
        return text
    except Exception:
        logger.exception("Azure OpenAI call failed")
        return None

# Image analysis worker (background)
def analyze_image_worker(chat_id, image_path):
    """Save immediate confirmation then call Azure to analyze (note: model won't truly parse image file bytes
       unless using a vision-enabled model; here we pass a structured prompt describing the task).
       Replace with real vision integration when available."""
    try:
        # Give user a little delay simulation (or heavy processing time)
        time.sleep(1)

        # System prompt: instruct the model to act as gold chart analyzer
        system_prompt = (
            "You are a helpful, concise gold price chart analyst. The user uploaded a chart image (path provided). "
            "If you cannot access image content, explain you can't and provide a short checklist the user can copy for a manual analysis "
            "(trend direction, support, resistance, candle pattern, buy/sell bias). Keep it short and in bullet points."
        )

        user_prompt = (
            f"I have saved the uploaded image on the server at: {image_path}\n\n"
            "Please (1) try to analyze the gold price chart (trend/support/resistance/entry confluence) and (2) if you don't actually have image pixels, "
            "explicitly mention that this is a simulated analysis and provide a short actionable checklist for the user to perform a manual check."
        )

        # Notify that analysis running (if desired we already sent earlier confirmations)
        # Call Azure OpenAI
        result = azure_chat_completion(system_prompt, user_prompt, max_tokens=350)
        if not result:
            result = "Sorry, analysis failed — the server couldn't reach the model. Contact admin."

        # Send final message
        tg_send_message(chat_id, result)
    except Exception:
        logger.exception("analyze_image_worker error")
        tg_send_message(chat_id, "Analysis failed due to internal error. Contact admin.")


# Flask app
app = Flask(__name__)

@app.route("/")
def health():
    return jsonify({"status":"ok","note":"bot webhook running"}), 200

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Receive update from Telegram and process"""
    try:
        update = request.get_json(force=True)
        logger.info("Received update: %s", update and update.get("message", update))
        message = update.get("message") or update.get("edited_message")
        if not message:
            return jsonify({"ok":True})

        chat = message["chat"]
        chat_id = chat["id"]
        text = message.get("text", "")
        username = message.get("from", {}).get("username") or message.get("from", {}).get("first_name")

        # handle /start
        if text and text.strip().startswith("/start"):
            welcome = f"Welcome {username or ''}!\nUpload your chart screenshot and then send /analyze (if you are active).\nIf your access is inactive, message admin: {ADMIN_USERNAME}"
            tg_send_message(chat_id, welcome)
            return jsonify({"ok":True})

        # admin commands
        if text and text.startswith("/activate"):
            # only allow admins
            if int(chat_id) not in admins and (message.get("from",{}).get("id") not in admins):
                tg_send_message(chat_id, "Only admin can activate users.")
                return jsonify({"ok":True})
            parts = text.split()
            if len(parts) < 2:
                tg_send_message(chat_id, "Usage: /activate <chat_id>")
                return jsonify({"ok":True})
            try:
                target = int(parts[1])
                active_users.add(target)
                save_active_users(active_users)
                tg_send_message(chat_id, f"Activated {target}")
            except:
                tg_send_message(chat_id, "Invalid chat id.")
            return jsonify({"ok":True})

        if text and text.startswith("/deactivate"):
            if int(chat_id) not in admins and (message.get("from",{}).get("id") not in admins):
                tg_send_message(chat_id, "Only admin can deactivate users.")
                return jsonify({"ok":True})
            parts = text.split()
            if len(parts) < 2:
                tg_send_message(chat_id, "Usage: /deactivate <chat_id>")
                return jsonify({"ok":True})
            try:
                target = int(parts[1])
                active_users.discard(target)
                save_active_users(active_users)
                tg_send_message(chat_id, f"Deactivated {target}")
            except:
                tg_send_message(chat_id, "Invalid chat id.")
            return jsonify({"ok":True})

        if text and text.startswith("/list_active"):
            if int(chat_id) not in admins and (message.get("from",{}).get("id") not in admins):
                tg_send_message(chat_id, "Only admin can list active users.")
                return jsonify({"ok":True})
            if not active_users:
                tg_send_message(chat_id, "No active users.")
            else:
                tg_send_message(chat_id, "Active users:\n" + "\n".join(map(str, active_users)))
            return jsonify({"ok":True})

        # /analyze command - user requests analysis of previously uploaded image
        if text and text.strip().startswith("/analyze"):
            if chat_id not in active_users:
                tg_send_message(chat_id, f"Your access is inactive. Contact admin: {ADMIN_USERNAME}")
                return jsonify({"ok":True})
            # find latest image file for this user
            user_files = sorted(IMAGES_DIR.glob(f"{chat_id}_*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not user_files:
                tg_send_message(chat_id, "No screenshot found. Please upload your screenshot first.")
                return jsonify({"ok":True})
            image_path = str(user_files[0])
            tg_send_message(chat_id, "Processing your screenshot — I'll send results here when ready.")
            t = threading.Thread(target=analyze_image_worker, args=(chat_id, image_path), daemon=True)
            t.start()
            return jsonify({"ok":True})

        # photo handler
        if "photo" in message:
            # Telegram sends multiple sizes; pick largest
            photos = message["photo"]
            file_id = photos[-1]["file_id"]
            # get file path from Telegram
            file_info = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=15).json()
            file_path = file_info.get("result", {}).get("file_path")
            if not file_path:
                tg_send_message(chat_id, "Failed to get file from Telegram.")
                return jsonify({"ok":True})
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            local_filename = IMAGES_DIR / f"{chat_id}_{int(time.time())}.jpg"
            try:
                r = requests.get(file_url, stream=True, timeout=30)
                r.raise_for_status()
                with open(local_filename, "wb") as f:
                    for chunk in r.iter_content(1024):
                        f.write(chunk)
                # confirm to user
                tg_send_message(chat_id, "📸 Screenshot saved! Processing will continue in background — you will receive results here shortly.")
                tg_send_message(chat_id, "🔎 Processing your screenshot — I'll send results here when ready.")
                # start background analysis thread (only if user active)
                if chat_id in active_users:
                    t = threading.Thread(target=analyze_image_worker, args=(chat_id, str(local_filename)), daemon=True)
                    t.start()
                else:
                    tg_send_message(chat_id, f"✅ Screenshot saved! Use /analyze to run analysis (if active).\nYour access is inactive. Contact admin: {ADMIN_USERNAME}")
            except Exception:
                logger.exception("Failed to download/save photo")
                tg_send_message(chat_id, "Failed to download the photo. Try again.")
            return jsonify({"ok":True})

        # fallback - echo or help
        if text:
            if chat_id in active_users:
                tg_send_message(chat_id, "Send a screenshot of the chart, then /analyze. Or if you need help, use /start.")
            else:
                tg_send_message(chat_id, f"Your access is inactive. Contact admin: {ADMIN_USERNAME}")
            return jsonify({"ok":True})

    except Exception:
        logger.exception("Error in webhook handler")
    return jsonify({"ok":True})


# set webhook at startup (in a small delay to let server start)
def start_set_webhook_delayed():
    time.sleep(1.5)
    try:
        tg_set_webhook()
    except Exception:
        logger.exception("set webhook failed")

if __name__ == "__main__":
    # start webhook setter thread
    threading.Thread(target=start_set_webhook_delayed, daemon=True).start()
    # If running standalone (locally) which is useful for testing:
    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting Flask on port %s", port)
    app.run(host="0.0.0.0", port=port)
