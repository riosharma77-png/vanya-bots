# -*- coding: utf-8 -*-
"""
Vanya Instagram DM bot — Phase 2 build (started 21 Oct 2026).

Stack: Flask webhook server for the "Vanya Assistant" Meta app
(API setup with Instagram login). Receives Instagram message events,
replies as Vanya using the locked Life File persona, with the
Human Behaviour Layer the user insisted on (no instant robot replies).

Secrets come ONLY from environment variables (set via secure route):
  IG_ACCESS_TOKEN   - token generated in the app dashboard (Step 2)
  IG_VERIFY_TOKEN   - webhook verify token we choose in dashboard Step 3
  LLM_API_KEY       - brain provider key
  LLM_BASE_URL      - OpenAI-compatible endpoint (default: Meta Model API)
  LLM_MODEL         - model name on that endpoint
  TELEGRAM_LINK     - user's channel link for the funnel (from the user)
"""
import json
import os
import random
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, abort

from vanya_persona import SYSTEM_PROMPT, FUNNEL, routine_now

IST = ZoneInfo("Asia/Kolkata")
GRAPH = "https://graph.instagram.com/v21.0"
STORE_PATH = os.path.join(os.path.dirname(__file__), "user_memory.json")

app = Flask(__name__)

# ---------------------------------------------------------------- memory
def load_memory():
    if os.path.exists(STORE_PATH):
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_memory(mem):
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=1)

# ------------------------------------------------------- human behaviour
def is_sleep_time(now=None):
    now = now or datetime.now(IST)
    return now.hour >= 0 and now.hour < 8  # Vanya sleeps by ~12, up by 8

def reply_delay_seconds(warm_count):
    """Variable, human-like delay. Warmer chats reply a bit faster."""
    if warm_count >= 3:
        return random.uniform(60, 180)      # ~1-3 min in a warm chat
    return random.uniform(120, 420)         # colder chats: slower

def split_messages(text):
    """Split a reply into 1-3 short texting-style messages."""
    parts = [p.strip() for p in text.replace("\n", " ").split("  ") if p.strip()]
    if len(parts) <= 1 and len(text) > 90:
        mid = text.rfind(" ", 40, 90)
        if mid > 0:
            parts = [text[:mid].strip(), text[mid:].strip()]
    return parts[:3]

# ---------------------------------------------------------------- brain
def generate_reply(user_id, incoming_text):
    mem = load_memory()
    user = mem.setdefault(user_id, {"history": [], "warm": 0, "telegram_offered": False})
    user["history"].append({"role": "user", "content": incoming_text})
    user["history"] = user["history"][-20:]

    now = datetime.now(IST)
    context = (
        f"\n\n[Right now in Delhi it is {now.strftime('%I:%M %p')}. "
        f"At this time you are usually: {routine_now(now.hour)}.]"
    )
    payload = {
        "model": os.environ.get("LLM_MODEL", ""),
        "messages": [{"role": "system", "content": SYSTEM_PROMPT + context}] + user["history"],
        "temperature": 0.85,
        "max_tokens": 220,
    }
    resp = requests.post(
        os.environ.get("LLM_BASE_URL", "https://api.meta.ai/v1") + "/chat/completions",
        headers={"Authorization": f"Bearer {os.environ.get('LLM_API_KEY', '')}"},
        json=payload, timeout=45,
    )
    reply = resp.json()["choices"][0]["message"]["content"].strip()

    user["warm"] += 1
    offer_at = FUNNEL["trigger_after_warm_messages"]
    telegram_link = os.environ.get("TELEGRAM_LINK", "")
    if (telegram_link and not user["telegram_offered"]
            and offer_at[0] <= user["warm"] <= offer_at[1] + 2):
        reply += "\n\n" + FUNNEL["offer_text"].format(telegram_link=telegram_link)
        user["telegram_offered"] = True

    user["history"].append({"role": "assistant", "content": reply})
    save_memory(mem)
    return reply

# ------------------------------------------------------------- instagram
def ig_send(recipient_id, text):
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    requests.post(
        f"{GRAPH}/me/messages",
        params={"access_token": token},
        json={"recipient": {"id": recipient_id}, "message": {"text": text}},
        timeout=30,
    )

def ig_typing(recipient_id):
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    requests.post(
        f"{GRAPH}/me/messages",
        params={"access_token": token},
        json={"recipient": {"id": recipient_id}, "sender_action": "typing_on"},
        timeout=15,
    )

def handle_message(sender_id, text):
    if is_sleep_time():
        time.sleep(random.uniform(600, 1800))  # she is asleep; replies in the morning batch
    mem = load_memory()
    warm = mem.get(sender_id, {}).get("warm", 0)
    time.sleep(reply_delay_seconds(warm))
    ig_typing(sender_id)
    time.sleep(random.uniform(2, 5))            # typing pause
    reply = generate_reply(sender_id, text)
    for part in split_messages(reply):
        ig_send(sender_id, part)
        time.sleep(random.uniform(1.5, 4))      # gap between split messages

# --------------------------------------------------------------- webhook
@app.get("/webhook")
def verify():
    if (request.args.get("hub.mode") == "subscribe"
            and request.args.get("hub.verify_token") == os.environ.get("IG_VERIFY_TOKEN")):
        return request.args.get("hub.challenge", ""), 200
    abort(403)

@app.post("/webhook")
def receive():
    data = request.get_json(force=True, silent=True) or {}
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            msg = event.get("message", {})
            sender = event.get("sender", {}).get("id")
            if sender and msg.get("text") and not msg.get("is_echo"):
                threading.Thread(target=handle_message,
                                 args=(sender, msg["text"]), daemon=True).start()
    return "OK", 200

@app.get("/health")
def health():
    return {"ok": True, "bot": "vanya"}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
