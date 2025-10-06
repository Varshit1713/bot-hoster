import os
import time
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# Environment variables
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "1410458084874260592")
AUTH_SECRET = os.getenv("AUTH_SECRET")  # Optional security
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "CommandLoggerBot")

DISCORD_API_BASE = "https://discord.com/api/v10"

def auth_ok(req):
    if not AUTH_SECRET:
        return True
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
        return token == AUTH_SECRET
    return False

def make_embed(payload):
    command = payload.get("command", "<unknown>")
    username = payload.get("username") or payload.get("user", "Unknown user")
    user_id = payload.get("user_id", payload.get("userId", "unknown"))
    description = payload.get("description", "No description provided.")
    bot_name = payload.get("bot_name", payload.get("bot", "Unknown Bot"))
    extra = payload.get("extra", {})

    fields = [
        {"name": "Command / Trigger", "value": f"`{command}`", "inline": True},
        {"name": "Who triggered it", "value": f"{username} (`{user_id}`)", "inline": True},
        {"name": "Bot used", "value": f"{bot_name}", "inline": True},
        {"name": "What it did", "value": description, "inline": False},
    ]

    i = 0
    for k, v in (extra.items() if isinstance(extra, dict) else []):
        if i >= 6: break
        val = str(v)
        if len(val) > 1024:
            val = val[:1000] + "…"
        fields.append({"name": str(k), "value": val, "inline": False})
        i += 1

    embed = {
        "title": "Command Triggered",
        "description": "A command or trigger was used in the server.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "color": 0x2F3136,
        "author": {"name": bot_name},
        "fields": fields,
        "footer": {"text": f"{BOT_DISPLAY_NAME} • logged"},
    }
    return embed

def post_with_bot_channel(channel_id, embed):
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"embeds": [embed]}
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 1)
        time.sleep(retry_after / 1000 if retry_after > 1000 else retry_after)
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp

@app.route("/")
def index():
    return "Bot command logger is running."

@app.route("/notify", methods=["POST"])
def notify():
    if not auth_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    if not request.is_json:
        return jsonify({"error": "expected JSON body"}), 400

    payload = request.get_json()
    if "command" not in payload:
        return jsonify({"error": "missing 'command' field"}), 400

    embed = make_embed(payload)

    try:
        r = post_with_bot_channel(LOG_CHANNEL_ID, embed)
    except requests.HTTPError as e:
        return jsonify({"error": "failed to send to Discord", "details": getattr(e, "response").text if getattr(e, "response", None) else str(e)}), 500
    except Exception as e:
        return jsonify({"error": "unexpected error", "details": str(e)}), 500

    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
