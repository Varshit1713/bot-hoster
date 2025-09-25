# This/main.py
import os
import asyncio
import discord
from discord.ext import commands, tasks
from aiohttp import web

# -----------------------------
# Bot Initialization
# -----------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!m", intents=intents)
bot.remove_command("help")  # optional: use custom help

# -----------------------------
# Load all cogs from Is/ folder
# -----------------------------
cogs_dir = os.path.join(os.path.dirname(__file__), "Is")
for filename in os.listdir(cogs_dir):
    if filename.endswith(".py"):
        bot.load_extension(f"Is.{filename[:-3]}")  # strip .py

# -----------------------------
# Timetrack loop placeholder (actual implementation in timetrack.py)
# -----------------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    # Placeholder loop. Real logic lives in timetrack.py cog
    pass

# -----------------------------
# Start Render webserver (Example/webserver.py)
# -----------------------------
from Example import webserver

async def start_render_webserver():
    await webserver.start_webserver()
    print("✅ Render webserver started")

# -----------------------------
# Async main function
# -----------------------------
async def main():
    # Start webserver
    asyncio.create_task(start_render_webserver())

    # Start timetrack loop
    if not timetrack_loop.is_running():
        timetrack_loop.start()

    # Run Discord bot
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ DISCORD_TOKEN not found.")
        return

    try:
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        await bot.close()
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        await bot.close()

# -----------------------------
# Entry point
# -----------------------------
if __name__ == "__main__":
    asyncio.run(main())
