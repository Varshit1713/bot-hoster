# main.py
import discord
from discord.ext import commands
import os
import asyncio

# -----------------------------
# Bot setup
# -----------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# Load all cogs
# -----------------------------
cogs = [
    "Is.admin",
    "Is.msmute",
    "Is.timetrack",
    "Is.cache",
    "Is.ping",
    "Is.dm_control"
]

for cog in cogs:
    try:
        bot.load_extension(cog)
        print(f"✅ Loaded cog {cog}")
    except Exception as e:
        print(f"❌ Failed to load {cog}: {e}")

# -----------------------------
# On ready event
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")

# -----------------------------
# Keep-alive webserver
# -----------------------------
from Example import webserver

async def start_webserver():
    await asyncio.to_thread(webserver.run)

# -----------------------------
# Run bot
# -----------------------------
async def main():
    # Start webserver
    asyncio.create_task(start_webserver())
    # Run bot
    await bot.start(os.environ["DISCORD_TOKEN"])

# Entry point
if __name__ == "__main__":
    asyncio.run(main())
