import os
import threading
import datetime
import json
import sys
from flask import Flask
import discord
from discord.ext import commands

# ---------------- CONFIG ----------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    sys.exit(1)

# ---------------- FLASK KEEP-ALIVE ----------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ---------------- DISCORD BOT ----------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- EVENTS ----------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"⚠️ Slash sync failed: {e}")

# ---------------- LOAD COGS ----------------
initial_extensions = ["cogs.timetrack", "cogs.rmute"]

for ext in initial_extensions:
    try:
        bot.load_extension(ext)
        print(f"✅ Loaded {ext}")
    except Exception as e:
        print(f"❌ Failed to load {ext}: {e}")

# ---------------- RUN BOT ----------------
bot.run(TOKEN)
