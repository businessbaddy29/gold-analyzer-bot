#!/usr/bin/env python3
"""
Bot for Telegram + Azure OpenAI (vision) integration.

Requirements (pip):
  pip install flask requests python-dotenv

Env vars (set on Render / locally):
  TELEGRAM_TOKEN            -> Telegram bot token (botXXXXXXXX:YYYY)
  BASE_URL                  -> Public URL of your service, e.g. https://gold-analyzer-bot.onrender.com
  AZURE_OPENAI_ENDPOINT     -> e.g. https://samee-mhjab3yd-eastus2.cognitiveservices.azure.com/
  AZURE_OPENAI_DEPLOYMENT   -> e.g. gpt-4o-mini
  AZURE_OPENAI_KEY          -> Azure OpenAI API key
"""

import os
import json
import base64
import threading
import time
from io import BytesIO

import requests
from flask import Flask, request, jsonify

# load env
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
BASE_URL = os.environ.get("BASE_URL")  # e.g. https://gold-analyzer-bot.onrender.com (no trailing slash)
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")  # e.g. https://...cognitiveservices.azure.com/
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
AZURE_OPENAI_KEY = os.environ.get("AZURE_OPENAI_KEY")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

if not all([TELEGRAM_TOKEN, BASE_URL, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT]):
    missing = [k for k in ["TELEGRAM_TOKEN","BASE_URL","AZURE_OPENAI_ENDPOINT","AZURE_OPENAI_KEY","AZURE_OPENAI_DEPLOYMENT"] if not os.environ.get(k)]
    raise SystemExit(f"Missing required env vars: {missing}")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

app = Flask(__name__)

# helper to send message to Telegram
def send_message(chat_id, text, reply_to_message_id=None):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    r = requests.post(url, json=payload, timeout=30)
    return r.ok, r.text

def send_photo(chat_id, photo_bytes, caption=None):
    url = f"{TELEGRAM_API}/sendPhoto"
    files = {"photo": ("image.jpg", photo_bytes)}
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    r = requests.post(url, data=data, files=files, timeout=60)
    return r.ok, r.text

# download Telegram file bytes by file_path
def download_telegram_file(file_path):
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    r = requests.get(file_url, timeout=30)
    r.raise_for_status()
    return r.content

# Get file path from Telegram getFile
def get_file_path(file_id):
    url = f"{TELEGRAM_API}/getFile"
    r = requests.get(url, params={"file_id": file_id}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError("getFile failed: " + json.dumps(j))
    return j["result"]["file_path"]

# Compose Azure request (we will include base64 data url)
def call_azure_image_analysis(image_bytes, prompt_text):
    """
    Send image + prompt to Azure OpenAI chat completions endpoint (deployment).
    We pass an 'input' like structure with an input_text and an input_image (base64 data URL).
    """
    # Build data URL (jpeg)
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    # Azure chat completions endpoint for deployment:
    # POST {endpoint}/openai/deployments/{deployment}/chat/completions?api-version={API_VERSION}
    endpoint = AZURE_OPENAI_ENDPOINT.rstrip("/") + f"/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version={API_VERSION}"

    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_OPENAI_KEY
    }

    # Payload — include image as part of content array following examples used in Azure docs (image as input)
    # NOTE: Azure flavors vary; this structure works with many image-enabled chat endpoints.
    payload = {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that analyzes gold trading charts. Give clear Trend, Support & Resistance, Candlestick pattern (if any), Entry confluence, SL, TP, Timeframes H1 and H4."},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt_text},
                    {"type": "input_image", "image_url": data_url}
                ]
            }
        ],
        "max_tokens": 800,
        "temperature": 0.1
    }

    # Make request
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
    # if status not OK, raise helpful info
    if resp.status_code != 200:
        # return full body for debugging
        return False, f"Azure returned status {resp.status_code}: {resp.text}"

    # Parse response — different Azure versions return slightly different shapes
    j = resp.json()

    # Common patterns: 'choices' with 'message' or 'output_text' field; or 'reply' etc.
    # We'll try several heuristics:
    text = None
    # 1) choices -> 0 -> message -> content -> parts / 0 / text
    try:
        if "choices" in j and len(j["choices"])>0:
            ch = j["choices"][0]
            if isinstance(ch.get("message"), dict):
                # message.content can be a string or list
                msg = ch["message"].get("content")
                if isinstance(msg, str):
                    text = msg
                elif isinstance(msg, list):
                    # find first text block
                    for part in msg:
                        if isinstance(part, str):
                            text = part
                            break
                        if isinstance(part, dict) and "text" in part:
                            text = part["text"]
                            break
            elif "message" in ch and isinstance(ch["message"], str):
                text = ch["message"]
            elif "text" in ch:
                text = ch["text"]
    except Exception:
        pass

    # 2) 'output_text' top-level
    if not text:
        if "output_text" in j:
            text = j["output_text"]

    # 3) 'response' -> 'output' etc
    if not text:
        # inspect nested fields quickly
        for k in ("response","result","output"):
            if k in j and isinstance(j[k], dict):
                for k2 in ("output_text","text"):
                    if k2 in j[k]:
                        text = j[k][k2]
                        break
                if text:
                    break

    # 4) fallback: dump the whole json as a string
    if not text:
        text = "No standard text found in Azure response. Raw JSON:\n" + json.dumps(j, indent=2)[:1800]

    return True, text

# Background worker for processing image to azure and responding to user
def process_screenshot_async(chat_id, message_id, image_bytes):
    try:
        # Step 1: send a processing message (edit or new)
        send_message(chat_id, "🔎 Processing your screenshot — I'll send results here when ready.", reply_to_message_id=message_id)

        # Step 2: create a concise prompt that instructs model for trading analysis
        prompt = (
            "Analyze the attached gold price chart screenshot (candlestick chart). "
            "Return a short structured analysis with sections: Trend, Support, Resistance, "
            "Candlestick pattern (if any), Entry confluence (why enter), Suggested entry price(s), "
            "Stop Loss (SL), Take Profit (TP), Timeframes checked (H1 and H4). "
            "Be concise and give numeric values if visible; if you can't determine values, say 'not visible'."
        )

        ok, azure_text = call_azure_image_analysis(image_bytes, prompt)
        if not ok:
            # azure returned error text
            send_message(chat_id, f"⚠️ Analysis failed: {azure_text}", reply_to_message_id=message_id)
            return

        # Send result back to user
        send_message(chat_id, "✅ Analysis complete:\n\n" + azure_text, reply_to_message_id=message_id)

    except Exception as e:
        send_message(chat_id, f"⚠️ Error while processing screenshot: {str(e)}")
        raise

@app.route("/", methods=["GET"])
def index():
    return "Gold Analyzer Bot (Azure Vision) — alive"

# webhook endpoint path uses the token to avoid random posts
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"ok": False, "error": "invalid json"}), 400

    # Basic update handling
    # Only handle messages with photos for now and text commands /start /analyze
    if "message" in data:
        msg = data["message"]
        chat_id = msg["chat"]["id"]
        message_id = msg.get("message_id")

        # /start command
        if "text" in msg:
            text = msg["text"].strip()
            if text.startswith("/start"):
                user_name = msg["from"].get("first_name", "") or msg["from"].get("username","")
                send_message(chat_id, f"Welcome {user_name}! Upload your chart screenshot and then send /analyze to process (if active).")
                return jsonify({"ok": True})

            if text.startswith("/analyze"):
                send_message(chat_id, "If you already uploaded a screenshot in this chat, please reply to that message with /analyze or upload a screenshot now.")
                return jsonify({"ok": True})

            # other text -> small help
            send_message(chat_id, "Send a chart image (photo). I'll save it and analyze automatically.")
            return jsonify({"ok": True})

        # If photo present
        if "photo" in msg:
            photos = msg["photo"]
            # pick the largest (last one)
            file_id = photos[-1]["file_id"]
            try:
                file_path = get_file_path(file_id)
                img_bytes = download_telegram_file(file_path)
            except Exception as e:
                send_message(chat_id, f"⚠️ Could not download image: {str(e)}", reply_to_message_id=message_id)
                return jsonify({"ok": False})

            # Save locally (optional) - unique filename
            ts = int(time.time())
            filename = f"images/{chat_id}_{ts}.jpg"
            os.makedirs("images", exist_ok=True)
            with open(filename, "wb") as f:
                f.write(img_bytes)

            # acknowledge quickly
            send_message(chat_id, "📸 Screenshot saved! Processing will continue in background — you will receive results here shortly.", reply_to_message_id=message_id)

            # process in background
            t = threading.Thread(target=process_screenshot_async, args=(chat_id, message_id, img_bytes), daemon=True)
            t.start()
            return jsonify({"ok": True})

    # For other update types or no-ops
    return jsonify({"ok": True})

if __name__ == "__main__":
    # If running locally for debug: run on port 5000
    port = int(os.environ.get("PORT", 5000))
    print("Starting Flask server on port", port)
    print("Webhook path: /" + TELEGRAM_TOKEN)
    app.run(host="0.0.0.0", port=port)
