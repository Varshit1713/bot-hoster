import os
import threading
import asyncio
from flask import Flask
import discord
from discord.ext import commands

# ----------------- Flask web server -----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Discord bot is running!"

def run_flask():
    # Bind to Render’s port
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ----------------- Discord bot -----------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

async def start_bot():
    # Async load your cogs
    await bot.load_extension("timetrack")
    await bot.load_extension("rmute")
    # Start the bot
    await bot.start(os.getenv("DISCORD_TOKEN"))

# ----------------- Run both -----------------
if __name__ == "__main__":
    # Start Flask in a separate thread
    threading.Thread(target=run_flask).start()
    # Run Discord bot in asyncio event loop
    asyncio.run(start_bot())
